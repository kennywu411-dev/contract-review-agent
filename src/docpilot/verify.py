"""生成后的引用校验（D4）。

第一层：数字检查。句子里的每个数字都必须出现在它引用的原文或查表结果里；中文数字、"1万"、千分位都先归一化。
第二层：Haiku 判断蕴含关系（HaikuVerifier）。两层都通过的 fact 句才保留。
"""

from __future__ import annotations

import re

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
