"""网页类法规（gov.cn、npc.gov.cn 等政府网站）的正文抽取，只用标准库。"""

from __future__ import annotations

import re
from html.parser import HTMLParser
from pathlib import Path

from contract_review.blocks import Block
from contract_review.structure import RawItem, assemble

# 各站正文容器：gov.cn 用 id=UCAP-CONTENT，人大网用 TRS_Editor，
# 博达站群用 v_news_content，最高法用 txt_txt
CONTAINERS = (
    ("id", "UCAP-CONTENT"),
    ("class", "TRS_Editor"),
    ("class", "v_news_content"),
    ("class", "txt_txt"),
)
BREAK_TAGS = {"p", "div", "br", "h1", "h2", "h3", "h4", "h5", "h6", "li", "tr"}
SKIP_TAGS = {"script", "style"}
# 没有闭合标签的空元素：不能计入深度，否则正文容器永远关不上，页脚会被当成正文
VOID_TAGS = {
    "area",
    "base",
    "br",
    "col",
    "embed",
    "hr",
    "img",
    "input",
    "link",
    "meta",
    "param",
    "source",
    "track",
    "wbr",
}


class _BodyParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.depth = 0  # 进入正文容器后的标签深度；0 表示不在正文里
        self.skip = 0
        self.lines: list[str] = []
        self.buf: list[str] = []

    def _flush(self) -> None:
        line = re.sub(r"[ \t\r\n\xa0]+", " ", "".join(self.buf)).strip()
        if line:
            self.lines.append(line)
        self.buf = []

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if self.depth == 0:
            if any(
                key in a and val in (a[key] or "").split() + [a[key]] for key, val in CONTAINERS
            ):
                self.depth = 1
            return
        if tag in SKIP_TAGS:
            self.skip += 1
        if tag in BREAK_TAGS:
            self._flush()
        if tag not in VOID_TAGS:
            self.depth += 1

    def handle_startendtag(self, tag, attrs):
        if self.depth and tag in BREAK_TAGS:
            self._flush()

    def handle_endtag(self, tag):
        if self.depth == 0 or tag in VOID_TAGS:
            return
        if tag in SKIP_TAGS:
            self.skip = max(0, self.skip - 1)
        if tag in BREAK_TAGS:
            self._flush()
        self.depth -= 1
        if self.depth == 0:
            self._flush()

    def handle_data(self, data):
        if self.depth and not self.skip:
            self.buf.append(data)


def parse_html(path: Path, doc_id: str) -> list[Block]:
    parser = _BodyParser()
    parser.feed(path.read_text(encoding="utf-8", errors="replace"))
    parser._flush()
    return assemble(doc_id, [RawItem("paragraph", line) for line in parser.lines])
