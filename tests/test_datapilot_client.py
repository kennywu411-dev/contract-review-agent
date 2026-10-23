import sqlite3

import pytest

from contract_review.datapilot_client import DATAPILOT_DIR, LocalBackend, MCPBackend, link_datapilot


@pytest.fixture
def db(tmp_path):
    p = tmp_path / "campus.sqlite"
    conn = sqlite3.connect(p)
    conn.execute("create table t (tenant text, v int)")
    conn.executemany("insert into t values (?,?)", [("huaxin", 1), ("yunqi", 2)])
    conn.commit()
    return p


def test_local_backend_select(db):
    res = LocalBackend(db).run("select tenant, v from t order by v")
    assert res["columns"] == ["tenant", "v"] and res["rows"] == [["huaxin", 1], ["yunqi", 2]]


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
    import contract_review.datapilot_client as dc

    monkeypatch.setattr(dc, "DATAPILOT_DIR", tmp_path / "datapilot")
    (tmp_path / "datapilot" / "data" / "dev_databases").mkdir(parents=True)
    target = link_datapilot()
    assert link_datapilot() == target and target.is_symlink()


@pytest.mark.skipif(not (DATAPILOT_DIR / "src").exists(), reason="本机没有 DataPilot")
def test_mcp_backend_roundtrip():
    link_datapilot()
    backend = MCPBackend()
    try:
        res = backend.run("select count(*) as n from suppliers")
        assert res["rows"][0][0] >= 1
        assert "error" in backend.run("delete from suppliers")
    finally:
        backend.close()


class Flaky:
    name = "datapilot-mcp"

    def __init__(self):
        self.calls = 0
        self.closed = False

    def run(self, sql):
        self.calls += 1
        return {"error": "backend_error", "message": "broken pipe"}

    def close(self):
        self.closed = True


def test_resilient_backend_falls_back_after_repeated_errors(db):
    from contract_review.datapilot_client import ResilientBackend

    primary = Flaky()
    b = ResilientBackend(primary, LocalBackend(db), max_errors=2)
    assert "error" in b.run("select 1")
    res = b.run("select tenant from t")
    assert res["rows"] and b.name == "local(fallback)" and primary.closed
    assert primary.calls == 2


def test_sql_errors_do_not_trigger_fallback(db):
    from contract_review.datapilot_client import ResilientBackend

    class Strict(Flaky):
        def run(self, sql):
            self.calls += 1
            return {"error": "sql_error", "message": "no such column"}

    b = ResilientBackend(Strict(), LocalBackend(db), max_errors=1)
    b.run("select nope from t")
    assert b.name == "datapilot-mcp"
