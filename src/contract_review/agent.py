"""Agent 主循环：手写工具循环，最多 max_steps 次模型调用，每步写 trace。

不用 SDK 的 tool runner：要逐步记录 token、花费、耗时和工具输入输出（D5 的 trace 回放靠它），
还要在步数用完或模型没调用终止工具时，按 fallback 收尾。
"""

from __future__ import annotations

import re
import time
import uuid
from dataclasses import asdict, dataclass, field

from contract_review.llm import LLM, cost_usd
from contract_review.prompts import system_prompt
from contract_review.tools import TERMINAL_TOOLS, TOOLS, ToolBox

MAX_STEPS = 6
MAX_TOKENS = 4000
EFFORT = "low"


@dataclass
class AgentResult:
    question: str
    tenant: str
    kind: str  # answer / partial / refuse / clarify / fallback
    sentences: list[dict] = field(default_factory=list)
    refuse_reason: str = ""
    unanswered: str = ""
    clarification: str = ""
    invalid_citations: list[str] = field(default_factory=list)
    citations: dict = field(default_factory=dict)
    steps: int = 0
    cost_usd: float = 0.0
    latency_ms: float = 0.0
    trace: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)


def _block_summary(b) -> dict:
    if b.type == "text":
        return {"type": "text", "text": b.text}
    if b.type == "tool_use":
        return {"type": "tool_use", "name": b.name, "input": b.input}
    # thinking 等：只记类型和长度（原样回传给模型，但 trace 里不存内容）
    return {"type": b.type, "chars": len(getattr(b, "thinking", "") or "")}


def _usage_dict(u) -> dict:
    keys = (
        "input_tokens",
        "output_tokens",
        "cache_read_input_tokens",
        "cache_creation_input_tokens",
    )
    return {k: getattr(u, k, 0) or 0 for k in keys}


def tool_loop(
    *,
    llm: LLM,
    system_text: str,
    tools: list[dict],
    terminal_tools: set[str],
    user_content: str,
    toolbox,
    trace: dict,
    max_steps: int,
    max_tokens: int = MAX_TOKENS,
    on_step=None,
    cache: str = "prefix",
):
    """通用工具循环：调模型 → 执行工具 → 回填结果，直到模型调用终止工具或步数用完。

    返回 (终止工具调用块或 None, 总花费, 步数)；
    每步记录追加到 trace["steps"]，结束原因写 trace["end"]。
    cache="prefix"：system 和整个消息前缀都写缓存（默认）；cache="system"：只缓存 system。
    """
    system = [{"type": "text", "text": system_text, "cache_control": {"type": "ephemeral"}}]
    messages: list[dict] = [{"role": "user", "content": user_content}]
    cost, steps, terminal = 0.0, 0, None
    trace["end"] = "max_steps"
    for step in range(1, max_steps + 1):
        ts = time.perf_counter()
        extra = {"cache_control": {"type": "ephemeral"}} if cache == "prefix" else {}
        resp = llm.create(
            max_tokens=max_tokens,
            system=system,
            tools=tools,
            messages=messages,
            output_config={"effort": EFFORT},
            **extra,
        )
        step_cost = cost_usd(llm.model, resp.usage)
        record = {
            "step": step,
            "stop_reason": resp.stop_reason,
            "usage": _usage_dict(resp.usage),
            "cost_usd": step_cost,
            "llm_ms": (time.perf_counter() - ts) * 1000,
            "assistant": [_block_summary(b) for b in resp.content],
            "tool_calls": [],
        }
        trace["steps"].append(record)
        cost += step_cost
        steps = step
        uses = [b for b in resp.content if b.type == "tool_use"]
        if not uses:
            # end_turn 却没调终止工具 / 输出被截断 / 模型拒绝：分开记录，回放时好诊断
            trace["end"] = (
                "no_terminal_tool" if resp.stop_reason == "end_turn" else resp.stop_reason
            )
            details = getattr(resp, "stop_details", None)
            if details is not None:
                trace["stop_details"] = getattr(details, "category", str(details))
            break
        terminal = next((b for b in uses if b.name in terminal_tools), None)
        if terminal is not None:
            record["tool_calls"].append({"name": terminal.name, "input": terminal.input})
            trace["end"] = terminal.name
            if on_step:
                on_step(record)
            break
        messages.append({"role": "assistant", "content": resp.content})
        results = []
        for b in uses:
            tt = time.perf_counter()
            out = toolbox.execute(b.name, b.input)
            record["tool_calls"].append(
                {
                    "name": b.name,
                    "input": b.input,
                    "output": out.content,
                    "is_error": out.is_error,
                    "citations": out.new_citations,
                    "ms": (time.perf_counter() - tt) * 1000,
                }
            )
            results.append(
                {
                    "type": "tool_result",
                    "tool_use_id": b.id,
                    "content": out.content,
                    "is_error": out.is_error,
                }
            )
        messages.append({"role": "user", "content": results})
        if on_step:
            on_step(record)
    return terminal, cost, steps


