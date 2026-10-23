"""trace 落盘与读取：runs/traces/<日期>/<trace_id>.json，一题一个文件，D5 回放用。"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from contract_review.config import ROOT

TRACE_DIR = ROOT / "runs" / "traces"


def save_trace(result, directory: Path | None = None) -> Path:
    d = directory or TRACE_DIR / f"{datetime.now():%Y%m%d}"
    d.mkdir(parents=True, exist_ok=True)
    path = d / f"{result.trace['trace_id']}.json"
    path.write_text(json.dumps(result.trace, ensure_ascii=False, indent=1, default=str), "utf-8")
    return path


def load_trace(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))
