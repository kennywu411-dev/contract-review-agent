import json
import random
import sqlite3
from collections import Counter

import pytest

from docpilot.config import DB_PATH
from docpilot.evalset import QUESTIONS_PATH, Question, load_questions
from docpilot.sources import load_sources


def write(tmp_path, rows):
    p = tmp_path / "q.jsonl"
    p.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows), encoding="utf-8")
    return p


GOOD = {
    "id": "bnu-001",
    "school": "bnu",
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
        ({"school": "pku"}, "school"),
        ({"source_type": "public", "source_url": None}, "source_url"),
        ({"type": "refuse"}, "gold"),
        ({"gold": []}, "gold"),
        ({"subtype": "weird"}, "subtype"),
        ({"subtyp": "version"}, "未知字段"),
        ({"id": "upc-001"}, "id"),
        ({"gold": [{"doc_id": "bnu-handbook-2025", "page": "166", "clause": None}]}, "page"),
        ({"gold": [{"doc_id": "x", "page": None, "clause": ""}]}, "gold"),
    ],
)
def test_load_rejects_invalid(tmp_path, patch, msg):
    with pytest.raises(ValueError, match=msg):
        load_questions(write(tmp_path, [GOOD | patch]))


def test_load_rejects_duplicate_ids(tmp_path):
    with pytest.raises(ValueError, match="bnu-001"):
        load_questions(write(tmp_path, [GOOD, GOOD]))


# ---- 以下检查真实评测集 ----
real = pytest.mark.skipif(not QUESTIONS_PATH.exists(), reason="评测集未生成")


@pytest.fixture(scope="module")
def qs():
    return load_questions()


@real
def test_gold_doc_ids_exist(qs):
    known = {s.doc_id for s in load_sources()}
    assert {g["doc_id"] for q in qs for g in q.gold if "doc_id" in g} <= known


@real
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
def test_gold_school_matches_question_school(qs):
    school = {s.doc_id: s.school for s in load_sources()}
    bad = [
        q.id
        for q in qs
        for g in q.gold
        if "doc_id" in g and school[g["doc_id"]] not in (q.school, "national")
    ]
    assert bad == []


@real
def test_distribution_targets(qs):
    types = Counter(q.type for q in qs)
    subs = Counter(q.subtype for q in qs)
    schools = Counter(q.school for q in qs)
    assert len(qs) >= 100
    assert types["answerable"] >= 55 and types["table"] >= 15 and types["refuse"] >= 20
    assert subs["cross_school"] >= 5 and subs["version"] >= 3
    assert subs["conflict"] >= 3 and subs["clarify"] >= 4
    assert schools["bnu"] >= 25 and schools["upc"] >= 25


@real
def test_real_source_ratio_reported(qs):
    real_n = sum(q.source_type in ("faq", "public") for q in qs)
    print(f"\n真实来源占比：{real_n}/{len(qs)} = {real_n / len(qs):.0%}")


def test_spot_check_sample_is_deterministic():
    from docpilot.evalset import spot_check_sample

    items = [GOOD | {"id": f"bnu-{i:03d}"} for i in range(10)]
    qs_ = [Question(**r) for r in items]
    a, b = spot_check_sample(qs_, 0.2), spot_check_sample(qs_, 0.2)
    assert [q.id for q in a] == [q.id for q in b] and len(a) == 2
    random.seed(1)  # 不受全局随机状态影响
    assert [q.id for q in spot_check_sample(qs_, 0.2)] == [q.id for q in a]


@real
def test_table_gold_queries_return_rows(qs):
    import sqlite3 as sq

    from docpilot.config import CAMPUS_DB

    conn = sq.connect(CAMPUS_DB)
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
