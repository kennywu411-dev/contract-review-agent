"""供应商台账库：data/ledger_src/*.csv → data/ledger/ledger.sqlite（供 DataPilot 只读查询）。

台账是构造的（ADR 007）。每张表都必须带 tenant（租户隔离）和 source（数据出处，如"构造"）两列；
目录布局遵循 DataPilot 的 <db_id>/<db_id>.sqlite 约定，db_id=ledger。
"""

from __future__ import annotations

import csv
import re
import sqlite3
from pathlib import Path

from contract_review.config import LEDGER_DB

REQUIRED_COLUMNS = ("tenant", "source")
_INT = re.compile(r"^-?\d+$")
_REAL = re.compile(r"^-?\d+\.\d+$")


def _column_type(values: list[str]) -> str:
    present = [v for v in values if v != ""]
    if present and all(_INT.match(v) for v in present):
        return "INTEGER"
    if present and all(_INT.match(v) or _REAL.match(v) for v in present):
        return "REAL"
    return "TEXT"


def _convert(value: str, typ: str):
    if value == "":
        return None
    return int(value) if typ == "INTEGER" else float(value) if typ == "REAL" else value


def build_ledger_db(csv_dir: Path, out: Path = LEDGER_DB) -> dict[str, int]:
    """每次整库重建，返回 表名 → 行数。"""
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_suffix(".tmp")
    tmp.unlink(missing_ok=True)
    conn = sqlite3.connect(tmp)
    counts: dict[str, int] = {}
    for path in sorted(csv_dir.glob("*.csv")):
        with path.open(encoding="utf-8") as fh:
            rows = list(csv.reader(fh))
        header, data = rows[0], rows[1:]
        missing = [c for c in REQUIRED_COLUMNS if c not in header]
        if missing:
            conn.close()
            tmp.unlink()
            raise ValueError(f"{path.name} 缺少列：{', '.join(missing)}")
        types = [_column_type([r[i] for r in data]) for i in range(len(header))]
        cols = ", ".join(f'"{h}" {t}' for h, t in zip(header, types, strict=True))
        conn.execute(f'CREATE TABLE "{path.stem}" ({cols})')
        conn.executemany(
            f'INSERT INTO "{path.stem}" VALUES ({", ".join("?" * len(header))})',
            [[_convert(v, t) for v, t in zip(r, types, strict=True)] for r in data],
        )
        counts[path.stem] = len(data)
    conn.commit()
    conn.close()
    tmp.replace(out)
    return counts
