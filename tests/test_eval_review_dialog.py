from contract_review.eval_review_dialog import score_followup, summarize_dialogs
from tests.test_eval_review import CITES, contract, finding, result


def followup(clause, cites=("C1",), kind="followup"):
    return dict(
        kind=kind,
        answer="建议改为……",
        answer_clause=clause,
        answer_citations=list(cites),
        citations=CITES,
        our_side="",
        findings=[],
        invalid_citations=[],
        cost_usd=0.004,
        latency_ms=9000.0,
        steps=2,
        trace={"end": "answer_followup"},
    )


def test_ordinal_followup_needs_right_clause():
    exp = {"type": "ordinal", "clause": "第四条"}
    assert score_followup(exp, followup("第四条"), contract())["ok"] is True
    assert score_followup(exp, followup("第五条"), contract())["ok"] is False
    assert score_followup(exp, followup("第四条", kind="review"), contract())["ok"] is False


def test_basis_followup_checks_citation_set():
    exp = {"type": "basis", "clause": "第四条", "basis": [["law-civil-code", "第六百二十一条"]]}
    good = score_followup(exp, followup("第四条", ["C1"]), contract())
    bad = score_followup(exp, followup("第四条", ["C2"]), contract())
    assert good["ok"] and good["basis_ok"] and bad["ok"] is True and bad["basis_ok"] is False


def test_flip_and_clarify():
    c = contract()
    flip = {"type": "flip", "side": "乙"}
    r = result([finding("第四条", "inspection")], our_side="乙")
    s = score_followup(flip, r, c)
    assert s["ok"] is True and s["old_gold_reported"] == 1 / 2  # 缺失型不计入
    assert score_followup(flip, result([], our_side="甲"), c)["ok"] is False
    clar = {"type": "clarify", "side": "甲"}
    s = score_followup(clar, result([finding("第四条", "inspection", ["C1"])]), c)
    assert s["ok"] is True and s["recall"] == 1 / 3


def test_summary_by_type_and_mode():
    rows = [
        {"mode": "full", "type": "ordinal", "ok": True, "cost_usd": 0.01, "latency_ms": 1.0},
        {"mode": "none", "type": "ordinal", "ok": False, "cost_usd": 0.01, "latency_ms": 1.0},
        {
            "mode": "full",
            "type": "basis",
            "ok": True,
            "basis_ok": False,
            "cost_usd": 0.01,
            "latency_ms": 1.0,
        },
    ]
    s = summarize_dialogs(rows)
    assert s["full"]["ordinal"]["ok_rate"] == 1.0 and s["none"]["ordinal"]["ok_rate"] == 0.0
    assert s["full"]["basis"]["basis_ok_rate"] == 0.0
    assert abs(s["cost_usd"] - 0.03) < 1e-9


def test_failed_flip_not_counted_as_reporting_old_gold():
    flip = {"type": "flip", "side": "乙"}
    s = score_followup(flip, result([], our_side="甲"), contract())
    assert s["ok"] is False and s["old_gold_reported"] is None
    rows = [
        {
            "mode": "full",
            "type": "flip",
            "ok": False,
            "old_gold_reported": None,
            "cost_usd": 0,
            "latency_ms": 1,
        }
    ]
    assert summarize_dialogs(rows)["full"]["flip"]["old_gold_reported"] is None
