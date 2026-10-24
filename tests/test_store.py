import sqlite3

import pytest

from contract_review import sources, store
from contract_review.blocks import Block
from contract_review.sources import Source


def src(doc_id="d1", kind="html"):
    return Source(doc_id, "huaxin", "标题", "https://x", "北师大", "2025", kind)


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(sources, "RAW_DIR", tmp_path / "raw")
    calls = []

    def fake_parse(path, doc_id, kind):
        calls.append(doc_id)
        n = len(path.read_bytes())
        return [Block(doc_id, i, "paragraph", f"段落{i}", page=None) for i in range(n)]

    monkeypatch.setattr(store, "parse_document", fake_parse)
    return tmp_path, calls


def put(tmp_path, s, data: bytes):
    p = tmp_path / "raw" / s.tenant / f"{s.doc_id}.{s.kind}"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(data)


def count(db, doc_id):
    return (
        sqlite3.connect(db)
        .execute("select count(*) from blocks where doc_id=?", (doc_id,))
        .fetchone()[0]
    )


def test_ingest_is_idempotent(env):
    tmp, calls = env
    db = tmp / "t.db"
    put(tmp, src(), b"abc")
    assert store.ingest([src()], db).parsed == ["d1"]
    report = store.ingest([src()], db)
    assert report.parsed == [] and report.unchanged == ["d1"] and count(db, "d1") == 3


def test_changed_file_is_reparsed(env):
    tmp, _ = env
    db = tmp / "t.db"
    put(tmp, src(), b"abc")
    store.ingest([src()], db)
    put(tmp, src(), b"abcde")
    assert store.ingest([src()], db).parsed == ["d1"]
    assert count(db, "d1") == 5


def test_missing_file_reported(env):
    tmp, calls = env
    report = store.ingest([src("nofile")], tmp / "t.db")
    assert report.missing == ["nofile"] and calls == []


def test_document_metadata_stored(env):
    tmp, _ = env
    db = tmp / "t.db"
    put(tmp, src(), b"ab")
    store.ingest([src()], db)
    row = sqlite3.connect(db).execute("select tenant, title, url, sha256 from documents").fetchone()
    assert row[:3] == ("huaxin", "标题", "https://x") and len(row[3]) == 64


def test_pdf_text_layer_recorded(tmp_path, monkeypatch):
    from pathlib import Path

    monkeypatch.setattr(sources, "RAW_DIR", tmp_path / "raw")
    monkeypatch.setattr(store, "parse_document", lambda p, d, k: [])
    s = src("pdf1", kind="pdf")
    put(tmp_path, s, (Path(__file__).parent / "fixtures" / "upc_sample.pdf").read_bytes())
    store.ingest([s], tmp_path / "t.db")
    rows = (
        sqlite3.connect(tmp_path / "t.db")
        .execute("select page, text_chars from pages where doc_id='pdf1' order by page")
        .fetchall()
    )
    assert [p for p, _ in rows] == [1, 2, 3] and all(n > 100 for _, n in rows)


def test_parse_error_is_reported_not_raised(env, monkeypatch):
    tmp, _ = env
    put(tmp, src("bad"), b"x")
    put(tmp, src("good"), b"ab")

    def flaky(path, doc_id, kind):
        if doc_id == "bad":
            raise RuntimeError("坏文件")
        return [Block(doc_id, 0, "paragraph", "段落")]

    monkeypatch.setattr(store, "parse_document", flaky)
    report = store.ingest([src("bad"), src("good")], tmp / "t.db")
    assert list(report.failed) == ["bad"] and "坏文件" in report.failed["bad"]
    assert report.parsed == ["good"]


def test_force_reparses_unchanged(env):
    tmp, calls = env
    db = tmp / "t.db"
    put(tmp, src(), b"abc")
    store.ingest([src()], db)
    assert store.ingest([src()], db, force=True).parsed == ["d1"]
