"""合同审查 agent：站在我方（租户公司）立场逐条找出不利条款，给出类型、等级、建议和依据。

复用问答 agent 的工具循环（agent.tool_loop）、检索与查表工具；终止工具换成 submit_review。
材料不全（看不出我方是甲方还是乙方）时反问，对应问答场景的"反问"；拿不准的意见标"转人工复核"，
对应问答场景的"拒答"（ADR 007）。
"""

from __future__ import annotations

import re
import time
import uuid
from dataclasses import asdict, dataclass, field

from contract_review.agent import _normalize_citations, tool_loop
from contract_review.config import TENANTS
from contract_review.contract_evalset import RISK_TYPES
from contract_review.llm import LLM
from contract_review.prompts import review_system_prompt
from contract_review.tools import TOOLS, ToolBox, _tool

MAX_STEPS = 8
MAX_TOKENS = 8000

_BASE = {t["name"]: t for t in TOOLS}
REVIEW_TOOLS = [
    _tool(
        "search_docs",
        "检索法律法规（民法典、合同编通则解释、民事诉讼法）、官方合同示范文本和本公司合同审查规范。"
        "每个疑似风险单独检索，可以在一次回复里同时发起多个检索。"
        "返回带编号 [C1] [C2]… 的原文片段。",
        _BASE["search_docs"]["input_schema"]["properties"],
    ),
    _tool(
        "run_sql",
        "在供应商台账（suppliers、purchase_history）上执行单条只读 SELECT（经 DataPilot MCP），"
        "用于核对单价是否明显高于历史成交均价、供应商是否在黑名单、资质是否在有效期内。"
        "结果带编号 [T1]…，提交意见时引用。",
        {"sql": {"type": "string"}},
    ),
    _tool(
        "ask_clarification",
        "缺少审查必需的信息（最常见：无法确定我方是甲方还是乙方）时反问用户。调用后本轮结束。",
        _BASE["ask_clarification"]["input_schema"]["properties"],
    ),
    _tool(
        "submit_review",
        "提交审查结果，调用后本轮结束。findings 只列对我方不利的条款，每条都要有依据编号。",
        {
            "our_side": {"type": "string", "enum": ["甲", "乙"]},
            "summary": {"type": "string", "description": "一两句话的总体审查结论"},
            "findings": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "clause": {
                            "type": "string",
                            "description": "条款号，如“第四条”；条款缺失写“缺失”",
                        },
                        "quote": {"type": "string", "description": "原样摘录的问题句子"},
                        "risk_type": {"type": "string", "enum": [*RISK_TYPES, "other"]},
                        "level": {"type": "string", "enum": ["高", "中", "低"]},
                        "reason": {"type": "string"},
                        "suggestion": {"type": "string", "description": "可直接替换的修改后条款"},
                        "citations": {"type": "array", "items": {"type": "string"}},
                        "needs_human_review": {"type": "boolean"},
                    },
                    "required": [
                        "clause",
                        "quote",
                        "risk_type",
                        "level",
                        "reason",
                        "suggestion",
                        "citations",
                        "needs_human_review",
                    ],
                    "additionalProperties": False,
                },
            },
        },
    ),
]
REVIEW_TERMINAL = {"ask_clarification", "submit_review"}
# 追问轮多一个终止工具：回答针对某条意见/条款的问题。首轮不给，避免模型跳过审查直接作答
FOLLOWUP_TOOLS = [
    *REVIEW_TOOLS,
    _tool(
        "answer_followup",
        "回答用户针对上一轮某条意见或某个条款的追问，调用后本轮结束。",
        {
            "clause": {"type": "string", "description": "追问对应的合同条款号，如“第四条”"},
            "answer": {"type": "string"},
            "citations": {"type": "array", "items": {"type": "string"}},
            "needs_human_review": {"type": "boolean"},
        },
    ),
]
FOLLOWUP_TERMINAL = REVIEW_TERMINAL | {"answer_followup"}

