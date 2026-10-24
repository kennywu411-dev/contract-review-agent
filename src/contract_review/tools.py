"""Agent 的工具：检索文档、查表（DataPilot）、反问、作答。

安全边界：租户由会话注入（ToolBox.tenant），工具参数里没有 tenant，模型改不了检索范围；
查表结果里带 tenant 列的行只保留本租户（和 common）。
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

from contract_review.config import TENANTS

SEARCH_K = 5
SQL_PREVIEW_ROWS = 30
# 台账库的表都带 tenant 列。查询前用同名 CTE 把它们遮住（CTE 名优先于同名表），
# 不管模型怎么写 SQL（不选 tenant 列、别名、IN、JOIN），都只能看到本租户的行
SCOPED_TABLES = ("suppliers", "purchase_history")
_WITH = re.compile(r"^\s*with\s+(recursive\s+)?", re.IGNORECASE)
# 带 schema 的限定名（main.suppliers、"main"."x"、[temp].x）直接解析到真实表，
# CTE 遮蔽不生效，一律拒绝
_SCHEMA = re.compile(
    r"""(?:\b(?:main|temp|temporary)\b|["`\[](?:main|temp|temporary)["`\]])\s*\.""", re.I
)


def scope_sql(sql: str, tenant: str) -> str:
    # tenant 会拼进 SQL，只接受固定取值；common 不是客户租户
    if tenant not in TENANTS or tenant == "common":
        raise ValueError(f"未知租户：{tenant}")
    if _SCHEMA.search(sql):
        raise ValueError("不允许带 schema 前缀的表名（如 main.suppliers），请直接写表名")
    ctes = ", ".join(
        f"{t} AS (SELECT * FROM main.{t} WHERE tenant IN ('{tenant}', 'common'))"
        for t in SCOPED_TABLES
    )
    body = sql.strip().rstrip(";")
    m = _WITH.match(body)
    if m:  # 原 SQL 自带 WITH：把遮蔽 CTE 插在最前面，后面的 CTE 能引用它们
        return f"WITH {m.group(1) or ''}{ctes}, {body[m.end() :]}"
    return f"WITH {ctes} {body}"


def _tool(name: str, description: str, props: dict) -> dict:
    return {
        "name": name,
        "description": description,
        "strict": True,
        "input_schema": {
            "type": "object",
            "properties": props,
            "required": list(props),
            "additionalProperties": False,
        },
    }


TOOLS = [
    _tool(
        "search_docs",
        "在本校和国家层面的规章文件里检索条文。一个问题问了几件事时，分别检索。"
        "返回带编号 [C1] [C2]… 的原文片段，作答时引用这些编号。",
        {"query": {"type": "string", "description": "检索用的中文短语，可改写成规章里的说法"}},
    ),
    _tool(
        "run_sql",
        "在 campus 结构化表上执行单条只读 SELECT（经 DataPilot MCP）。用于金额、比例、名额、"
        "学制、校历等数字。结果带编号 [T1]…，作答时引用。WHERE 里要带 tenant 条件。",
        {"sql": {"type": "string"}},
    ),
    _tool(
        "ask_clarification",
        "缺少回答所必需的信息（如专业、年级、学历层次）时，向学生反问。调用后本轮结束。",
        {
            "question": {"type": "string", "description": "给学生的一句话反问"},
            "missing": {"type": "array", "items": {"type": "string"}},
        },
    ),
    _tool(
        "final_answer",
        "给出最终回答，调用后本轮结束。fact 句写规章事实，必须列出支撑它的编号（C 或 T）；"
        "note 句只用于说明边界或建议咨询的部门，不能含数字和具体规定。整题都查不到答案时 "
        "refuse=true 并写 refuse_reason；只有一部分答不了时，在 unanswered 里说明是哪部分。",
        {
            "refuse": {"type": "boolean"},
            "refuse_reason": {"type": "string"},
            "unanswered": {"type": "string", "description": "答不了的部分；全部能答时为空字符串"},
            "sentences": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "kind": {"type": "string", "enum": ["fact", "note"]},
                        "text": {"type": "string"},
                        "citations": {"type": "array", "items": {"type": "string"}},
                    },
                    "required": ["kind", "text", "citations"],
                    "additionalProperties": False,
                },
            },
        },
    ),
]
TERMINAL_TOOLS = {"ask_clarification", "final_answer"}


@dataclass
class ToolOutput:
    content: str
    is_error: bool = False
    new_citations: list[str] = field(default_factory=list)


class ToolBox:
    def __init__(self, tenant: str, retriever, sql) -> None:
        self.tenant = tenant
        self.retriever = retriever
        self.sql = sql
        self.citations: dict[str, dict] = {}
        self._c = self._t = 0

    def execute(self, name: str, args: dict) -> ToolOutput:
        handler = {"search_docs": self._search, "run_sql": self._run_sql}.get(name)
        if handler is None:
            return ToolOutput(f"未知工具：{name}", is_error=True)
        try:
            return handler(**args)
        except Exception as e:  # 工具内部异常交还给模型，不中断会话
            return ToolOutput(f"工具执行出错：{type(e).__name__}: {e}", is_error=True)

    def _search(self, query: str) -> ToolOutput:
        res = self.retriever.search(query, self.tenant, k=SEARCH_K)
        lines: list[str] = []
        if res.tenant_mismatch:
            lines.append(
                f"注意：查询提到了{TENANTS[res.tenant_mismatch]}，但当前租户是{TENANTS[self.tenant]}，"
                "检索只覆盖本公司规范和通用的法律法规、示范文本。"
            )
        if not res.hits:
            lines.append("没有检索到相关条文。")
            return ToolOutput("\n".join(lines))
        new = []
        for h in res.hits:
            c = h.chunk
            self._c += 1
            cid = f"C{self._c}"
            self.citations[cid] = {
                "kind": "doc",
                "chunk_id": c.chunk_id,
                "doc_id": c.doc_id,
                "tenant": c.tenant,
                "title": c.title,
                "section_path": c.section_path,
                "pages": c.pages,
                "articles": c.articles,
                "text": c.text,
                "score": round(h.score, 4),
            }
            new.append(cid)
            where = (
                f"第{','.join(map(str, c.pages))}页" if c.pages else "、".join(c.articles) or "网页"
            )
            scope = "通用" if c.tenant == "common" else TENANTS[c.tenant]
            path = " > ".join(c.section_path[1:]) if len(c.section_path) > 1 else ""
            lines.append(
                f"[{cid}] 《{c.title}》{path}｜{scope}｜{where}\n"
                f'<文档片段 id="{cid}">\n{c.text}\n</文档片段>'
            )
        return ToolOutput("\n\n".join(lines), new_citations=new)

    def _run_sql(self, sql: str) -> ToolOutput:
        res = self.sql.run(scope_sql(sql, self.tenant))
        if "error" in res:
            return ToolOutput(
                f"SQL 出错（{res['error']}）：{res.get('message', '')}", is_error=True
            )
        columns, rows = res["columns"], res["rows"]
        if "tenant" in columns:
            i = columns.index("tenant")
            rows = [r for r in rows if r[i] in (self.tenant, "common")]
        self._t += 1
        tid = f"T{self._t}"
        self.citations[tid] = {"kind": "table", "sql": sql, "columns": columns, "rows": rows}
        body = json.dumps({"columns": columns, "rows": rows[:SQL_PREVIEW_ROWS]}, ensure_ascii=False)
        return ToolOutput(
            f'[{tid}] {len(rows)} 行\n<查询结果 id="{tid}">\n{body}\n</查询结果>',
            new_citations=[tid],
        )
