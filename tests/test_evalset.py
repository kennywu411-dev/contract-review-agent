import json
import random
import sqlite3
from collections import Counter

import pytest

from contract_review.config import DB_PATH, ROOT
from contract_review.evalset import QUESTIONS_PATH, Question, load_questions

CAMPUS_SOURCES = ROOT / "archive" / "campus" / "sources.toml"


def write(tmp_path, rows):
    p = tmp_path / "q.jsonl"
    p.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows), encoding="utf-8")
    return p


GOOD = {
    "id": "huaxin-001",
    "tenant": "huaxin",
    "question": "缓考怎么申请",
    "type": "answerable",
    "subtype": None,
    "source_type": "doc",
    "source_url": None,
    "gold": [{"doc_id": "bnu-handbook-2025", "page": 166, "clause": None}],
    "answer_hint": "考前书面申请",
    "note": "",
}


def test_load_valid(tmp_path):
    [q] = load_questions(write(tmp_path, [GOOD]))
    assert isinstance(q, Question) and q.gold[0]["page"] == 166


@pytest.mark.parametrize(
    "patch,msg",
    [
        ({"type": "maybe"}, "type"),
        ({"tenant": "pku"}, "tenant"),
        ({"source_type": "public", "source_url": None}, "source_url"),
        ({"type": "refuse"}, "gold"),
        ({"gold": []}, "gold"),
        ({"subtype": "weird"}, "subtype"),
        ({"subtyp": "version"}, "未知字段"),
        ({"id": "yunqi-001"}, "id"),
        ({"gold": [{"doc_id": "bnu-handbook-2025", "page": "166", "clause": None}]}, "page"),
        ({"gold": [{"doc_id": "x", "page": None, "clause": ""}]}, "gold"),
    ],
)
def test_load_rejects_invalid(tmp_path, patch, msg):
    with pytest.raises(ValueError, match=msg):
        load_questions(write(tmp_path, [GOOD | patch]))


def test_load_rejects_duplicate_ids(tmp_path):
    with pytest.raises(ValueError, match="huaxin-001"):
        load_questions(write(tmp_path, [GOOD, GOOD]))


# ---- 以下检查真实评测集 ----
def _campus_sources():
    # 存档清单里的 tenant 是学校 id，绕过新租户表的校验，直接读取
    import tomllib

    from contract_review.sources import Source

    rows = tomllib.loads(CAMPUS_SOURCES.read_text(encoding="utf-8"))["source"]
    out = []
    for r in rows:
        r = dict(r)
        r["tenant"] = r.pop("school", r.get("tenant"))
        out.append(Source(**r))
    return out


def _campus_db_present() -> bool:
    try:
        conn = sqlite3.connect(DB_PATH)
        return bool(
            conn.execute("select 1 from documents where doc_id like 'bnu-%' limit 1").fetchone()
        )
    except sqlite3.Error:
        return False


real = pytest.mark.skipif(not QUESTIONS_PATH.exists(), reason="评测集未生成")
# 依赖高校文档入库内容的检查：ADR 007 后库里换成合同依据库，这些只在恢复高校数据时运行
campus_db = pytest.mark.skipif(not _campus_db_present(), reason="高校场景数据已存档（ADR 007）")


@pytest.fixture(scope="module")
def qs():
    return load_questions()


@real
@campus_db
def test_gold_doc_ids_exist(qs):
    known = {s.doc_id for s in _campus_sources()}
    assert {g["doc_id"] for q in qs for g in q.gold if "doc_id" in g} <= known


@real
@campus_db
def test_gold_pages_exist_in_blocks(qs):
    conn = sqlite3.connect(DB_PATH)
    bad = []
    for q in qs:
        for g in q.gold:
            if "doc_id" not in g:
                continue
            if g.get("page") is not None:
                sql, args = (
                    "select count(*) from blocks where doc_id=? and page=?",
                    (
                        g["doc_id"],
                        g["page"],
                    ),
                )
            else:
                sql, args = (
                    "select count(*) from blocks where doc_id=? and text like ?",
                    (
                        g["doc_id"],
                        f"{g['clause']}%",
                    ),
                )
            if conn.execute(sql, args).fetchone()[0] == 0:
                bad.append((q.id, g))
    assert bad == []


@real
@campus_db
def test_gold_school_matches_question_school(qs):
    tenant = {s.doc_id: s.tenant for s in _campus_sources()}
    bad = [
        q.id
        for q in qs
        for g in q.gold
        if "doc_id" in g and tenant[g["doc_id"]] not in (q.tenant, "common", "national")
    ]
    assert bad == []


@real
def test_distribution_targets(qs):
    types = Counter(q.type for q in qs)
    subs = Counter(q.subtype for q in qs)
    tenants = Counter(q.tenant for q in qs)
    assert len(qs) >= 100
    assert types["answerable"] >= 55 and types["table"] >= 15 and types["refuse"] >= 20
    assert subs["cross_school"] >= 5 and subs["version"] >= 3
    assert subs["conflict"] >= 3 and subs["clarify"] >= 4
    assert tenants["bnu"] >= 25 and tenants["upc"] >= 25


@real
def test_real_source_ratio_reported(qs):
    real_n = sum(q.source_type in ("faq", "public") for q in qs)
    print(f"\n真实来源占比：{real_n}/{len(qs)} = {real_n / len(qs):.0%}")


def test_spot_check_sample_is_deterministic():
    from contract_review.evalset import spot_check_sample

    items = [GOOD | {"id": f"huaxin-{i:03d}"} for i in range(10)]
    qs_ = [Question(**r) for r in items]
    a, b = spot_check_sample(qs_, 0.2), spot_check_sample(qs_, 0.2)
    assert [q.id for q in a] == [q.id for q in b] and len(a) == 2
    random.seed(1)  # 不受全局随机状态影响
    assert [q.id for q in spot_check_sample(qs_, 0.2)] == [q.id for q in a]


@real
@campus_db
def test_table_gold_queries_return_rows(qs):
    import sqlite3 as sq

    from contract_review.config import LEDGER_DB

    conn = sq.connect(LEDGER_DB)
    bad = []
    for q in qs:
        for g in q.gold:
            if "table" in g:
                n = conn.execute(
                    f"select count(*) from {g['table']} where {g['where']}"
                ).fetchone()[0]
                if n == 0:
                    bad.append(q.id)
    assert bad == []
