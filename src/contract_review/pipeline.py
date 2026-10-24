"""完整的一次问答：agent 作答 → 逐句校验 → 降级。校验结果并入同一份 trace。"""

from __future__ import annotations

from dataclasses import asdict

from contract_review.agent import AgentResult, run_agent
from contract_review.memory import Turn
from contract_review.verify import HaikuVerifier, VerifiedAnswer, verify_result


def answer_question(
    question: str,
    tenant: str,
    llm,
    toolbox,
    verifier: HaikuVerifier,
    on_step=None,
    history: str = "",
) -> tuple[AgentResult, VerifiedAnswer]:
    raw = run_agent(question, tenant, llm, toolbox, on_step=on_step, history=history)
    ver = verify_result(raw, verifier)
    raw.trace["verify"] = asdict(ver)
    raw.trace["cost_usd"] = raw.cost_usd + ver.cost_usd
    raw.trace["latency_ms"] = raw.latency_ms + ver.latency_ms
    return raw, ver


def turn_from(raw: AgentResult, ver: VerifiedAnswer) -> Turn:
    """把一轮问答压成记忆条目：校验后保留的句子 + 可读出处（编号每轮重置，不能跨轮引用）。"""
    if ver.kind in ("refuse", "clarify"):
        answer = ver.refuse_reason or ver.clarification
    else:
        answer = "".join(s["text"] for s in ver.kept)
    sources: list[str] = []
    for s in ver.kept:
        for cid in s.get("citations", []):
            c = raw.citations.get(cid)
            if not c or c["kind"] != "doc":
                continue
            where = (
                f"第{','.join(map(str, c['pages']))}页" if c["pages"] else "、".join(c["articles"])
            )
            label = f"《{c['title']}》{where}"
            if label not in sources:
                sources.append(label)
    return Turn(question=raw.question, answer=answer, sources=sources, kind=ver.kind)
