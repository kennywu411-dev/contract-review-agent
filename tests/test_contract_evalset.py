import sqlite3
from collections import Counter

import pytest

from contract_review.config import DB_PATH, LEDGER_DB
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
                assert "保密" not in c.text.split("订立本合同。")[1], c.id


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
