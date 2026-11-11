"""v3 采购金额测算评测集：结构检查，以及"生成器独立算的标准答案"和 quant.py 的计算逐项一致。"""

import json
import re
from collections import Counter

import pytest

from contract_review import quant
from contract_review.config import ROOT
from contract_review.quant_extract import TOPICS, topic_outcomes

PATHS = [ROOT / "evals" / "contracts_v3.jsonl", ROOT / "evals" / "contracts_v3_holdout.jsonl"]


@pytest.fixture(scope="module", params=PATHS, ids=["v3", "holdout"])
def cs(request):
    return [json.loads(line) for line in request.param.open(encoding="utf-8")]


def test_holdout_shares_no_contract_text_with_v3():
    a, b = ([json.loads(line)["text"] for line in p.open(encoding="utf-8")] for p in PATHS)
    assert not set(a) & set(b)


def test_counts(cs):
    assert len(cs) == 60 and len({c["id"] for c in cs}) == 60
    assert all(c["our_side"] == "甲" and c["constructed"] for c in cs)
    assert Counter(c["variants"]["style"] for c in cs) == {"cn": 30, "ar": 30}
    assert sum(len(c["gold_lines"]) > 1 for c in cs) == 18
    for topic in TOPICS:
        statuses = Counter(c["gold_amounts"][topic]["status"] for c in cs)
        assert statuses["amount"] >= 10 and statuses["none"] >= 4, (topic, statuses)
    assert sum(c["gold_amounts"]["payment"]["status"] == "unquantifiable" for c in cs) >= 6
    assert sum(c["gold_amounts"]["penalty"]["status"] == "unquantifiable" for c in cs) >= 6


def test_every_gold_number_is_written_in_the_contract(cs):
    """标准答案里的每个数字，用一句包含它的原文都能通过核对（否则这个字段永远抄不对）。"""
    for c in cs:
        sentences = [
            s for line in c["text"].splitlines() for s in line.replace("；", "。").split("。") if s
        ]
        for name, value in c["gold_fields"].items():
            assert any(quant.verify_figure(name, value, s, c["text"])[0] for s in sentences), (
                c["id"],
                name,
                value,
            )
        rows = [line for line in c["text"].splitlines() if "品名：" in line]
        assert len(rows) == len(c["gold_lines"])
        quoted = [
            g | {"quote": re.sub(r"^（[一二三四五]）", "", r.split("；合同总额")[0]).rstrip("；")}
            for g, r in zip(c["gold_lines"], rows, strict=True)
        ]
        checked = quant.check_lines(quoted, c["text"], {})
        assert all(x["ok"] for x in checked), (c["id"], checked)


def test_generator_gold_matches_quant_module(cs):
    """两套独立实现（生成器里的直接算式、quant.compute）在 60 份 × 5 项上结果一致。"""
    for c in cs:
        figures = [
            {
                "field": k,
                "label": quant.FIELDS[k][1],
                "value": v,
                "quote": "",
                "clause": None,
                "ok": True,
                "why": "",
            }
            for k, v in c["gold_fields"].items()
        ]
        flags = [{"topic": t, "quote": "", "reason": "构造"} for t in c["gold_uncomputable"]]
        lines = [
            g
            | {"quote": "", "clause": None, "ok": True, "why": ""}
            | {"ledger": c["ledger"][g["item"]] | {"item": g["item"]}}
            for g in c["gold_lines"]
        ]
        items = quant.compute(figures, c["our_side"], quant.load_params(c["tenant"]), lines, flags)
        got = topic_outcomes({"status": "ok", "items": quant.items_to_dicts(items)})
        for topic in TOPICS:
            gold = c["gold_amounts"][topic]
            assert got[topic][0] == gold["status"], (c["id"], topic, got[topic], gold)
            assert got[topic][1] == pytest.approx(gold["amount"], abs=0.011), (c["id"], topic)


def test_ledger_average_matches_source_csv(cs):
    import csv

    rows = list(
        csv.DictReader((ROOT / "data/ledger_src/purchase_history.csv").open(encoding="utf-8"))
    )
    for c in cs:
        for item, led in c["ledger"].items():
            prices = [
                float(r["unit_price_yuan"])
                for r in rows
                if r["tenant"] == c["tenant"] and r["item"] == item
            ]
            assert len(prices) == led["n"]
            assert sum(prices) / len(prices) == pytest.approx(led["avg"], abs=1e-4)
