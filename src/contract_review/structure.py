"""把解析器给出的"原始行"组装成带层级和标题链的 Block。

PDF（docling）和网页（html_parse）共用这一步，层级以 headings.heading_level 为准。
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from contract_review.blocks import Block
from contract_review.headings import heading_level

# 超过这个长度、或以句号结尾的编号行视为正文段落（如"一、……。"）
MAX_HEADING_CHARS = 40
ARTICLE_LEVEL = 4  # "第X条" 是条文段落，不进标题链
TITLE_LEVEL = 0  # 没有编号的文件名（如"中华人民共和国学位法"）
MAX_TITLE_CHARS = 30
# 标题链里的先后：文件名排在"部分"之下、"章"之上；"1.1"排在"1."和"（1）"之间；
# 民法典的"编""分编"排在最上
RANK = {TITLE_LEVEL: 1.5, 9: 7.5, 10: 0.5, 11: 0.8}
# 文件名必须以规章类名词结尾（允许跟一个括号，如"（试行）""（2025年修订）"）
_TITLE = re.compile(
    r"(规定|办法|细则|条例|法|章程|方案|意见|通知|标准|手册|规则|决定|制度|守则|准则|须知|指南|纲要|规范)"
    r"([（(][^）)]{1,12}[）)])?$"
)
_DOC_NUMBER = re.compile(r"[〔\[]\d{4}[〕\]]")  # 文号，如"师校发〔2016〕43号"
# 低层级编号（（一）、1.、（1）、1.1）后面跟着逗号冒号，多半是被版面截断的正文，不是标题
_CLAUSE = re.compile(r"[，,：:；;]")
SENTENCE_END = ("。", "；", ";", "：", ":", "！", "？", "”", "）", ")")


@dataclass
class RawItem:
    kind: str  # "heading" | "paragraph" | "table" | "list"：解析器的判断，仅作参考
    text: str
    page: int | None = None
    bbox: tuple[float, float, float, float] | None = None


def _rank(level: int) -> float:
    return RANK.get(level, level)


def _numbered_heading(text: str, level: int | None) -> bool:
    if level is None or level == ARTICLE_LEVEL:
        return False
    text = text.strip()
    if len(text) > MAX_HEADING_CHARS or text.endswith(("。", "；", ";")):
        return False
    return not (level >= 6 and _CLAUSE.search(text))


def _is_title(text: str) -> bool:
    return (
        len(text) <= MAX_TITLE_CHARS and bool(_TITLE.search(text)) and not _DOC_NUMBER.search(text)
    )


def merge_paragraphs(items: list[RawItem]) -> list[RawItem]:
    """把 PDF 里被版面切断的段落/列表项接回去：

    同一页，上一行不像标题、没以句末标点结束，下一行是正文且不带新编号。
    """
    out: list[RawItem] = []
    for item in items:
        prev = out[-1] if out else None
        if (
            prev is not None
            and prev.kind in ("paragraph", "list")
            and item.kind in ("paragraph", "list")
            and prev.page == item.page
            and not prev.text.rstrip().endswith(SENTENCE_END)
            and not _numbered_heading(prev.text, heading_level(prev.text))
            and heading_level(item.text) is None
        ):
            bbox = prev.bbox
            if prev.bbox and item.bbox:
                bbox = (
                    min(prev.bbox[0], item.bbox[0]),
                    min(prev.bbox[1], item.bbox[1]),
                    max(prev.bbox[2], item.bbox[2]),
                    max(prev.bbox[3], item.bbox[3]),
                )
            out[-1] = RawItem(prev.kind, prev.text + item.text, prev.page, bbox)
        else:
            out.append(item)
    return out


def assemble(doc_id: str, items: list[RawItem]) -> list[Block]:
    blocks: list[Block] = []
    stack: list[tuple[int, str]] = []  # (level, 标题文本)
    pending: RawItem | None = None  # 文件名的前半行，如"中国石油大学(华东)"

    def emit(item: RawItem, text: str, kind: str, level: int | None) -> None:
        if kind == "heading":
            while stack and _rank(stack[-1][0]) >= _rank(level):
                stack.pop()
            path = [t for _, t in stack]
            stack.append((level, text))
        else:
            path = [t for _, t in stack]
        blocks.append(
            Block(
                doc_id=doc_id,
                seq=len(blocks),
                kind=kind,
                text=text,
                level=level,
                page=item.page,
                bbox=item.bbox,
                section_path=path,
            )
        )

    for item in items:
        text = item.text.strip()
        if not text:
            continue
        level = heading_level(text)
        if level is None and item.kind == "heading":
            if pending is not None and pending.page == item.page and _is_title(pending.text + text):
                # 文件名被版面拆成两行时接回去
                emit(pending, pending.text + text, "heading", TITLE_LEVEL)
                pending = None
                continue
            if pending is not None:
                emit(pending, pending.text, "paragraph", None)
                pending = None
            if _is_title(text):
                emit(item, text, "heading", TITLE_LEVEL)
            elif len(text) <= MAX_TITLE_CHARS and not _DOC_NUMBER.search(text):
                pending = item
            else:
                emit(item, text, "paragraph", None)
            continue
        if pending is not None:
            emit(pending, pending.text, "paragraph", None)
            pending = None
        if item.kind != "table" and _numbered_heading(text, level):
            emit(item, text, "heading", level)
        else:
            kind = item.kind if item.kind in ("table", "list") else "paragraph"
            emit(item, text, kind, ARTICLE_LEVEL if level == ARTICLE_LEVEL else None)
    if pending is not None:
        emit(pending, pending.text, "paragraph", None)
    return blocks


def drop_part_navigation(items: list[RawItem]) -> list[RawItem]:
    """手册的分部扉页会在侧栏列出全部"部分"，还有只写"第X部分"的页签。

    每页最多保留一个"部分"级标题：优先取出现两次的（扉页大标题 + 侧栏），否则取第一个
    带名称的；页签（只有"第X部分"）、几个部分粘在一行的侧栏都丢掉，避免标题链被导航覆盖。
    """
    by_page: dict[int | None, list[str]] = {}
    for item in items:
        text = item.text.strip()
        if heading_level(text) == 1 and not _BARE_PART.match(text) and text.count("部分") == 1:
            by_page.setdefault(item.page, []).append(text)
    keep: dict[int | None, str] = {}
    for page, texts in by_page.items():
        repeated = [t for t in texts if texts.count(t) > 1]
        keep[page] = (repeated or texts)[0]
    out: list[RawItem] = []
    kept: set[int | None] = set()
    for item in items:
        text = item.text.strip()
        if heading_level(text) == 1:
            if text == keep.get(item.page) and item.page not in kept:
                kept.add(item.page)
                out.append(item)
            continue  # 导航、页签、重复的扉页标题都丢掉，不进正文
        out.append(item)
    return out


_BARE_PART = re.compile(r"^第[零一二三四五六七八九十]+部分$")
