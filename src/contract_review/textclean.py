"""PDF 抽取文本的清洗：去掉错误字形映射出来的空白符号、去掉中文字之间的断行空格。"""

from __future__ import annotations

import re

from contract_review.quality import GARBLE_MAP, KNOWN_GARBLE

_CJK = r"㐀-鿿＀-￯　-〿"
_CJK_GAP = re.compile(rf"(?<=[{_CJK}])[ \t]+(?=[{_CJK}])")
_GARBLE = re.compile("[" + "".join(sorted(KNOWN_GARBLE)) + "]")


def strip_garble(text: str) -> str:
    """把错误字形换回原字符；表格单元格之间的空格要保留，不能走 normalize。"""
    return _GARBLE.sub(lambda m: GARBLE_MAP[m.group()], text)


def normalize(text: str) -> str:
    text = strip_garble(text)
    text = _CJK_GAP.sub("", text)
    return re.sub(r"[ \t]{2,}", " ", text).strip()
