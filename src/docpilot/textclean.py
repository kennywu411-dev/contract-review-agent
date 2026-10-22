"""PDF 抽取文本的清洗：去掉错误字形映射出来的空白符号、去掉中文字之间的断行空格。"""

from __future__ import annotations

import re

from docpilot.quality import KNOWN_GARBLE

_CJK = r"㐀-鿿＀-￯　-〿"
_CJK_GAP = re.compile(rf"(?<=[{_CJK}])[ \t]+(?=[{_CJK}])")
_GARBLE = re.compile("[" + "".join(sorted(KNOWN_GARBLE)) + "]")


def normalize(text: str) -> str:
    text = _GARBLE.sub("", text)
    text = _CJK_GAP.sub("", text)
    return re.sub(r"[ \t]{2,}", " ", text).strip()
