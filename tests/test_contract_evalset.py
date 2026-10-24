import sqlite3
from collections import Counter

import pytest

from contract_review.config import DB_PATH, LEDGER_DB, ROOT
from contract_review.contract_evalset import RISK_TYPES, load_contracts


@pytest.fixture(scope="module")
def cs():
    return load_contracts()


def test_counts_and_ids(cs):
    assert len(cs) == 48 and len({c.id for c in cs}) == 48
    assert sum(1 for c in cs if c.gold) == 36 and sum(1 for c in cs if c.clarify) == 4


def test_every_risk_type_has_six_scored(cs):
    by_type = Counter(g["type"] for c in cs if not c.clarify for g in c.gold)
    assert set(by_type) == set(RISK_TYPES) and min(by_type.values()) >= 6


def test_gold_clauses_exist_in_text(cs):
    for c in cs:
        for g in c.gold + c.borderline + c.favorable:
            if g["clause"] is not None:
                assert f"\n{g['clause']} " in c.text, (c.id, g["clause"])


def test_missing_clause_gold_really_missing(cs):
    for c in cs:
        for g in c.gold:
            if g["clause"] is None:
                assert not any(line.endswith(" 保密") for line in c.text.splitlines()), c.id


def test_clarify_contracts_hide_parties(cs):
    for c in cs:
        assert (c.our_name in c.text) != c.clarify, c.id


@pytest.mark.skipif(not DB_PATH.exists(), reason="依据库未入库")
def test_basis_articles_exist_in_corpus(cs):
    conn = sqlite3.connect(DB_PATH)
    missing = set()
    for c in cs:
        for g in c.gold + c.borderline:
            for doc_id, article in g["basis"]:
                hit = conn.execute(
                    "select 1 from blocks where doc_id=? and text like ? limit 1",
                    (doc_id, f"{article}%"),
                ).fetchone()
                if not hit:
                    missing.add((doc_id, article))
    assert missing == set()


@pytest.mark.skipif(not LEDGER_DB.exists(), reason="台账库未生成")
def test_unit_price_ratio_against_ledger(cs):
    conn = sqlite3.connect(LEDGER_DB)
    for c in cs:
        risky = {g["type"] for g in c.gold} | {b["type"] for b in c.borderline}
        line = c.text.split("品名：")[1].split("\n")[0]
        item = line.split("；")[0]
        price = float(line.split("单价：人民币")[1].split("元")[0])
        (avg,) = conn.execute(
            "select avg(unit_price_yuan) from purchase_history where tenant=? and item=?",
            (c.tenant, item),
        ).fetchone()
        ratio = price / avg
        if "unit_price" in {g["type"] for g in c.gold}:
            assert ratio >= 1.25, c.id
        elif "unit_price" in risky:
            assert 1.08 < ratio < 1.15, c.id
        elif c.our_side == "甲":
            assert abs(ratio - 1) <= 0.035, (c.id, ratio)


def test_sample_covers_every_subset(cs):
    from contract_review.contract_evalset import sample_contracts

    s = sample_contracts(cs, 20)
    assert len(s) == 20 and len({c.id for c in s}) == 20
    assert any(c.clarify for c in s) and any(not c.gold for c in s) and any(c.borderline for c in s)
    assert any(c.favorable for c in s)
    assert {c.contract_type for c in s} == {c.contract_type for c in cs}
    assert sample_contracts(cs, 20) == s


def test_acceptable_clauses_exist(cs):
    for c in cs:
        for a in c.acceptable:
            for no in a["clauses"]:
                assert no is None or f"\n{no} " in c.text, (c.id, no)


V2 = ROOT / "evals" / "contracts_v2.jsonl"


@pytest.fixture(scope="module")
def v2():
    return load_contracts(V2)


def test_v2_counts_and_categories(v2):
    # ADR 013：60 份长合同，30 份植入 75 处风险，5 类数量固定；干净合同都带干扰项
    assert len(v2) == 60 and len({c.id for c in v2}) == 60
    risky = [c for c in v2 if c.gold]
    assert len(risky) == 30 and all(2 <= len(c.gold) <= 3 for c in risky)
    cats = Counter(g["category"] for c in risky for g in c.gold)
    assert cats == {"hidden": 18, "paraphrase": 18, "combo": 12, "threshold": 15, "standard": 12}
    assert all(c.distractors and not c.gold for c in v2 if not c.gold)
    assert sum(1 for c in risky for g in c.gold if len(g["clauses"]) > 1) >= 10


def test_v2_contracts_are_long_and_gold_clauses_exist(v2):
    for c in v2:
        n = sum(1 for line in c.text.splitlines() if line.startswith("第") and "条 " in line[:8])
        assert 25 <= n <= 30, (c.id, n)
        for g in c.gold:
            assert g["why"] and g["category"]
            for no in g["clauses"]:
                assert f"\n{no} " in c.text, (c.id, no)
        for d in c.distractors:
            assert f"\n{d['clause']} " in c.text, (c.id, d)


@pytest.mark.skipif(not DB_PATH.exists(), reason="依据库未入库")
def test_v2_basis_articles_exist_in_corpus(v2):
    conn = sqlite3.connect(DB_PATH)
    missing = {
        (doc_id, article)
        for c in v2
        for g in c.gold
        for doc_id, article in g["basis"]
        if not conn.execute(
            "select 1 from blocks where doc_id=? and text like ? limit 1",
            (doc_id, f"{article}%"),
        ).fetchone()
    }
    assert missing == set()
