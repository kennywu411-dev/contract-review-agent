"""合同审查意见的依据校验（合同场景的 D4）。

规则层：从意见的理由和修改建议里抽出"《民法典》第X条""本公司规范第X条"这类点名引用，
核对它是否真出现在本次检索到的原文里。点名了却没检索到的条文，是编造或凭记忆错引的直接信号。
"""

from __future__ import annotations

import re

_DIGITS = "零一二三四五六七八九"
_NUM = r"[0-9零〇一二两三四五六七八九十百千]+"
_ART = rf"第\s*{_NUM}\s*条"
# 文件名 → doc_id（policy 表示当前租户的合同审查规范）
_DOCS = [
    (r"民法典", "law-civil-code"),
    (r"合同编通则(?:若干问题的)?解释|通则解释|合同编解释|司法解释", "law-contract-interp-2023"),
    (r"民事诉讼法|民诉法", "law-civil-procedure"),
    (r"规范|细则|管理办法|办法", "policy"),
]
_REF = re.compile(
    "("
    + "|".join(p for p, _ in _DOCS)
    + r")[》」”\s]*("
    + _ART
    + r"(?:\s*第\s*"
    + _NUM
    + r"\s*款)?(?:\s*[、和及与或]\s*"
    + _ART
    + r"(?:\s*第\s*"
    + _NUM
    + r"\s*款)?)*)"
)
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
    return next(d for p, d in _DOCS if re.fullmatch(p, name))


def law_refs(text: str) -> set[tuple[str, str]]:
    out: set[tuple[str, str]] = set()
    for m in _REF.finditer(text or ""):
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
