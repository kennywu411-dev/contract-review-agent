import csv
import sqlite3
from pathlib import Path

import pytest

from docpilot.campus_db import REQUIRED_COLUMNS, build_campus_db
from docpilot.sources import load_sources

CSV_DIR = Path(__file__).parents[1] / "data" / "structured"


@pytest.fixture(scope="module")
def db(tmp_path_factory):
    out = tmp_path_factory.mktemp("campus") / "campus.sqlite"
    counts = build_campus_db(CSV_DIR, out)
    assert counts and all(n > 0 for n in counts.values())
    return out


def tables(db):
    conn = sqlite3.connect(db)
    return [r[0] for r in conn.execute("select name from sqlite_master where type='table'")]


def test_every_row_has_source(db):
    conn = sqlite3.connect(db)
    for t in tables(db):
        for col in REQUIRED_COLUMNS:
            nulls = conn.execute(f"select count(*) from {t} where {col} is null or {col}=''")
            assert nulls.fetchone()[0] == 0, (t, col)


def test_source_doc_ids_exist(db):
    known = {s.doc_id for s in load_sources()}
    conn = sqlite3.connect(db)
    for t in tables(db):
        ids = {r[0] for r in conn.execute(f"select distinct source_doc_id from {t}")}
        assert ids <= known, (t, ids - known)


def test_national_amounts(db):
    conn = sqlite3.connect(db)
    rows = dict(
        conn.execute("select name, amount_yuan from national_aid where stage='本专科'").fetchall()
    )
    assert rows["国家奖学金"] == 10000 and rows["国家励志奖学金"] == 6000


def test_numeric_columns_are_numbers(db):
    conn = sqlite3.connect(db)
    (typ,) = conn.execute("select typeof(amount_yuan) from scholarships limit 1").fetchone()
    assert typ == "integer"


def test_missing_required_column_rejected(tmp_path):
    (tmp_path / "bad.csv").write_text("school,name\nbnu,x\n", encoding="utf-8")
    with pytest.raises(ValueError, match="source_doc_id"):
        build_campus_db(tmp_path, tmp_path / "o.sqlite")


def test_csv_headers_documented():
    for f in CSV_DIR.glob("*.csv"):
        with f.open(encoding="utf-8") as fh:
            header = next(csv.reader(fh))
        assert set(REQUIRED_COLUMNS) <= set(header), f.name
