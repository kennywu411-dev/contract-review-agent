"""解析质量检查：疑似扫描页、乱码比例。只报告，不修改数据。"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from pathlib import Path

MIN_PAGE_CHARS = 20
GARBLED_THRESHOLD = 0.005
# 已在真实文件里见到的错误字形映射（中石油手册：全角空格被抽成"暋"）
KNOWN_GARBLE = set("暋暠")


@dataclass
class DocQuality:
    doc_id: str
    n_pages: int | None
    n_blocks: int
    n_tables: int
    n_headings: int
    empty_pages: list[int] = field(default_factory=list)
    garbled_ratio: float = 0.0
    flags: list[str] = field(default_factory=list)


def _is_garbled(ch: str) -> bool:
    return ch == "�" or "" <= ch <= "" or ch in KNOWN_GARBLE


def doc_quality(db: Path, doc_id: str) -> DocQuality:
    conn = sqlite3.connect(db)
    (n_pages,) = conn.execute("SELECT n_pages FROM documents WHERE doc_id=?", (doc_id,)).fetchone()
    rows = conn.execute("SELECT kind, text, page FROM blocks WHERE doc_id=?", (doc_id,)).fetchall()
    conn.close()
    chars_per_page: dict[int, int] = {}
    total = garbled = 0
    for _, text, page in rows:
        if page is not None:
            chars_per_page[page] = chars_per_page.get(page, 0) + len(text.strip())
        total += len(text)
        garbled += sum(_is_garbled(c) for c in text)
    q = DocQuality(
        doc_id=doc_id,
        n_pages=n_pages,
        n_blocks=len(rows),
        n_tables=sum(k == "table" for k, _, _ in rows),
        n_headings=sum(k == "heading" for k, _, _ in rows),
    )
    if n_pages:
        q.empty_pages = [
            p for p in range(1, n_pages + 1) if chars_per_page.get(p, 0) < MIN_PAGE_CHARS
        ]
    q.garbled_ratio = garbled / total if total else 0.0
    if q.empty_pages:
        q.flags.append("疑似扫描页")
    if q.garbled_ratio > GARBLED_THRESHOLD:
        q.flags.append("乱码")
    return q
