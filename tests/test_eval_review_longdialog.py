from pathlib import Path

import pytest

from contract_review.eval_review_longdialog import (
    clauses_in,
    run_long_dialog_eval,
    score_turn,
    summarize_long,
)
from contract_review.review_agent import ReviewResult

PREV = {
    "kind": "review",
    "our_side": "甲",
    "summary": "三处风险",
    "findings": [
        {
            "clause": "第四条",
            "risk_type": "inspection",
            "level": "高",
            "reason": "r",
            "suggestion": "s",
        },
        {
            "clause": "第六条",
            "risk_type": "deposit",
            "level": "中",
            "reason": "r",
            "suggestion": "s",
        },
        {
            "clause": "第九条",
            "risk_type": "liability",
            "level": "高",
            "reason": "r",
            "suggestion": "s",
        },
    ],
}


def test_clauses_in_answer_text():
    assert clauses_in("第四条和第9条要改，另见民法典第五百八十五条") == {"第四条", "第九条"}
    assert clauses_in("依据本公司规范第十五条、办法第六条，第二条要改") == {"第二条"}


def test_score_turn_kinds():
    fu = {"kind": "followup", "answer_clause": "第四条", "answer": "", "our_side": ""}
    assert score_turn({"type": "clause", "clause": "第四条"}, fu) is True
    assert score_turn({"type": "clause", "clause": "第六条"}, fu) is False
    rv = {"kind": "review", "our_side": "乙", "answer": "", "answer_clause": None}
    assert score_turn({"type": "side", "side": "乙"}, rv) is True
    lst = {
        "kind": "followup",
        "answer": "还需修改第四条、第九条。",
        "answer_clause": None,
        "our_side": "",
    }
    exp = {"type": "list", "must": ["第四条", "第九条"], "must_not": ["第六条"]}
    assert score_turn(exp, lst) is True
    assert score_turn(exp, lst | {"answer": "第四条、第六条、第九条"}) is False
    assert score_turn({"type": "none"}, rv) is None


def test_summary_groups_by_mode_and_turn_type():
    rows = [
        {
            "mode": "full",
            "group": "early",
            "ok": True,
            "input_tokens": 1000,
            "cost_usd": 0.01,
            "history_chars": 5000,
        },
        {
            "mode": "window",
            "group": "early",
            "ok": False,
            "input_tokens": 600,
            "cost_usd": 0.006,
            "history_chars": 2000,
        },
        {
            "mode": "full",
            "group": "recent",
            "ok": None,
            "input_tokens": 900,
            "cost_usd": 0.01,
            "history_chars": 4000,
        },
    ]
    s = summarize_long(rows, {"full": {"cost_usd": 0.0}, "window": {"cost_usd": 0.0}})
    assert s["full"]["early_ok"] == 1.0 and s["window"]["early_ok"] == 0.0
    assert s["full"]["scored_turns"] == 1 and s["full"]["input_tokens_mean"] == 950


def test_runner_threads_history_per_mode(tmp_path, monkeypatch):
    # 假的审查函数：记录每轮收到的历史，按问题返回追问/重审
    from contract_review import eval_review_longdialog as m

    seen = []

    def review_fn(c, history, question):
        seen.append((question, history))
        if "视角" in question or "立场" in question:
            r = ReviewResult(
                c.tenant,
                "review",
                our_side="乙" if "换成" in question else "甲",
                findings=PREV["findings"][:1],
            )
        else:
            r = ReviewResult(c.tenant, "followup", answer="第四条、第九条", answer_clause="第四条")
        r.trace = {"trace_id": f"t{len(seen)}", "steps": []}
        return r

    contract = m.load_contracts()[0]
    dialog = {"contract_id": contract.id, "prev": PREV, "turns": m.script_turns(PREV, contract)}
    monkeypatch.setattr(m, "build_long_dialogs", lambda source, n: [dialog])
    monkeypatch.setattr(m, "RUNS_DIR", tmp_path)
    out = run_long_dialog_eval(
        Path("x"), review_fn, None, ["full", "window"], n=1, tag="t", max_cost=1
    )
    assert len(seen) == 2 * len(dialog["turns"])
    full_last = [h for q, h in seen[: len(dialog["turns"])]][-1]
    window_last = [h for q, h in seen[len(dialog["turns"]) :]][-1]
    assert "【第0轮·首次审查】" in full_last and "【第0轮·首次审查】" not in window_last
    assert (out / "summary.json").exists()


def test_script_turns_cover_early_references():
    from contract_review.eval_review_longdialog import load_contracts, script_turns

    c = next(c for c in load_contracts() if c.id == "goods-r1")
    turns = script_turns(PREV, c)
    groups = [t["expect"].get("group") for t in turns]
    assert len(turns) == 8 and groups.count("early") == 2
    last = turns[-1]["expect"]
    assert last["must"] == ["第四条", "第九条"] and last["must_not"] == ["第六条"]


@pytest.fixture(autouse=True)
def _no_api(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
