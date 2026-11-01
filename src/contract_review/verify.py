"""生成后的引用校验（D4）。

第一层：数字检查。句子里的每个数字都必须出现在它引用的原文或查表结果里；中文数字、"1万"、千分位都先归一化。
第二层：Haiku 判断蕴含关系（HaikuVerifier）。两层都通过的 fact 句才保留。
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field

from contract_review.llm import cost_usd
from contract_review.prompts import VERIFY_PROMPT

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
# 不含"级"：会把"降一级"误读成数字 1（问题日志 P15），"2023级"这类年级是阿拉伯数字，不受影响
_UNITS = "年个次学期周天门学分元倍名档小时周岁月日人项等"
_CN_NUM = re.compile(rf"([零〇一二两三四五六七八九十百]+)(?=[{_UNITS}])")
_DOC_NO = re.compile(r"[〔\[［（(]\s*\d{4}\s*[〕\]］）)]\s*\d+\s*号")  # 文号：财教〔2024〕181号
_CLAUSE = re.compile(r"第[零〇一二两三四五六七八九十百千\d]+[条章节款项部分]")  # 条款编号不算事实
_WAN = re.compile(r"(\d+(?:\.\d+)?)\s*万")
_CN_WAN = re.compile(r"([零〇一二两三四五六七八九十百]+)万")  # "两万元""十万"
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

    def cn_wan(m: re.Match) -> str:
        n = _cn_to_int(m.group(1))
        if n is not None:
            out.add(str(n * 10000))
        return " "

    text = _CN_WAN.sub(cn_wan, text)
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
                    "support": {"type": "array", "items": {"type": "string"}},
                    "revised": {"type": "string"},
                    "reason": {"type": "string"},
                },
                "required": ["index", "label", "support", "revised", "reason"],
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
    repaired: int = 0  # 引用集合被改正的句数（只是顺序不同不算；原本无引用另计 cited_added）
    revised: int = 0  # partial 句被改写成只含有依据部分的句数
    cited_added: int = 0  # 模型没给引用、由校验器补上引用的句数
    cost_usd: float = 0.0
    latency_ms: float = 0.0


def _source_text(c: dict) -> str:
    if c["kind"] == "doc":
        return c["text"]
    return json.dumps({"columns": c["columns"], "rows": c["rows"]}, ensure_ascii=False)


class HaikuVerifier:
    """一次调用核对整份回答：每句给标签，并指出真正支撑它的编号（可改正挂错的引用）。"""

    def __init__(self, llm) -> None:
        self.llm = llm

    def label(
        self, sentences: list[str], citations: dict
    ) -> tuple[list[tuple[str, list[str]]], float]:
        sources = "\n".join(
            f'[{cid}]\n<原文 id="{cid}">\n{_source_text(c)}\n</原文>'
            for cid, c in citations.items()
        )
        items = "\n".join(f'<句子 index="{i}">{t}</句子>' for i, t in enumerate(sentences))
        resp = self.llm.create(
            max_tokens=3000,
            messages=[
                {
                    "role": "user",
                    "content": f"{VERIFY_PROMPT}\n## 原文\n{sources}\n\n## 待核对\n{items}",
                }
            ],
            output_config={
                "effort": "low",
                "format": {"type": "json_schema", "schema": VERIFY_SCHEMA},
            },
        )
        text = next(b.text for b in resp.content if b.type == "text")
        by_index = {
            x["index"]: (x["label"], x["support"], x.get("revised", ""))
            for x in json.loads(text)["labels"]
        }
        out = [by_index.get(i, ("unsupported", [], "")) for i in range(len(sentences))]
        return out, cost_usd(self.llm.model, resp.usage)


def verify_result(result, verifier: HaikuVerifier) -> VerifiedAnswer:
    t0 = time.perf_counter()
    if result.kind in ("refuse", "clarify", "fallback"):
        kind = "refuse" if result.kind == "fallback" else result.kind
        reason = result.refuse_reason or (REFUSE_NO_BASIS if kind == "refuse" else "")
        return VerifiedAnswer(kind, refuse_reason=reason, clarification=result.clarification)

    v = VerifiedAnswer(result.kind, unanswered=result.unanswered)
    kept: dict[int, dict] = {}
    facts: list[tuple[int, dict]] = []
    # note 句也交给校验器：无数字的"研究生也能申请国奖"被标成 note 也不能免检
    # 校验器对只说明边界、建议咨询部门的 note 句标 supported、support 可为空。
    for i, s in enumerate(result.sentences):
        facts.append((i, s))

    if facts:
        try:
            labels, cost = verifier.label([s["text"] for _, s in facts], result.citations)
            v.cost_usd += cost
        except Exception as e:  # 校验器自己出错：保守处理，不放行未校验的句子
            v.verifier_error = f"{type(e).__name__}: {e}"
            labels = [("unverified", [], "")] * len(facts)
        for (i, s), (label, support, revised) in zip(facts, labels, strict=True):
            support = [c for c in dict.fromkeys(support) if c in result.citations]
            if s.get("kind") == "note" and not extract_numbers(s["text"]):
                if label == "supported" or (label == "partial" and not support):
                    kept[i] = s | {"index": i}
                else:
                    v.dropped.append({"sentence": s, "reason": f"校验未通过（note，{label}）"})
                continue
            # partial：校验器给出只含有依据部分的改写时，保留改写（整句删除会丢掉答案要点，ADR 004）
            text = revised.strip() if label == "partial" and revised.strip() else s["text"]
            if not support or not (label == "supported" or text != s["text"]):
                v.dropped.append({"sentence": s, "reason": f"校验未通过（{label}）"})
                continue
            # 数字检查：文档用原文，表格只用数据行（表头里的数字不算依据）
            texts = [
                result.citations[c]["text"] for c in support if result.citations[c]["kind"] == "doc"
            ]
            tables = [
                result.citations[c]["rows"]
                for c in support
                if result.citations[c]["kind"] == "table"
            ]
            if not numbers_supported(text, texts, tables):
                v.dropped.append({"sentence": s, "reason": "数字与原文不符"})
                continue
            if not s["citations"]:
                v.cited_added += 1
            elif set(support) != set(s["citations"]):
                v.repaired += 1
            item = s | {
                "index": i,
                "kind": "fact",
                "citations": support,
                "cited_by_model": s["citations"],
            }
            if text != s["text"]:
                item |= {"text": text, "revised_from": s["text"]}
                v.revised += 1
            kept[i] = item

    v.kept = [kept[i] for i in sorted(kept)]
    if not any(s.get("kind", "fact") == "fact" for s in v.kept):
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