def run_agent(
    question: str,
    tenant: str,
    llm: LLM,
    toolbox: ToolBox,
    max_steps: int = MAX_STEPS,
    on_step=None,
    cache: str = "prefix",
    history: str = "",
) -> AgentResult:
    """on_step(record)：每步结束时回调（Web 端用它流式推送步骤）。"""
    t0 = time.perf_counter()
    content = f"{history}\n\n当前问题：{question}" if history else question
    result = AgentResult(question, tenant, "fallback")
    trace = {
        "trace_id": uuid.uuid4().hex[:12],
        "model": llm.model,
        "question": question,
        "tenant": tenant,
        "max_steps": max_steps,
        "cache": cache,
        "history": history,
        "steps": [],
    }
    terminal, result.cost_usd, result.steps = tool_loop(
        llm=llm,
        system_text=system_prompt(tenant),
        tools=TOOLS,
        terminal_tools=TERMINAL_TOOLS,
        user_content=content,
        toolbox=toolbox,
        trace=trace,
        max_steps=max_steps,
        on_step=on_step,
        cache=cache,
    )
    if terminal is not None:
        _finish(result, terminal, toolbox)

    result.citations = toolbox.citations
    result.latency_ms = (time.perf_counter() - t0) * 1000
    trace["result"] = {
        "kind": result.kind,
        "sentences": result.sentences,
        "refuse_reason": result.refuse_reason,
        "unanswered": result.unanswered,
        "clarification": result.clarification,
        "invalid_citations": result.invalid_citations,
    }
    trace["citations"] = toolbox.citations
    trace["cost_usd"] = result.cost_usd
    trace["latency_ms"] = result.latency_ms
    trace["sql_backend"] = getattr(toolbox.sql, "name", None)
    result.trace = trace
    return result


_CITE = re.compile(r"[CT]\d+", re.IGNORECASE)


def _normalize_citations(raw: list[str]) -> list[str]:
    """模型常把编号写成 "[C1]"、"【C1】" 或 "C1、T2"；统一成去重后的 C1/T2。"""
    out: list[str] = []
    for item in raw:
        out += [m.upper() for m in _CITE.findall(str(item))]
    return list(dict.fromkeys(out))


def _finish(result: AgentResult, call, toolbox: ToolBox) -> None:
    args = call.input
    if call.name == "ask_clarification":
        result.kind = "clarify"
        result.clarification = args.get("question", "")
        return
    result.sentences = [
        {
            "kind": s.get("kind", "fact"),
            "text": s.get("text", ""),
            "citations": _normalize_citations(s.get("citations", [])),
        }
        for s in args.get("sentences", [])
    ]
    result.refuse_reason = args.get("refuse_reason", "")
    result.unanswered = args.get("unanswered", "") or ""
    if args.get("refuse"):
        result.kind = "refuse"
    else:
        result.kind = "partial" if result.unanswered.strip() else "answer"
    cited = [c for s in result.sentences for c in s.get("citations", [])]
    result.invalid_citations = [c for c in dict.fromkeys(cited) if c not in toolbox.citations]
