"""把解析器给出的"原始行"组装成带层级和标题链的 Block。

PDF（docling）和网页（html_parse）共用这一步，层级以 headings.heading_level 为准。
"""

from __future__ import annotations

from dataclasses import dataclass

from docpilot.blocks import Block
from docpilot.headings import heading_level

# 超过这个长度、或以句号结尾的编号行视为正文段落（如"一、……。"）
MAX_HEADING_CHARS = 40
ARTICLE_LEVEL = 4  # "第X条" 是条文段落，不进标题链
TITLE_LEVEL = 0  # 解析器判为标题、但没有编号的行：文件名（如"中华人民共和国学位法"）
MAX_TITLE_CHARS = 30
NOT_TITLES = {"目录", "目 录", "前言"}


@dataclass
class RawItem:
    kind: str  # "heading" | "paragraph" | "table" | "list"：解析器的判断，仅作参考
    text: str
    page: int | None = None
    bbox: tuple[float, float, float, float] | None = None


SENTENCE_END = ("。", "；", ";", "：", ":", "！", "？", "”", "）", ")")


def merge_paragraphs(items: list[RawItem]) -> list[RawItem]:
    """把 PDF 里被版面切断的段落接回去：同一页、上一段没以句末标点结束、下一段不是新编号。"""
    out: list[RawItem] = []
    for item in items:
        prev = out[-1] if out else None
        if (
            prev is not None
            and prev.kind == item.kind == "paragraph"
            and prev.page == item.page
            and not prev.text.rstrip().endswith(SENTENCE_END)
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
            out[-1] = RawItem("paragraph", prev.text + item.text, prev.page, bbox)
        else:
            out.append(item)
    return out


def _title_level(item: RawItem) -> int | None:
    text = item.text.strip()
    if item.kind == "heading" and len(text) <= MAX_TITLE_CHARS and text not in NOT_TITLES:
        return TITLE_LEVEL
    return None


def _is_heading(item: RawItem, level: int | None) -> bool:
    if level is None or level == ARTICLE_LEVEL or item.kind == "table":
        return False
    text = item.text.strip()
    return len(text) <= MAX_HEADING_CHARS and not text.endswith(("。", "；", ";"))


def assemble(doc_id: str, items: list[RawItem]) -> list[Block]:
    blocks: list[Block] = []
    stack: list[tuple[int, str]] = []  # (level, 标题文本)
    for item in items:
        text = item.text.strip()
        if not text:
            continue
        level = heading_level(text)
        if level is None:
            level = _title_level(item)
        prev = blocks[-1] if blocks else None
        if (
            level == TITLE_LEVEL
            and prev is not None
            and prev.kind == "heading"
            and prev.level == TITLE_LEVEL
            and prev.page == item.page
        ):
            # 标题被版面拆成两行（"中国石油大学(华东)" + "本科生学籍管理规定"）时接回去
            prev.text += text
            stack[-1] = (TITLE_LEVEL, prev.text)
            continue
        if _is_heading(item, level):
            while stack and stack[-1][0] >= level:
                stack.pop()
            path = [t for _, t in stack]
            stack.append((level, text))
            kind, lvl = "heading", level
        else:
            path = [t for _, t in stack]
            kind = item.kind if item.kind in ("table", "list") else "paragraph"
            lvl = ARTICLE_LEVEL if level == ARTICLE_LEVEL else None
        blocks.append(
            Block(
                doc_id=doc_id,
                seq=len(blocks),
                kind=kind,
                text=text,
                level=lvl,
                page=item.page,
                bbox=item.bbox,
                section_path=path,
            )
        )
    return blocks
