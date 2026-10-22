"""入库：把解析出的 Block 写进 SQLite，按文件 sha256 判断是否需要重新解析。"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from docpilot.config import DB_PATH
from docpilot.parse import parse_document
from docpilot.sources import Source, raw_path, sha256_of

SCHEMA = """
CREATE TABLE IF NOT EXISTS documents (
    doc_id TEXT PRIMARY KEY, school TEXT NOT NULL, title TEXT NOT NULL, url TEXT NOT NULL,
    issuer TEXT, issued TEXT, applies_to TEXT, kind TEXT NOT NULL,
    sha256 TEXT NOT NULL, n_pages INTEGER, parsed_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS blocks (
    doc_id TEXT NOT NULL REFERENCES documents(doc_id), seq INTEGER NOT NULL,
    kind TEXT NOT NULL, level INTEGER, text TEXT NOT NULL,
    page INTEGER, page_label TEXT, bbox_json TEXT, section_path_json TEXT NOT NULL,
    PRIMARY KEY (doc_id, seq)
);
"""


@dataclass
class IngestReport:
    parsed: list[str] = field(default_factory=list)
    unchanged: list[str] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)


def init_db(db: Path) -> sqlite3.Connection:
    db.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db)
    conn.executescript(SCHEMA)
    return conn


def ingest(sources: list[Source], db: Path = DB_PATH) -> IngestReport:
    report = IngestReport()
    conn = init_db(db)
    for src in sources:
        path = raw_path(src)
        if not path.exists():
            report.missing.append(src.doc_id)
            continue
        sha = sha256_of(path)
        row = conn.execute("SELECT sha256 FROM documents WHERE doc_id=?", (src.doc_id,)).fetchone()
        if row and row[0] == sha:
            report.unchanged.append(src.doc_id)
            continue
        blocks = parse_document(path, src.doc_id, src.kind)
        pages = [b.page for b in blocks if b.page is not None]
        with conn:  # 一个事务：先删旧块再写新块，中途失败不会留下半份文档
            conn.execute("DELETE FROM blocks WHERE doc_id=?", (src.doc_id,))
            conn.execute(
                "INSERT OR REPLACE INTO documents VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (
                    src.doc_id,
                    src.school,
                    src.title,
                    src.url,
                    src.issuer,
                    src.issued,
                    src.applies_to,
                    src.kind,
                    sha,
                    max(pages) if pages else None,
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
        report.parsed.append(src.doc_id)
    conn.close()
    return report
