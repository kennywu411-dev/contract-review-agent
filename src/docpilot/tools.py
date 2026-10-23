"""Agent 的工具：检索文档、查表（DataPilot）、反问、作答。

安全边界：学校由会话注入（ToolBox.school），工具参数里没有 school，模型改不了检索范围；
查表结果里带 school 列的行只保留本校和国家层面。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field

from docpilot.config import SCHOOLS

SEARCH_K = 5
SQL_PREVIEW_ROWS = 30


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
        "学制、校历等数字。结果带编号 [T1]…，作答时引用。WHERE 里要带 school 条件。",
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
        "给出最终回答，调用后本轮结束。每句话都要列出支撑它的编号（C 或 T）；没有出处支撑的话"
        "不要写。文件里查不到答案时 refuse=true，并在 refuse_reason 里说明建议咨询的部门。",
        {
            "refuse": {"type": "boolean"},
            "refuse_reason": {"type": "string"},
            "sentences": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "text": {"type": "string"},
                        "citations": {"type": "array", "items": {"type": "string"}},
                    },
                    "required": ["text", "citations"],
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
    def __init__(self, school: str, retriever, sql) -> None:
        self.school = school
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
        res = self.retriever.search(query, self.school, k=SEARCH_K)
        lines: list[str] = []
        if res.school_mismatch:
            lines.append(
                f"注意：问题提到了{SCHOOLS[res.school_mismatch]}，但学生选择的是{SCHOOLS[self.school]}，"
                "检索只覆盖本校和国家层面文件。"
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
                "school": c.school,
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
            scope = "国家层面" if c.school == "national" else SCHOOLS[c.school]
            path = " > ".join(c.section_path[1:]) if len(c.section_path) > 1 else ""
            lines.append(f"[{cid}] 《{c.title}》{path}｜{scope}｜{where}\n{c.text}")
        return ToolOutput("\n\n".join(lines), new_citations=new)

    def _run_sql(self, sql: str) -> ToolOutput:
        res = self.sql.run(sql)
        if "error" in res:
            return ToolOutput(
                f"SQL 出错（{res['error']}）：{res.get('message', '')}", is_error=True
            )
        columns, rows = res["columns"], res["rows"]
        if "school" in columns:
            i = columns.index("school")
            rows = [r for r in rows if r[i] in (self.school, "national")]
        self._t += 1
        tid = f"T{self._t}"
        self.citations[tid] = {"kind": "table", "sql": sql, "columns": columns, "rows": rows}
        body = json.dumps({"columns": columns, "rows": rows[:SQL_PREVIEW_ROWS]}, ensure_ascii=False)
        return ToolOutput(f"[{tid}] {len(rows)} 行\n{body}", new_citations=[tid])
