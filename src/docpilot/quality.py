"""解析质量检查：扫描件、未解析页、乱码比例。只报告，不修改数据。

扫描判断看 PDF 文字层（pages 表），不看解析出的块：docling 会主动丢掉目录页，
只看块会把目录误报成扫描页（问题日志 P4）。
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from pathlib import Path

MIN_PAGE_CHARS = 20  # 文字层少于这个数视为"无文字页"
UNPARSED_LAYER_CHARS = 100  # 文字层不少于这个数、却没解析出块，视为"未解析页"
SCANNED_RATIO = 0.2  # 无文字页超过这个比例，整份文件视为疑似扫描件
GARBLED_THRESHOLD = 0.005
# 已在真实文件里见到的错误字形映射（中石油手册的 ToUnicode 表有误，问题日志 P3、P16）
GARBLE_MAP = {"暋": "", "暠": "”", "暡": "〔", "暢": "〕"}
KNOWN_GARBLE = set(GARBLE_MAP)


@dataclass
class DocQuality:
    doc_id: str
    n_pages: int | None
    n_blocks: int
    n_tables: int
    n_headings: int
    no_text_pages: list[int] = field(default_factory=list)
    unparsed_pages: list[int] = field(default_factory=list)
    garbled_ratio: float = 0.0
    flags: list[str] = field(default_factory=list)


def _is_garbled(ch: str) -> bool:
    return ch == "�" or "" <= ch <= "" or ch in KNOWN_GARBLE


def doc_quality(db: Path, doc_id: str) -> DocQuality:
    conn = sqlite3.connect(db)
    (n_pages,) = conn.execute("SELECT n_pages FROM documents WHERE doc_id=?", (doc_id,)).fetchone()
    rows = conn.execute("SELECT kind, text, page FROM blocks WHERE doc_id=?", (doc_id,)).fetchall()
    layer = dict(conn.execute("SELECT page, text_chars FROM pages WHERE doc_id=?", (doc_id,)))
    conn.close()
    block_chars: dict[int, int] = {}
    total = garbled = 0
    for _, text, page in rows:
        if page is not None:
            block_chars[page] = block_chars.get(page, 0) + len(text.strip())
        total += len(text)
        garbled += sum(_is_garbled(c) for c in text)
    q = DocQuality(
        doc_id=doc_id,
        n_pages=n_pages,
        n_blocks=len(rows),
        n_tables=sum(k == "table" for k, _, _ in rows),
        n_headings=sum(k == "heading" for k, _, _ in rows),
    )
    for page in sorted(layer):
        if layer[page] < MIN_PAGE_CHARS:
            q.no_text_pages.append(page)
        elif layer[page] >= UNPARSED_LAYER_CHARS and block_chars.get(page, 0) < MIN_PAGE_CHARS:
            q.unparsed_pages.append(page)
    q.garbled_ratio = garbled / total if total else 0.0
    if layer and len(q.no_text_pages) / len(layer) > SCANNED_RATIO:
        q.flags.append("疑似扫描件")
    if q.garbled_ratio > GARBLED_THRESHOLD:
        q.flags.append("乱码")
    return q
