import json

from contract_review.eval_dialog import Dialog, load_dialogs, run_dialog, summarize_dialogs
from contract_review.verify import HaikuVerifier
from tests.test_agent import ANSWER, FakeLLM, tool
from tests.test_judge import FakeSonnet
from tests.test_tools import box, ch
from tests.test_verify import FakeJudgeLLM

DLG = {
    "id": "dlg-001",
    "tenant": "huaxin",
    "pattern": "coreference",
    "turns": [
        {
            "question": "北师大国奖多少钱",
            "type": "answerable",
            "needs_context": False,
            "gold": [{"doc_id": "d", "page": 5, "clause": None}],
            "answer_hint": "1万元",
        },
        {
            "question": "那研究生呢",
            "type": "refuse",
            "needs_context": True,
            "gold": [],
            "answer_hint": "未收录研究生规定",
        },
    ],
}


def test_load_dialogs(tmp_path):
    p = tmp_path / "d.jsonl"
    p.write_text(json.dumps(DLG, ensure_ascii=False), encoding="utf-8")
    [d] = load_dialogs(p)
    assert isinstance(d, Dialog) and len(d.turns) == 2


def make_llm():
    return FakeLLM([[tool("search_docs", {"query": "q"})], [tool("final_answer", ANSWER)]] * 2)


JUDGE_OUT = {
    "sentences": [{"index": 0, "supported": True}],
    "revised": [],
    "raw_correct": "correct",
    "verified_correct": "correct",
    "raw_refusal_ok": True,
    "verified_refusal_ok": True,
}


def run(mode):
    llm, judge = make_llm(), FakeSonnet(JUDGE_OUT)
    d = load_dialogs_from(DLG)
    rows = run_dialog(
        d,
        mode,
        lambda tenant: box([ch("d", "huaxin", "国奖1万元")]),
        llm,
        HaikuVerifier(FakeJudgeLLM()),
        judge_llm=judge,
        summarizer=FakeJudgeLLM(),
    )
    return rows, llm, judge


def load_dialogs_from(obj):
    return Dialog(**obj)


def test_history_only_in_memory_modes():
    rows, llm, _ = run("none")
    assert "对话历史" not in llm.requests[2]["messages"][0]["content"]
    rows, llm, _ = run("full")
    assert "北师大国奖多少钱" in llm.requests[2]["messages"][0]["content"]


def test_only_followups_are_judged_and_context_given_to_judge():
    rows, _, judge = run("full")
    assert [r["turn"] for r in rows] == [0, 1] and rows[0]["judged"] is False and rows[1]["judged"]
    assert "北师大国奖多少钱" in judge.requests[0]["messages"][0]["content"]


def test_followup_correct_requires_refusal_ok_for_refuse_turn():
    rows, _, _ = run("full")
    assert rows[1]["correct"] is True


def test_summary_by_mode():
    rows = run("full")[0] + [r | {"mode": "none", "correct": False} for r in run("none")[0]]
    s = summarize_dialogs(rows)
    assert s["full"]["followup_correct"] == 1.0 and s["none"]["followup_correct"] == 0.0
    assert "history_tokens_mean" in s["full"] and "cost_usd" in s["full"]
