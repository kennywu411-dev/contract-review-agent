"""合同审查会话的多轮记忆与上下文压缩（ADR 011，沿用 ADR 005 的"最近几轮原文 + 更早摘要"）。

三种方式（评测对比用，产品默认 compress）：
- full：全部轮次原文；
- window：只留最近 keep_last 轮原文，更早的直接丢掉；
- compress：历史超过 budget_chars 时，把最近 keep_last 轮以外的轮次交给 Haiku 压成结构化摘要
  （我方立场、意见台账、用户决定、已引用依据）。摘要生成后用规则核对：被压缩轮次里的每条意见
  （第几轮第几条、条款号）都必须在台账里，缺了按规则补录；摘要模型出错就整份用规则生成台账。

每轮都带绝对轮次号（第0轮是首次审查），用户说"最开始你列的第1条"时按"第0轮第1条"对应。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Literal

from contract_review.contract_evalset import RISK_TYPES
from contract_review.llm import cost_usd

Mode = Literal["full", "window", "compress"]

SUMMARY_SCHEMA = {
    "type": "object",
    "properties": {
        "our_side": {"type": "string"},
        "findings": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "turn": {"type": "integer"},
                    "no": {"type": "integer"},
                    "clause": {"type": "string"},
                    "risk_type": {"type": "string"},
                    "status": {"type": "string"},
                },
                "required": ["turn", "no", "clause", "risk_type", "status"],
                "additionalProperties": False,
            },
        },
        "decisions": {"type": "array", "items": {"type": "string"}},
        "cited": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["our_side", "findings", "decisions", "cited"],
    "additionalProperties": False,
}


@dataclass
class _Turn:
    no: int
    text: str
    findings: list[tuple[int, int, str, str]] = field(
        default_factory=list
    )  # (轮, 序号, 条款, 类型)
    our_side: str = ""


def _finding_line(i: int, f: dict) -> str:
    clause = f.get("clause") or "缺失条款"
    rtype = RISK_TYPES.get(f.get("risk_type", ""), f.get("risk_type", ""))
    level, reason, sugg = f.get("level", ""), f.get("reason", ""), f.get("suggestion", "")
    return f"{i}. {clause}｜{rtype}｜{level}｜理由：{reason}｜建议：{sugg}"


class ReviewMemory:
    def __init__(
        self,
        mode: Mode = "compress",
        keep_last: int = 2,
        budget_chars: int = 3000,
        summarizer=None,
    ) -> None:
        self.mode, self.keep_last, self.budget = mode, keep_last, budget_chars
        self.summarizer = summarizer
        self.turns: list[_Turn] = []
        self.summary: dict | None = None
        self.summarized_upto = -1  # 已压进摘要的最后一个轮次号
        self.required: list[tuple[int, int, str, str]] = []  # 摘要台账里必须有的意见
        self.compressions = self.compress_errors = self.backfilled = 0
        self.cost_usd = 0.0
        self.last_render_chars = 0
        self._next = 0

    # ---------------- 记录 ----------------
    def add_review(self, result: dict, question: str = "") -> None:
        k = self._next
        self._next += 1
        head = "【第0轮·首次审查】" if k == 0 and not question else f"【第{k}轮】"
        lines = [head + (f"用户：{question}" if question else "")]
        if result.get("kind") == "clarify":
            lines.append(f"助手反问：{result.get('clarification', '')}")
            self.turns.append(_Turn(k, "\n".join(lines)))
            return
        side = result.get("our_side", "")
        again = "重新审查，" if question else ""
        lines.append(f"助手：{again}我方：{side}方。总体结论：{result.get('summary', '')}")
        findings = []
        for i, f in enumerate(result.get("findings", []), 1):
            lines.append(_finding_line(i, f))
            findings.append((k, i, f.get("clause") or "缺失条款", f.get("risk_type", "")))
        self.turns.append(_Turn(k, "\n".join(lines), findings, side))

    def add_followup(self, question: str, result: dict) -> None:
        if result.get("kind") in ("review", "clarify"):
            self.add_review(result, question)
            return
        k = self._next
        self._next += 1
        clause = result.get("answer_clause") or ""
        tag = f"（{clause}）" if clause else ""
        text = f"【第{k}轮】用户：{question}\n助手{tag}：{result.get('answer', '')}"
        self.turns.append(_Turn(k, text))

    # ---------------- 渲染 ----------------
    def _summary_text(self) -> str:
        s = self.summary or {}
        lines = [
            f"较早对话摘要（第0–{self.summarized_upto}轮）：",
            f"我方：{s.get('our_side', '')}方",
        ]
        lines.append("意见台账：")
        for f in s.get("findings", []):
            rtype = RISK_TYPES.get(f["risk_type"], f["risk_type"])
            lines.append(f"- 第{f['turn']}轮第{f['no']}条：{f['clause']} {rtype}（{f['status']}）")
        if s.get("decisions"):
            lines.append("用户决定：" + "；".join(s["decisions"]))
        if s.get("cited"):
            lines.append("已引用依据：" + "；".join(s["cited"]))
        return "\n".join(lines)

    def _render(self, turns: list[_Turn]) -> str:
        if not turns and not self.summary:
            return ""
        parts = ["<对话历史>"]
        if self.summary:
            parts.append(self._summary_text())
        parts += [t.text for t in turns]
        parts.append("</对话历史>")
        return "\n".join(parts)

    def render(self) -> str:
        if self.mode == "window":
            out = self._render(self.turns[-self.keep_last :])
        else:
            out = self._render(self.turns)
            if (
                self.mode == "compress"
                and len(out) > self.budget
                and len(self.turns) > self.keep_last
            ):
                self._compress()
                out = self._render(self.turns)
        self.last_render_chars = len(out)
        return out

    # ---------------- 压缩 ----------------
    def _compress(self) -> None:
        from contract_review.prompts import REVIEW_SUMMARY_PROMPT

        old, self.turns = self.turns[: -self.keep_last], self.turns[-self.keep_last :]
        for t in old:
            self.required += t.findings
        side = next((t.our_side for t in reversed(old) if t.our_side), "")
        self.summarized_upto = old[-1].no
        text = (
            f"已有摘要：{json.dumps(self.summary, ensure_ascii=False)}\n" if self.summary else ""
        ) + "\n".join(t.text for t in old)
        try:
            resp = self.summarizer.create(
                max_tokens=1500,
                messages=[{"role": "user", "content": f"{REVIEW_SUMMARY_PROMPT}\n\n{text}"}],
                output_config={
                    "effort": "low",
                    "format": {"type": "json_schema", "schema": SUMMARY_SCHEMA},
                },
            )
            self.cost_usd += cost_usd(self.summarizer.model, resp.usage)
            body = next(b.text for b in resp.content if b.type == "text")
            summary = json.loads(body)
            self.compressions += 1
        except Exception:  # 摘要模型出错：整份用规则生成，不能丢掉早期意见
            self.compress_errors += 1
            prev = self.summary or {}
            summary = {
                "our_side": side or prev.get("our_side", ""),
                "findings": [],
                "decisions": prev.get("decisions", []),
                "cited": prev.get("cited", []),
            }
        self.summary = self._backfill(summary, side)

    def _backfill(self, summary: dict, side: str) -> dict:
        """规则核对：每条被压缩过的意见都要在台账里（按 轮次+序号 对），缺的补录。"""
        have = {(f["turn"], f["no"]) for f in summary.get("findings", [])}
        for turn, no, clause, rtype in self.required:
            if (turn, no) not in have:
                summary.setdefault("findings", []).append(
                    {
                        "turn": turn,
                        "no": no,
                        "clause": clause,
                        "risk_type": rtype,
                        "status": "已报（补录）",
                    }
                )
                if self.compressions:  # 模型摘要漏了才算补录；规则兜底时全是补录，不计
                    self.backfilled += 1
        summary["findings"].sort(key=lambda f: (f["turn"], f["no"]))
        if not summary.get("our_side"):
            summary["our_side"] = side
        return summary
