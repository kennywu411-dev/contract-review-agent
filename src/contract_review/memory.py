"""会话级短期记忆与上下文压缩（ADR 005）。

三层：槽位（学历层次、年级、专业，规则抽取、一直保留）+ 最近几轮原文 + 更早轮次的摘要。
历史估算超过 token 预算时，用 Haiku 把最近 keep_last 轮以外的内容压成摘要；
压缩出错就只保留最近几轮。
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Literal

from contract_review.llm import cost_usd
from contract_review.prompts import SUMMARY_PROMPT

Mode = Literal["none", "full", "compress"]
SUMMARY_SCHEMA = {
    "type": "object",
    "properties": {"summary": {"type": "string"}},
    "required": ["summary"],
    "additionalProperties": False,
}

# 只从第一人称表述里抽"学生身份"：问到的对象（"研究生能拿国奖吗"）不是身份
_ME = r"(?:我|本人)[^，。？！,.?!]{0,12}?"
_DEGREE = [
    (re.compile(_ME + r"(?:研究生|硕士|博士)"), "研究生"),
    (re.compile(_ME + r"本科"), "本科"),
]
_GRADE = re.compile(_ME + r"(大[一二三四五]|\d{4}\s*级)")
_MAJOR = re.compile(_ME + r"(?:是|读|学)([一-鿿]{2,10}?)专业")
_MAJOR_STOP = {"什么", "哪个", "校的", "学校的", "这个", "那个", "本", "原"}


def extract_slots(text: str) -> dict[str, str]:
    slots: dict[str, str] = {}
    for pat, value in _DEGREE:
        if pat.search(text):
            slots["degree"] = value
            break
    if m := _GRADE.search(text):
        slots["grade"] = m.group(1).replace(" ", "")
    if (m := _MAJOR.search(text)) and m.group(1) not in _MAJOR_STOP:
        slots["major"] = m.group(1)
    return slots


@dataclass
class Turn:
    question: str
    answer: str
    sources: list[str] = field(default_factory=list)  # "《文件名》第12页" 这类可读出处
    kind: str = "answer"


SLOT_NAMES = {"degree": "学历层次", "grade": "年级", "major": "专业"}


def estimate_tokens(text: str) -> int:
    """粗估：中文约 1 字 1 token（偏保守），只用来判断要不要压缩。"""
    return len(text)


class ConversationMemory:
    def __init__(
        self,
        mode: Mode = "compress",
        budget_tokens: int = 1200,
        keep_last: int = 2,
        summarizer=None,
    ) -> None:
        self.mode, self.budget, self.keep_last = mode, budget_tokens, keep_last
        self.summarizer = summarizer
        self.turns: list[Turn] = []
        self.summary = ""
        self.slots: dict[str, str] = {}
        self.compressions = self.compress_errors = 0
        self.cost_usd = 0.0
        self.last_render_tokens = 0

    def add(self, turn: Turn) -> None:
        self.slots |= extract_slots(turn.question)
        self.turns.append(turn)

    def _render(self) -> str:
        if not (self.turns or self.summary or self.slots):
            return ""
        lines = ["<对话历史>"]
        if self.slots:
            lines.append(
                "已知学生信息：" + "；".join(f"{SLOT_NAMES[k]}={v}" for k, v in self.slots.items())
            )
        if self.summary:
            lines.append(f"较早对话摘要：{self.summary}")
        for t in self.turns:
            src = f"（出处：{'；'.join(t.sources)}）" if t.sources else ""
            lines += [f"学生：{t.question}", f"助手：{t.answer}{src}"]
        lines.append("</对话历史>")
        return "\n".join(lines)

    def _compress(self) -> None:
        old, self.turns = self.turns[: -self.keep_last], self.turns[-self.keep_last :]
        if not old:
            return
        text = (f"已有摘要：{self.summary}\n" if self.summary else "") + "\n".join(
            f"学生：{t.question}\n助手：{t.answer}（出处：{'；'.join(t.sources)}）" for t in old
        )
        try:
            resp = self.summarizer.create(
                max_tokens=800,
                messages=[{"role": "user", "content": f"{SUMMARY_PROMPT}\n\n{text}"}],
                output_config={
                    "effort": "low",
                    "format": {"type": "json_schema", "schema": SUMMARY_SCHEMA},
                },
            )
            body = next(b.text for b in resp.content if b.type == "text")
            self.summary = json.loads(body)["summary"]
            self.cost_usd += cost_usd(self.summarizer.model, resp.usage)
            self.compressions += 1
        except Exception:  # 压缩失败：丢掉旧轮次原文，只留最近几轮和槽位，不能让历史无限增长
            self.compress_errors += 1

    def render(self) -> str:
        if self.mode == "none":
            return ""
        out = self._render()
        if (
            self.mode == "compress"
            and estimate_tokens(out) > self.budget
            and len(self.turns) > self.keep_last
        ):
            self._compress()
            out = self._render()
        self.last_render_tokens = estimate_tokens(out)
        return out
