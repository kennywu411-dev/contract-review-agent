"""按中文公文编号推断标题层级。

解析器（docling 等）常把标题全标成同一级，规章文件的层级却写在编号里：
（编 > 分编 >）部分 > 章 > 节 > 条 > 一、 > （一） > 1. > 1.1 > （1）。
返回值是编号类型；在标题链里的先后由 structure.RANK 决定（1.1 返回 9，但排在 1. 和 （1） 之间）。
"""

from __future__ import annotations

import re

CN_NUM = "零一二三四五六七八九十百千〇两"
# 中石油手册把全角空格抽成了"暋"，比较前当作空白去掉
_SPACE = re.compile(r"[\s　暋]+")

_RULES: list[tuple[int, re.Pattern[str]]] = [
    (10, re.compile(rf"^第[{CN_NUM}\d]+编")),
    (11, re.compile(rf"^第[{CN_NUM}\d]+分编")),
    (1, re.compile(rf"^第[{CN_NUM}\d]+部分")),
    (2, re.compile(rf"^第[{CN_NUM}\d]+章")),
    (3, re.compile(rf"^第[{CN_NUM}\d]+节")),
    (4, re.compile(rf"^第[{CN_NUM}\d]+条")),
    (5, re.compile(rf"^[{CN_NUM}]+、")),
    (6, re.compile(rf"^[（(][{CN_NUM}]+[）)]")),
    (7, re.compile(r"^\d{1,2}[.．、](?!\d)")),
    (8, re.compile(r"^[（(]\d{1,2}[）)]")),
    (9, re.compile(r"^\d{1,2}\.\d{1,2}(?!\d)")),
]


def heading_level(text: str) -> int | None:
    t = _SPACE.sub("", text)
    for level, pattern in _RULES:
        if pattern.match(t):
            return level
    return None
