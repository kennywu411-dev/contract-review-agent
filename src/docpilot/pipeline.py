"""完整的一次问答：agent 作答 → 逐句校验 → 降级。校验结果并入同一份 trace。"""

from __future__ import annotations

from dataclasses import asdict

from docpilot.agent import AgentResult, run_agent
from docpilot.verify import HaikuVerifier, VerifiedAnswer, verify_result


def answer_question(
    question: str, school: str, llm, toolbox, verifier: HaikuVerifier, on_step=None
) -> tuple[AgentResult, VerifiedAnswer]:
    raw = run_agent(question, school, llm, toolbox, on_step=on_step)
    ver = verify_result(raw, verifier)
    raw.trace["verify"] = asdict(ver)
    raw.trace["cost_usd"] = raw.cost_usd + ver.cost_usd
    raw.trace["latency_ms"] = raw.latency_ms + ver.latency_ms
    return raw, ver
