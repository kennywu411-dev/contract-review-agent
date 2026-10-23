"""文件清单（data/sources.toml）与下载。

清单是人写的、入 git；下载得到的 sha256 和日期写在 data/sources.lock.json，
避免脚本改写人维护的 TOML。
"""

from __future__ import annotations

import hashlib
import json
import tomllib
import urllib.request
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Literal

from contract_review.config import DATA_DIR, RAW_DIR, ROOT, SOURCES_PATH, TENANTS

LOCK_PATH = DATA_DIR / "sources.lock.json"
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/130 Safari/537.36"
KINDS = ("pdf", "html", "docx", "txt")
# 构造文件（租户规范等）放在仓库里，url 写 repo:<相对路径>
REPO_PREFIX = "repo:"
# 反爬站常返回 200 + HTML 页面；按文件头校验，避免把挑战页存成 .pdf
MAGIC = {"pdf": b"%PDF", "docx": b"PK\x03\x04"}


@dataclass(frozen=True)
class Source:
    doc_id: str
    tenant: str
    title: str
    url: str
    issuer: str
    issued: str
    kind: Literal["pdf", "html", "docx", "txt"]
    manual: bool = False
    applies_to: str | None = None
    sha256: str | None = None


@dataclass
class FetchReport:
    downloaded: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    missing_manual: list[str] = field(default_factory=list)
    failed: list[str] = field(default_factory=list)
    sha256: dict[str, str] = field(default_factory=dict)


def load_sources(path: Path = SOURCES_PATH, lock_path: Path | None = None) -> list[Source]:
    entries = tomllib.loads(path.read_text(encoding="utf-8")).get("source", [])
    lock_path = lock_path if lock_path is not None else path.with_name("sources.lock.json")
    lock = json.loads(lock_path.read_text(encoding="utf-8")) if lock_path.exists() else {}
    seen: set[str] = set()
    out = []
    for e in entries:
        doc_id = e["doc_id"]
        if doc_id in seen:
            raise ValueError(f"重复的 doc_id: {doc_id}")
        if e["tenant"] not in TENANTS:
            raise ValueError(f"未知租户 {e['tenant']}（{doc_id}）")
        if e["kind"] not in KINDS:
            raise ValueError(f"未知文件类型 {e['kind']}（{doc_id}）")
        seen.add(doc_id)
        sha = lock.get(doc_id, {}).get("sha256")
        out.append(Source(**e, sha256=sha))
    return out


def raw_path(src: Source) -> Path:
    return RAW_DIR / src.tenant / f"{src.doc_id}.{src.kind}"


def sha256_of(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def fetch_all(sources: list[Source], opener=urllib.request.urlopen) -> FetchReport:
    """下载缺失的文件；已存在且 sha 一致的跳过；失败不中断，汇总在报告里。"""
    report = FetchReport()
    for src in sources:
        path = raw_path(src)
        if path.exists() and (src.manual or src.sha256 in (None, sha256_of(path))):
            report.skipped.append(src.doc_id)
            report.sha256[src.doc_id] = sha256_of(path)
            continue
        if src.manual:
            report.missing_manual.append(src.doc_id)
            continue
        try:
            local = src.url.startswith(REPO_PREFIX)
            data = _read_repo(src.url) if local else _download(src, opener)
        except (OSError, ValueError):
            report.failed.append(src.doc_id)
            continue
        magic = MAGIC.get(src.kind)
        if magic and not data.startswith(magic):
            report.failed.append(src.doc_id)
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        report.downloaded.append(src.doc_id)
        report.sha256[src.doc_id] = hashlib.sha256(data).hexdigest()
    return report


def _download(src: Source, opener) -> bytes:
    req = urllib.request.Request(src.url, headers={"User-Agent": UA})
    return opener(req, timeout=30).read()


def _read_repo(url: str) -> bytes:
    root = ROOT.resolve()
    path = (root / url.removeprefix(REPO_PREFIX)).resolve()
    if not path.is_relative_to(root):
        raise ValueError(f"repo: 路径越出仓库：{url}")
    return path.read_bytes()


def write_lock(report: FetchReport, lock_path: Path = LOCK_PATH) -> None:
    lock = json.loads(lock_path.read_text(encoding="utf-8")) if lock_path.exists() else {}
    today = date.today().isoformat()
    for doc_id, sha in report.sha256.items():
        old = lock.get(doc_id, {})
        if old.get("sha256") != sha:
            lock[doc_id] = {"sha256": sha, "downloaded_at": today}
    lock_path.write_text(json.dumps(lock, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
