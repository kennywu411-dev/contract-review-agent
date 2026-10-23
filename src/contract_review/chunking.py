"""切块：把 blocks 表变成检索用的 Chunk。三种策略用于 D2 对比实验（ADR 002）。

每个 Chunk 记住来源块序号和页码，引用校验（D4）靠它回到原文。
"""

from __future__ import annotations

import json
import re
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

Strategy = Literal["block", "fixed", "section"]
STRATEGIES: tuple[Strategy, ...] = ("block", "fixed", "section")
FIXED_SIZE, FIXED_OVERLAP = 400, 80
SECTION_MAX = 600
# fixed 切出的片段与某块至少重叠这么多字，才把那块的页码和条款号算进来（审查发现：
# 只重叠 1 字也算会让跨页 chunk 白拿一页的命中，引用时也会贴错条款号）
MIN_OVERLAP = 20
_ARTICLE = re.compile(r"^第[零一二三四五六七八九十百千〇两\d]+条")


@dataclass
class Chunk:
    chunk_id: str
    doc_id: str
    tenant: str
    title: str
    section_path: list[str]
    text: str
    pages: list[int] = field(default_factory=list)
    block_seqs: list[int] = field(default_factory=list)
    # 覆盖到的条款号。"第十二条"下面的"1. 2."被切到别的 chunk 时，靠它知道属于哪一条
    # （网页、DOCX 没有页码，评测命中和 D4 引用都按条款号定位）
    articles: list[str] = field(default_factory=list)


@dataclass
class _Body:
    seq: int
    text: str
    page: int | None
    path: list[str]
    article: str | None = None


def embed_text(c: Chunk) -> str:
    """向量和 BM25 用的文本：标题链作前缀，让"第三条"这类块也带上所属规定和章节。"""
    head = " > ".join([c.title, *c.section_path[1:]] if c.section_path else [c.title])
    return f"{head}\n{c.text}"


def _pages(bodies: list[_Body]) -> list[int]:
    return sorted({b.page for b in bodies if b.page is not None})


def _make(doc, strategy, n, bodies: list[_Body], text: str | None = None) -> Chunk:
    doc_id, tenant, title, titles = doc
    path = bodies[0].path
    own_title = path[0] if path and path[0] in titles else title
    return Chunk(
        chunk_id=f"{doc_id}:{strategy}:{n}",
        doc_id=doc_id,
        tenant=tenant,
        title=own_title,
        section_path=path,
        text=text if text is not None else "\n".join(b.text for b in bodies),
        pages=_pages(bodies),
        block_seqs=[b.seq for b in bodies],
        articles=list(dict.fromkeys(b.article for b in bodies if b.article)),
    )


def _block(doc, bodies: list[_Body]) -> list[Chunk]:
    return [_make(doc, "block", i, [b]) for i, b in enumerate(bodies)]


def _fixed(doc, bodies: list[_Body]) -> list[Chunk]:
    # 记录每个块在拼接文本里的起止位置，切出的片段据此找回块和页码
    spans, parts, pos = [], [], 0
    for b in bodies:
        spans.append((pos, pos + len(b.text), b))
        parts.append(b.text)
        pos += len(b.text) + 1
    full = "\n".join(parts)
    chunks, start, n = [], 0, 0
    while start < len(full):
        end = min(start + FIXED_SIZE, len(full))
        covered = [b for s, e, b in spans if min(e, end) - max(s, start) >= MIN_OVERLAP]
        if not covered:  # 极短的尾巴：退回到任意重叠
            covered = [b for s, e, b in spans if s < end and e > start]
        chunks.append(_make(doc, "fixed", n, covered, full[start:end]))
        n += 1
        if end == len(full):
            break
        start = end - FIXED_OVERLAP
    return chunks


def _section(doc, bodies: list[_Body]) -> list[Chunk]:
    chunks: list[Chunk] = []
    group: list[_Body] = []

    def flush():
        if group:
            chunks.append(_make(doc, "section", len(chunks), list(group)))
            group.clear()

    for b in bodies:
        size = sum(len(g.text) + 1 for g in group) + len(b.text)
        if group and (b.path != group[0].path or size > SECTION_MAX):
            flush()
        group.append(b)
    flush()
    return chunks


def load_chunks(db: Path, strategy: Strategy) -> list[Chunk]:
    conn = sqlite3.connect(db)
    docs = conn.execute("SELECT doc_id, tenant, title FROM documents ORDER BY doc_id").fetchall()
    out: list[Chunk] = []
    for doc_id, tenant, title in docs:
        rows = conn.execute(
            "SELECT seq, kind, level, text, page, section_path_json FROM blocks "
            "WHERE doc_id=? ORDER BY seq",
            (doc_id,),
        ).fetchall()
        titles = {text for _, kind, level, text, _, _ in rows if kind == "heading" and level == 0}
        bodies: list[_Body] = []
        article: str | None = None
        for seq, kind, _, text, page, path_json in rows:
            if kind == "heading":
                article = None  # 换章节，条款归属清空
                continue
            m = _ARTICLE.match(text)
            if m:
                article = m.group()
            bodies.append(_Body(seq, text, page, json.loads(path_json), article))
        if not bodies:
            continue
        doc = (doc_id, tenant, title, titles)
        out += {"block": _block, "fixed": _fixed, "section": _section}[strategy](doc, bodies)
    conn.close()
    return out