_CN = "零一二三四五六七八九"
_CLAUSE = re.compile(r"第\s*([0-9]+|[零一二三四五六七八九十百]+)\s*条")
_SPACES = re.compile(r"\s+")


def _cn_num(n: int) -> str:
    if n < 10:
        return _CN[n]
    if n < 20:
        return "十" + (_CN[n % 10] if n % 10 else "")
    if n < 100:
        return _CN[n // 10] + "十" + (_CN[n % 10] if n % 10 else "")
    return str(n)


def normalize_clause(raw: str) -> str | None:
    """ "第2条" / "第十二条第二款" → "第二条" / "第十二条"；"缺失"、空串 → None。"""
    m = _CLAUSE.search(raw or "")
    if not m:
        return None
    num = m.group(1)
    return f"第{_cn_num(int(num)) if num.isdigit() else num}条"


def _defang(text: str) -> str:
    """用户文本里的 <合同> / </合同> 换成全角书名号，不能提前闭合分隔符。"""
    return (text or "").replace("</合同>", "〈/合同〉").replace("<合同>", "〈合同〉")


def quote_in_contract(quote: str, contract: str) -> bool:
    """摘录是否真在合同里（忽略空白；"……"省略的几段分别查）。空摘录（缺失型）算存在。"""
    body = _SPACES.sub("", contract)
    parts = [p for p in re.split(r"[…]+|\.{3,}", _SPACES.sub("", quote or "")) if p]
    return all(p in body for p in parts)


@dataclass
class ReviewResult:
    tenant: str
    kind: str  # review / clarify / fallback
    our_side: str = ""
    summary: str = ""
    findings: list[dict] = field(default_factory=list)
    clarification: str = ""
    answer: str = ""  # 追问轮的回答（kind=followup）
    answer_clause: str | None = None
    answer_citations: list[str] = field(default_factory=list)
    answer_needs_human: bool = False
    invalid_citations: list[str] = field(default_factory=list)
    citations: dict = field(default_factory=dict)
    steps: int = 0
    cost_usd: float = 0.0
    latency_ms: float = 0.0
    trace: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)


def review_contract(
    contract: str,
    tenant: str,
    llm: LLM,
    toolbox: ToolBox,
    max_steps: int = MAX_STEPS,
    on_step=None,
    cache: str = "prefix",
    history: str = "",
    contract_id: str = "",
    question: str = "",
) -> ReviewResult:
    """question 为空是首轮审查；否则是追问轮，history 放上一轮的审查意见（history_from_review）。"""
    t0 = time.perf_counter()
    company = TENANTS[tenant].removesuffix("（构造）")
    contract, question = _defang(contract), _defang(question)
    if question:
        content = (
            f"{history}\n\n正在审查的合同（我方：{company}）：\n<合同>\n{contract.strip()}\n</合同>"
            f"\n\n用户追问：{question}"
        )
        tools, terminal_tools = FOLLOWUP_TOOLS, FOLLOWUP_TERMINAL
    else:
        content = (
            f"请从{company}（我方）的角度审查下面这份合同。\n<合同>\n{contract.strip()}\n</合同>"
        )
        if history:
            content = f"{history}\n\n{content}"
        tools, terminal_tools = REVIEW_TOOLS, REVIEW_TERMINAL
    result = ReviewResult(tenant, "fallback")
    trace = {
        "trace_id": uuid.uuid4().hex[:12],
        "task": "review",
        "model": llm.model,
        "contract_id": contract_id,
        "question": question or f"审查合同 {contract_id}".strip(),
        "tenant": tenant,
        "max_steps": max_steps,
        "cache": cache,
        "history": history,
        "steps": [],
    }
    terminal, result.cost_usd, result.steps = tool_loop(
        llm=llm,
        system_text=review_system_prompt(tenant),
        tools=tools,
        terminal_tools=terminal_tools,
        user_content=content,
        toolbox=toolbox,
        trace=trace,
        max_steps=max_steps,
        max_tokens=MAX_TOKENS,
        on_step=on_step,
        cache=cache,
    )
    if terminal is not None:
        _finish(result, terminal, toolbox, contract)
    result.citations = toolbox.citations
    result.latency_ms = (time.perf_counter() - t0) * 1000
    trace["result"] = {
        "kind": result.kind,
        "our_side": result.our_side,
        "summary": result.summary,
        "findings": result.findings,
        "clarification": result.clarification,
        "answer": result.answer,
        "answer_clause": result.answer_clause,
        "answer_citations": result.answer_citations,
        "answer_needs_human": result.answer_needs_human,
        "invalid_citations": result.invalid_citations,
    }
    trace["citations"] = toolbox.citations
    trace["cost_usd"] = result.cost_usd
    trace["latency_ms"] = result.latency_ms
    trace["sql_backend"] = getattr(toolbox.sql, "name", None)
    result.trace = trace
    return result


