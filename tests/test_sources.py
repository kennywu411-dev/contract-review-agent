import hashlib
import io
from pathlib import Path
from urllib.error import URLError

import pytest

from contract_review import sources
from contract_review.sources import Source, fetch_all, load_sources

TOML_ONE = """
[[source]]
doc_id = "bnu-handbook"
tenant = "huaxin"
title = "北京师范大学本科学生手册（2025）"
url = "https://example.edu/a.pdf"
issuer = "北京师范大学"
issued = "2025-09"
kind = "pdf"
"""


def write(tmp_path: Path, text: str) -> Path:
    p = tmp_path / "sources.toml"
    p.write_text(text, encoding="utf-8")
    return p


def make_source(**kw) -> Source:
    base = dict(
        doc_id="d1",
        tenant="huaxin",
        title="t",
        url="https://example.edu/d1.pdf",
        issuer="i",
        issued="2025",
        kind="pdf",
        manual=False,
        applies_to=None,
        sha256=None,
    )
    base.update(kw)
    return Source(**base)


def test_load_sources_defaults(tmp_path):
    [s] = load_sources(write(tmp_path, TOML_ONE))
    assert s.doc_id == "bnu-handbook" and s.manual is False and s.sha256 is None


def test_load_sources_rejects_duplicate_ids(tmp_path):
    with pytest.raises(ValueError, match="bnu-handbook"):
        load_sources(write(tmp_path, TOML_ONE + TOML_ONE))


def test_load_sources_rejects_unknown_school(tmp_path):
    with pytest.raises(ValueError, match="pku"):
        load_sources(write(tmp_path, TOML_ONE.replace('"huaxin"', '"pku"')))


@pytest.fixture
def raw(tmp_path, monkeypatch):
    monkeypatch.setattr(sources, "RAW_DIR", tmp_path / "raw")
    return tmp_path / "raw"


def fake_opener(payload: bytes, calls: list):
    def opener(req, timeout=None):
        calls.append(req.full_url)
        return io.BytesIO(payload)

    return opener


def test_fetch_downloads_and_records_sha(raw):
    calls = []
    report = fetch_all([make_source()], opener=fake_opener(b"%PDF-1", calls))
    assert report.downloaded == ["d1"]
    assert (raw / "huaxin" / "d1.pdf").read_bytes() == b"%PDF-1"
    assert report.sha256["d1"] == hashlib.sha256(b"%PDF-1").hexdigest()


def test_fetch_skips_existing(raw):
    path = raw / "huaxin" / "d1.pdf"
    path.parent.mkdir(parents=True)
    path.write_bytes(b"x")
    src = make_source(sha256=hashlib.sha256(b"x").hexdigest())
    calls = []
    report = fetch_all([src], opener=fake_opener(b"y", calls))
    assert calls == [] and report.skipped == ["d1"]


def test_missing_manual_file_reported(raw):
    report = fetch_all([make_source(manual=True)], opener=fake_opener(b"", []))
    assert report.missing_manual == ["d1"] and report.failed == []


def test_failed_download_reported(raw):
    def boom(req, timeout=None):
        if "bad" in req.full_url:
            raise URLError("down")
        return io.BytesIO(b"%PDF-ok")

    srcs = [make_source(doc_id="bad", url="https://x/bad.pdf"), make_source(doc_id="good")]
    report = fetch_all(srcs, opener=boom)
    assert report.failed == ["bad"] and report.downloaded == ["good"]


def test_manual_file_replaced_by_hand_is_accepted(raw):
    path = raw / "huaxin" / "d1.pdf"
    path.parent.mkdir(parents=True)
    path.write_bytes(b"new")
    src = make_source(manual=True, sha256="old-sha")
    report = fetch_all([src], opener=fake_opener(b"", []))
    assert report.skipped == ["d1"] and report.sha256["d1"] == hashlib.sha256(b"new").hexdigest()


def test_html_saved_as_pdf_is_rejected(raw):
    report = fetch_all([make_source()], opener=fake_opener(b"<html>blocked</html>", []))
    assert report.failed == ["d1"] and not (raw / "huaxin" / "d1.pdf").exists()


def test_docx_magic_accepted(raw):
    report = fetch_all([make_source(kind="docx")], opener=fake_opener(b"PK\x03\x04..", []))
    assert report.downloaded == ["d1"]


def test_repo_url_copies_local_file(raw, tmp_path, monkeypatch):
    # 构造的租户规范放在仓库里，url 写 repo:<相对路径>，下载时直接复制，不联网
    monkeypatch.setattr(sources, "ROOT", tmp_path)
    (tmp_path / "data" / "tenants").mkdir(parents=True)
    (tmp_path / "data" / "tenants" / "p.txt").write_text("第一条 规范。", encoding="utf-8")
    calls = []
    src = make_source(kind="txt", url="repo:data/tenants/p.txt")
    report = fetch_all([src], opener=fake_opener(b"", calls))
    assert calls == [] and report.downloaded == ["d1"]
    assert (raw / "huaxin" / "d1.txt").read_text(encoding="utf-8") == "第一条 规范。"


def test_repo_url_cannot_escape_root(raw, tmp_path, monkeypatch):
    monkeypatch.setattr(sources, "ROOT", tmp_path / "repo")
    (tmp_path / "secret.txt").write_text("x", encoding="utf-8")
    report = fetch_all(
        [make_source(kind="txt", url="repo:../secret.txt")], opener=fake_opener(b"", [])
    )
    assert report.failed == ["d1"]
