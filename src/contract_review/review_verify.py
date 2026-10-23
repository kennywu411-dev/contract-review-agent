"""合同审查意见的依据校验（合同场景的 D4）。

模型层：Haiku 逐条核对理由能否由检索原文支撑；依据不足的意见改为转人工复核，不删除。
规则层：从意见的理由和修改建议里抽出"《民法典》第X条""本公司规范第X条"这类点名引用，
核对它是否真出现在本次检索到的原文里。点名了却没检索到的条文，是编造或凭记忆错引的直接信号。
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field

from contract_review.agent import _normalize_citations
from contract_review.llm import cost_usd
from contract_review.verify import VERIFY_SCHEMA, _source_text

_DIGITS = "零一二三四五六七八九"
_NUM = r"[0-9零〇一二两三四五六七八九十百千]+"
_ART = rf"第\s*{_NUM}\s*条"
# 文件名 → doc_id（policy 表示当前租户的合同审查规范）；书名号里不在这张表上的法名记为"其他:<名>"
_DOCS = [
    (r"民法典", "law-civil-code"),
    (r"合同编通则(?:若干问题的)?解释|通则解释|合同编解释|司法解释", "law-contract-interp-2023"),
    (r"民事诉讼法|民诉法", "law-civil-procedure"),
    (r"规范|细则|管理办法|办法", "policy"),
]
_CLAUSE_ITEM = rf"{_ART}(?:\s*第\s*{_NUM}\s*款)?"
# 连写的条号；后面紧跟"约定"的是合同自己的条款（"第五百八十五条及第八条约定"），不并入
_LIST = rf"{_CLAUSE_ITEM}(?:\s*[、和及与或]\s*{_CLAUSE_ITEM}(?!\s*约定))*"
_BOOK = re.compile(rf"《([^《》]{{1,40}})》[^《》第。；\n]{{0,8}}?({_LIST})")
_BARE = re.compile("(" + "|".join(p for p, _ in _DOCS) + rf")[」”\s]*({_LIST})")
_ONE = re.compile(rf"第\s*({_NUM})\s*条")


def _cn(n: int) -> str:
    """阿拉伯数字 → 法条里的中文写法：585 → 五百八十五，506 → 五百零六，510 → 五百一十。"""
    if n == 0:
        return "零"
    parts, units = [], [(1000, "千"), (100, "百"), (10, "十"), (1, "")]
    started, zero = False, False
    for value, unit in units:
        d = n // value % 10
        if d == 0:
            zero = started
            continue
        if zero:
            parts.append("零")
            zero = False
        if not (unit == "十" and d == 1 and not started):
            parts.append(_DIGITS[d])
        parts.append(unit)
        started = True
    return "".join(parts)


def _article(raw: str) -> str:
    raw = raw.replace(" ", "")
    return f"第{_cn(int(raw)) if raw.isdigit() else raw}条"


def _doc_id(name: str) -> str:
    for pattern, doc in _DOCS:
        if re.search(pattern, name):
            return doc
    return f"其他:{name}"


def law_refs(text: str) -> set[tuple[str, str]]:
    out: set[tuple[str, str]] = set()
    for regex in (_BOOK, _BARE):
        for m in regex.finditer(text or ""):
            doc = _doc_id(m.group(1))
            for a in _ONE.finditer(m.group(2)):
                out.add((doc, _article(a.group(1))))
    return out


def unretrieved_refs(finding: dict, citations: dict, tenant: str) -> list[tuple[str, str]]:
    """意见里点名、但本次检索结果里没有的条文（按 doc_id + 条号）。"""
    have = {
        (c["doc_id"], a)
        for c in citations.values()
        if c.get("kind") == "doc"
        for a in c.get("articles", [])
    }
    policy = f"{tenant}-contract-policy"
    refs = law_refs(f"{finding.get('reason', '')}\n{finding.get('suggestion', '')}")
    missing = []
    for doc, art in sorted(refs):
        key = (policy if doc == "policy" else doc, art)
        if key not in have:
            missing.append(key)
    return missing


# ---------------- 模型层：Haiku 核对理由能否由检索原文支撑 + 降级策略 ----------------


class ReviewVerifier:
    """一次调用核对一份合同的全部意见：每条给标签，并指出真正支撑它的原文编号。"""

    def __init__(self, llm) -> None:
        self.llm = llm

    def label(self, findings: list[dict], citations: dict, contract: str = ""):
        from contract_review.prompts import REVIEW_VERIFY_PROMPT

        sources = (
            "\n".join(
                f'[{cid}]\n<原文 id="{cid}">\n{_source_text(c)}\n</原文>'
                for cid, c in citations.items()
            )
            or "（没有检索到原文）"
        )
        items = "\n".join(
            f'<意见 index="{i}">条款：{f["clause_raw"] if f.get("clause_raw") else f["clause"]}\n'
            f"合同原文摘录：{f['quote']}\n理由：{f['reason']}</意见>"
            for i, f in enumerate(findings)
        )
        resp = self.llm.create(
            max_tokens=4000,
            messages=[
                {
                    "role": "user",
                    "content": (
                        f"{REVIEW_VERIFY_PROMPT}\n## 原文\n{sources}\n\n## 待核对意见\n{items}"
                    ),
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
        out = [by_index.get(i, ("unsupported", [], "")) for i in range(len(findings))]
        return out, cost_usd(self.llm.model, resp.usage)


@dataclass
class VerifiedReview:
    findings: list[dict] = field(default_factory=list)
    flagged: int = 0  # 因依据问题改为转人工的意见数
    revised: int = 0  # 理由被改写成只含有依据部分的意见数
    repaired: int = 0  # 引用编号被改正的意见数
    verifier_error: str = ""
    cost_usd: float = 0.0
    latency_ms: float = 0.0


def verify_review(r: dict, verifier, tenant: str, contract: str = "") -> VerifiedReview:
    """依据不足的意见不删除（漏报一处风险比多一条待复核意见代价大），改为转法务人工复核并注明原因。"""
    t0 = time.perf_counter()
    v = VerifiedReview()
    if r["kind"] != "review" or not r["findings"]:
        return v
    citations = r["citations"]
    try:
        labels, v.cost_usd = verifier.label(r["findings"], citations, contract)
    except Exception as e:  # 校验器出错：保守处理，全部转人工
        v.verifier_error = f"{type(e).__name__}: {e}"
        labels = [("unverified", [], "")] * len(r["findings"])
    for f, (label, support, revised) in zip(r["findings"], labels, strict=True):
        item = dict(f)
        flags: list[str] = []
        support = [c for c in _normalize_citations(support) if c in citations]
        if label in ("unsupported", "unverified") or not support:
            flags.append(label if label != "supported" else "unsupported")
        else:
            if label == "partial" and revised.strip():
                item["revised_from"] = f["reason"]
                item["reason"] = revised.strip()
                v.revised += 1
            elif label == "partial":
                flags.append("partial")
            if set(support) != set(f["citations"]):
                item["cited_by_model"] = f["citations"]
                item["citations"] = support
                v.repaired += 1
        flags += [f"unretrieved:{d}{a}" for d, a in unretrieved_refs(item, citations, tenant)]
        if not f.get("quote_found", True):
            flags.append("quote_mismatch")
        if flags:
            item["verify_flags"] = flags
            if not f["needs_human_review"]:
                v.flagged += 1
            item["needs_human_review"] = True
        v.findings.append(item)
    v.latency_ms = (time.perf_counter() - t0) * 1000
    return v
