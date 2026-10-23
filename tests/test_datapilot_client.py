import sqlite3

import pytest

from docpilot.datapilot_client import DATAPILOT_DIR, LocalBackend, MCPBackend, link_datapilot


@pytest.fixture
def db(tmp_path):
    p = tmp_path / "campus.sqlite"
    conn = sqlite3.connect(p)
    conn.execute("create table t (school text, v int)")
    conn.executemany("insert into t values (?,?)", [("bnu", 1), ("upc", 2)])
    conn.commit()
    return p


def test_local_backend_select(db):
    res = LocalBackend(db).run("select school, v from t order by v")
    assert res["columns"] == ["school", "v"] and res["rows"] == [["bnu", 1], ["upc", 2]]


@pytest.mark.parametrize(
    "sql", ["delete from t", "select 1; drop table t", "update t set v=0", "attach 'x' as y"]
)
def test_local_backend_rejects_writes(db, sql):
    res = LocalBackend(db).run(sql)
    assert "error" in res
    assert sqlite3.connect(db).execute("select count(*) from t").fetchone()[0] == 2


def test_local_backend_reports_sql_errors(db):
    assert "error" in LocalBackend(db).run("select nope from t")


def test_link_is_idempotent(tmp_path, monkeypatch):
    import docpilot.datapilot_client as dc

    monkeypatch.setattr(dc, "DATAPILOT_DIR", tmp_path / "datapilot")
    (tmp_path / "datapilot" / "data" / "dev_databases").mkdir(parents=True)
    target = link_datapilot()
    assert link_datapilot() == target and target.is_symlink()


@pytest.mark.skipif(not (DATAPILOT_DIR / "src").exists(), reason="本机没有 DataPilot")
def test_mcp_backend_roundtrip():
    link_datapilot()
    backend = MCPBackend()
    try:
        res = backend.run("select count(*) as n from national_aid")
        assert res["rows"][0][0] >= 1
        assert "error" in backend.run("delete from national_aid")
    finally:
        backend.close()
