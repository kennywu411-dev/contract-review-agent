"""生成后的引用校验（D4）。

第一层：数字检查。句子里的每个数字都必须出现在它引用的原文或查表结果里；中文数字、"1万"、千分位都先归一化。
第二层：Haiku 判断蕴含关系（HaikuVerifier）。两层都通过的 fact 句才保留。
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field

from docpilot.llm import cost_usd
from docpilot.prompts import VERIFY_PROMPT

_CN = {
    "零": 0,
    "〇": 0,
    "一": 1,
    "二": 2,
    "两": 2,
    "三": 3,
    "四": 4,
    "五": 5,
    "六": 6,
    "七": 7,
    "八": 8,
    "九": 9,
}
# 中文数字后面跟这些量词时才当数字（避免把"一般""统一"当成 1）
_UNITS = "年个次学期周天门学分元倍名级档小时周岁月日人项等"
_CN_NUM = re.compile(rf"([零〇一二两三四五六七八九十百]+)(?=[{_UNITS}])")
_DOC_NO = re.compile(r"[〔\[［（(]\s*\d{4}\s*[〕\]］）)]\s*\d+\s*号")  # 文号：财教〔2024〕181号
_CLAUSE = re.compile(r"第[零〇一二两三四五六七八九十百千\d]+[条章节款项部分]")  # 条款编号不算事实
_WAN = re.compile(r"(\d+(?:\.\d+)?)\s*万")
_ARABIC = re.compile(r"\d+(?:,\d{3})*(?:\.\d+)?")


def _cn_to_int(s: str) -> int | None:
    if s in ("十",):
        return 10
    total, cur = 0, 0
    for ch in s:
        if ch in _CN:
            cur = _CN[ch]
        elif ch == "十":
            total += (cur or 1) * 10
            cur = 0
        elif ch == "百":
            total += (cur or 1) * 100
            cur = 0
        else:
            return None
    return total + cur


def _norm(x: str) -> str:
    v = float(x.replace(",", ""))
    return str(int(v)) if v == int(v) else str(v)


def extract_numbers(text: str) -> set[str]:
    text = _CLAUSE.sub(" ", _DOC_NO.sub(" ", text))
    out: set[str] = set()

    def wan(m: re.Match) -> str:
        out.add(_norm(str(float(m.group(1)) * 10000)))
        return " "

    text = _WAN.sub(wan, text)
    for m in _CN_NUM.finditer(text):
        n = _cn_to_int(m.group(1))
        if n is not None:
            out.add(str(n))
    out |= {_norm(m) for m in _ARABIC.findall(text)}
    return out


def numbers_supported(
    sentence: str, source_texts: list[str], table_rows: list[list[list]] | None = None
) -> bool:
    need = extract_numbers(sentence)
    if not need:
        return True
    have: set[str] = set()
    for t in source_texts:
        have |= extract_numbers(t)
    for rows in table_rows or []:
        for row in rows:
            for cell in row:
                if isinstance(cell, int | float):
                    have.add(_norm(str(cell)))
                elif isinstance(cell, str):
                    have |= extract_numbers(cell)
    return need <= have


# ---------------- 第二层：Haiku 蕴含判断 + 降级策略 ----------------

VERIFY_SCHEMA = {
    "type": "object",
    "properties": {
        "labels": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "index": {"type": "integer"},
                    "label": {"type": "string", "enum": ["supported", "partial", "unsupported"]},
                    "reason": {"type": "string"},
                },
                "required": ["index", "label", "reason"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["labels"],
    "additionalProperties": False,
}


REFUSE_NO_BASIS = "未能在已收录的学校文件中找到可靠依据，建议咨询学校教务处或学生工作部门。"


@dataclass
class VerifiedAnswer:
    kind: str  # answer / partial / refuse / clarify
    kept: list[dict] = field(default_factory=list)
    dropped: list[dict] = field(default_factory=list)  # {sentence, reason}
    refuse_reason: str = ""
    unanswered: str = ""
    clarification: str = ""
    verifier_error: str = ""
    cost_usd: float = 0.0
    latency_ms: float = 0.0


def _sources(sentence: dict, citations: dict) -> tuple[list[str], list[list[list]]]:
    texts, tables = [], []
    for cid in sentence["citations"]:
        c = citations.get(cid)
        if not c:
            continue
        if c["kind"] == "doc":
            texts.append(c["text"])
        else:
            tables.append(c["rows"])
            texts.append(
                json.dumps({"columns": c["columns"], "rows": c["rows"]}, ensure_ascii=False)
            )
    return texts, tables


class HaikuVerifier:
    def __init__(self, llm) -> None:
        self.llm = llm

    def label(self, items: list[tuple[str, list[str]]]) -> tuple[list[str], float]:
        """items: (句子, 引用原文列表)。返回每句的标签和本次花费。"""
        parts = [VERIFY_PROMPT]
        for i, (sent, texts) in enumerate(items):
            src = "\n---\n".join(texts)
            parts.append(f'<句子 index="{i}">{sent}</句子>\n<原文>\n{src}\n</原文>\n')
        resp = self.llm.create(
            max_tokens=2000,
            messages=[{"role": "user", "content": "\n".join(parts)}],
            output_config={
                "effort": "low",
                "format": {"type": "json_schema", "schema": VERIFY_SCHEMA},
            },
        )
        text = next(b.text for b in resp.content if b.type == "text")
        by_index = {x["index"]: x["label"] for x in json.loads(text)["labels"]}
        return [by_index.get(i, "unsupported") for i in range(len(items))], cost_usd(
            self.llm.model, resp.usage
        )


def verify_result(result, verifier: HaikuVerifier) -> VerifiedAnswer:
    t0 = time.perf_counter()
    if result.kind in ("refuse", "clarify", "fallback"):
        kind = "refuse" if result.kind == "fallback" else result.kind
        reason = result.refuse_reason or (REFUSE_NO_BASIS if kind == "refuse" else "")
        return VerifiedAnswer(kind, refuse_reason=reason, clarification=result.clarification)

    v = VerifiedAnswer(result.kind, unanswered=result.unanswered)
    candidates: list[tuple[int, dict, list[str]]] = []
    keep_flags: dict[int, bool] = {}
    for i, s in enumerate(result.sentences):
        is_note = s.get("kind") == "note" and not extract_numbers(s["text"])
        if is_note:
            keep_flags[i] = True
            continue
        valid = [c for c in s["citations"] if c in result.citations]
        if not valid:
            v.dropped.append({"sentence": s, "reason": "无引用"})
            continue
        texts, tables = _sources({"citations": valid}, result.citations)
        if not numbers_supported(s["text"], texts, tables):
            v.dropped.append({"sentence": s, "reason": "数字与原文不符"})
            continue
        candidates.append((i, s, texts))

    if candidates:
        try:
            labels, cost = verifier.label([(s["text"], texts) for _, s, texts in candidates])
            v.cost_usd += cost
        except Exception as e:  # 校验器自己出错：保守处理，不放行未校验的句子
            v.verifier_error = f"{type(e).__name__}: {e}"
            labels = ["unverified"] * len(candidates)
        for (i, s, _), label in zip(candidates, labels, strict=True):
            if label == "supported":
                keep_flags[i] = True
            else:
                v.dropped.append({"sentence": s, "reason": f"校验未通过（{label}）"})

    v.kept = [s for i, s in enumerate(result.sentences) if keep_flags.get(i)]
    facts_kept = [s for s in v.kept if s.get("kind") != "note" or extract_numbers(s["text"])]
    if not facts_kept:
        v.kind = "refuse"
        v.refuse_reason = (
            f"{result.unanswered}：文件中没有相关规定。" if result.unanswered else REFUSE_NO_BASIS
        )
        v.kept = []
    elif v.dropped or result.unanswered:
        v.kind = "partial"
    else:
        v.kind = "answer"
    v.latency_ms = (time.perf_counter() - t0) * 1000
    return v
