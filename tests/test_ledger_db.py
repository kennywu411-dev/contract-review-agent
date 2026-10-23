import sqlite3

import pytest

from contract_review.ledger_db import REQUIRED_COLUMNS, build_ledger_db


@pytest.fixture
def db(tmp_path):
    csv_dir = tmp_path / "csv"
    csv_dir.mkdir()
    (csv_dir / "items.csv").write_text(
        "tenant,source,name,amount_yuan,rate,note\n"
        "huaxin,构造,甲,10000,0.5,a\nyunqi,构造,乙,6000,1,\n",
        encoding="utf-8",
    )
    out = tmp_path / "o.sqlite"
    assert build_ledger_db(csv_dir, out) == {"items": 2}
    return out


def test_required_columns_kept(db):
    cols = [r[1] for r in sqlite3.connect(db).execute("pragma table_info(items)")]
    assert set(REQUIRED_COLUMNS) <= set(cols)


def test_numeric_columns_are_numbers(db):
    conn = sqlite3.connect(db)
    assert conn.execute("select typeof(amount_yuan), typeof(rate) from items").fetchone() == (
        "integer",
        "real",
    )


def test_empty_cell_is_null(db):
    conn = sqlite3.connect(db)
    assert conn.execute("select note from items where name='乙'").fetchone() == (None,)


def test_missing_required_column_rejected(tmp_path):
    (tmp_path / "bad.csv").write_text("tenant,name\nhuaxin,x\n", encoding="utf-8")
    with pytest.raises(ValueError, match="source"):
        build_ledger_db(tmp_path, tmp_path / "o.sqlite")
