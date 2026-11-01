"""入库：把解析出的 Block 写进 SQLite，按文件 sha256 判断是否需要重新解析。"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from pypdf import PdfReader

from contract_review.config import DB_PATH
from contract_review.parse import parse_document
from contract_review.sources import Source, raw_path, sha256_of

SCHEMA = """
CREATE TABLE IF NOT EXISTS documents (
    doc_id TEXT PRIMARY KEY, tenant TEXT NOT NULL, title TEXT NOT NULL, url TEXT NOT NULL,
    issuer TEXT, issued TEXT, applies_to TEXT, kind TEXT NOT NULL,
    sha256 TEXT NOT NULL, n_pages INTEGER, parsed_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS blocks (
    doc_id TEXT NOT NULL REFERENCES documents(doc_id), seq INTEGER NOT NULL,
    kind TEXT NOT NULL, level INTEGER, text TEXT NOT NULL,
    page INTEGER, page_label TEXT, bbox_json TEXT, section_path_json TEXT NOT NULL,
    PRIMARY KEY (doc_id, seq)
);
CREATE TABLE IF NOT EXISTS pages (
    doc_id TEXT NOT NULL REFERENCES documents(doc_id), page INTEGER NOT NULL,
    text_chars INTEGER NOT NULL,  -- PDF 文字层字数（pypdf），用来区分扫描页和解析丢失
    PRIMARY KEY (doc_id, page)
);
"""


@dataclass
class IngestReport:
    parsed: list[str] = field(default_factory=list)
    unchanged: list[str] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)
    failed: dict[str, str] = field(default_factory=dict)  # doc_id → 错误信息


def init_db(db: Path) -> sqlite3.Connection:
    db.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db)
    conn.executescript(SCHEMA)
    return conn


def text_layer_chars(path: Path) -> list[int]:
    """每页文字层的非空白字数，第 i 项对应第 i+1 页。"""
    return [len("".join((p.extract_text() or "").split())) for p in PdfReader(path).pages]


def ingest(sources: list[Source], db: Path = DB_PATH, force: bool = False) -> IngestReport:
    """force=True 时忽略 sha256，全部重新解析（解析规则改了之后用）。"""
    report = IngestReport()
    conn = init_db(db)
    for src in sources:
        path = raw_path(src)
        if not path.exists():
            report.missing.append(src.doc_id)
            continue
        sha = sha256_of(path)
        row = conn.execute("SELECT sha256 FROM documents WHERE doc_id=?", (src.doc_id,)).fetchone()
        if row and row[0] == sha and not force:
            report.unchanged.append(src.doc_id)
            continue
        try:
            blocks = parse_document(path, src.doc_id, src.kind)
            layer = text_layer_chars(path) if src.kind == "pdf" else []
        except Exception as e:  # 一份坏文件不能拖垮整批入库，记下来汇总报告
            report.failed[src.doc_id] = f"{type(e).__name__}: {e}"
            continue
        with conn:  # 一个事务：先删旧块再写新块，中途失败不会留下半份文档
            conn.execute("DELETE FROM blocks WHERE doc_id=?", (src.doc_id,))
            conn.execute("DELETE FROM pages WHERE doc_id=?", (src.doc_id,))
            conn.execute(
                "INSERT OR REPLACE INTO documents VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (
                    src.doc_id,
                    src.tenant,
                    src.title,
                    src.url,
                    src.issuer,
                    src.issued,
                    src.applies_to,
                    src.kind,
                    sha,
                    len(layer) or None,
                    datetime.now().isoformat(timespec="seconds"),
                ),
            )
            conn.executemany(
                "INSERT INTO blocks VALUES (?,?,?,?,?,?,?,?,?)",
                [
                    (
                        b.doc_id,
                        b.seq,
                        b.kind,
                        b.level,
                        b.text,
                        b.page,
                        b.page_label,
                        json.dumps(b.bbox) if b.bbox else None,
                        json.dumps(b.section_path, ensure_ascii=False),
                    )
                    for b in blocks
                ],
            )
            conn.executemany(
                "INSERT INTO pages VALUES (?,?,?)",
                [(src.doc_id, i + 1, n) for i, n in enumerate(layer)],
            )
        report.parsed.append(src.doc_id)
    conn.close()
    return report