def _finish(result: ReviewResult, call, toolbox: ToolBox, contract: str) -> None:
    args = call.input
    if call.name == "ask_clarification":
        result.kind = "clarify"
        result.clarification = args.get("question", "")
        return
    if call.name == "answer_followup":
        result.kind = "followup"
        result.answer = args.get("answer", "")
        result.answer_clause = normalize_clause(args.get("clause", ""))
        result.answer_citations = _normalize_citations(args.get("citations", []))
        result.answer_needs_human = bool(args.get("needs_human_review", False))
        result.invalid_citations = [
            c for c in result.answer_citations if c not in toolbox.citations
        ]
        return
    result.kind = "review"
    result.our_side = args.get("our_side", "")
    result.summary = args.get("summary", "")
    for f in args.get("findings", []):
        result.findings.append(
            {
                "clause": normalize_clause(f.get("clause", "")),
                "clause_raw": f.get("clause", ""),
                "quote": f.get("quote", ""),
                # 缺失型意见没有可摘录的原文（模型常写"无"），不做摘录核对
                "quote_found": normalize_clause(f.get("clause", "")) is None
                or quote_in_contract(f.get("quote", ""), contract),
                "risk_type": f.get("risk_type", "other"),
                "level": f.get("level", "中"),
                "reason": f.get("reason", ""),
                "suggestion": f.get("suggestion", ""),
                "citations": _normalize_citations(f.get("citations", [])),
                "needs_human_review": bool(f.get("needs_human_review", False)),
            }
        )
    cited = [c for f in result.findings for c in f["citations"]]
    result.invalid_citations = [c for c in dict.fromkeys(cited) if c not in toolbox.citations]


def history_from_review(prev: dict, qa: list[tuple[str, str]] | None = None) -> str:
    """把上一轮审查结果压成结构化历史：我方身份 + 编号的意见列表 + 之后的追问问答。

    意见按提交顺序编号，用户说"你列的第 2 条"时按这个编号对应。引用编号不带过去（每轮重置）。
    """
    lines = ["<上一轮审查>"]
    if prev.get("kind") == "clarify":
        lines.append(f"助手反问：{prev.get('clarification', '')}")
    else:
        lines.append(f"我方：{prev.get('our_side', '')}方。总体结论：{prev.get('summary', '')}")
        for i, f in enumerate(prev.get("findings", []), 1):
            clause = f.get("clause") or "缺失条款"
            lines.append(
                f"{i}. {clause}｜{RISK_TYPES.get(f['risk_type'], f['risk_type'])}｜{f['level']}"
                f"｜理由：{f['reason']}｜建议：{f['suggestion']}"
            )
    for q, a in qa or []:
        lines += [f"用户：{q}", f"助手：{a}"]
    lines.append("</上一轮审查>")
    return "\n".join(lines)
