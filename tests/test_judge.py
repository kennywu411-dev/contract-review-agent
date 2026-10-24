import json
from types import SimpleNamespace as NS

from contract_review.agent import AgentResult
from contract_review.evalset import Question
from contract_review.judge import Judge, score_d4, summarize_d4
from contract_review.verify import VerifiedAnswer

DOC = {"kind": "doc", "doc_id": "d", "pages": [5], "articles": [], "text": "一等5000元"}


class FakeSonnet:
    model = "claude-sonnet-5-5"

    def __init__(self, out):
        self.out, self.requests = out, []

    def create(self, **kw):
        self.requests.append(kw)
        u = NS(
            input_tokens=1000,
            output_tokens=100,
            cache_read_input_tokens=0,
            cache_creation_input_tokens=0,
        )
        return NS(
            content=[NS(type="text", text=json.dumps(self.out))],
            usage=u,
            model=self.model,
            stop_reason="end_turn",
        )


def q(type_="answerable", subtype=None):
    gold = [] if type_ == "refuse" else [{"doc_id": "d", "page": 5, "clause": None}]
    return Question(
        "bnu-001", "huaxin", "一等多少钱", type_, subtype, "doc", None, gold, "5000元", ""
    )


def raw(sentences, kind="answer"):
    return AgentResult("一等多少钱", "huaxin", kind, sentences=sentences, citations={"C1": DOC})


S0 = {"kind": "fact", "text": "一等5000元", "citations": ["C1"]}
S1 = {"kind": "fact", "text": "可随时申请", "citations": ["C1"]}
OUT = {
    "sentences": [{"index": 0, "supported": True}, {"index": 1, "supported": False}],
    "raw_correct": "partially_correct",
    "verified_correct": "correct",
    "raw_refusal_ok": False,
    "verified_refusal_ok": False,
}


def test_judge_scores_both_versions():
    r = raw([S0, S1])
    v = VerifiedAnswer(
        "partial", kept=[S0 | {"index": 0}], dropped=[{"sentence": S1, "reason": "x"}]
    )
    j = Judge(FakeSonnet(OUT)).judge(q(), r, v)
    row = score_d4(q(), r, v, j)
    assert row["raw_fact_n"] == 2 and row["raw_fact_supported"] == 1
    assert row["ver_fact_n"] == 1 and row["ver_fact_supported"] == 1
    assert row["raw_hallucinated"] and not row["ver_hallucinated"]
    assert row["ver_correct"] == "correct" and row["judge_cost_usd"] > 0


def test_refuse_question_lenient_and_strict():
    out = OUT | {"sentences": [], "verified_refusal_ok": True}
    r = raw([], kind="partial")
    v = VerifiedAnswer(
        "partial", kept=[{"kind": "note", "text": "无法评价", "citations": [], "index": 0}]
    )
    row = score_d4(q("refuse"), r, v, Judge(FakeSonnet(out)).judge(q("refuse"), r, v))
    s = summarize_d4([row])
    assert s["refuse"]["strict_rate"]["verified"] == 0.0
    assert s["refuse"]["lenient_rate"]["verified"] == 1.0


def test_summary_citation_accuracy_and_false_refusal():
    r = raw([S0, S1])
    v = VerifiedAnswer("partial", kept=[S0 | {"index": 0}])
    row = score_d4(q(), r, v, Judge(FakeSonnet(OUT)).judge(q(), r, v))
    refused = score_d4(
        q(),
        raw([S0]),
        VerifiedAnswer("refuse"),
        Judge(FakeSonnet(OUT)).judge(q(), raw([S0]), VerifiedAnswer("refuse")),
    )
    s = summarize_d4([row, refused])
    assert s["citation_accuracy"]["raw"] == 2 / 3 and s["citation_accuracy"]["verified"] == 1.0
    assert s["answerable"]["false_refusal"]["verified"] == 0.5
    assert s["hallucination_rate"]["verified"] == 0.0


def test_revised_sentences_judged_on_new_text():
    out = OUT | {"revised": [{"index": 1, "supported": True}]}
    r = raw([S0, S1])
    v = VerifiedAnswer(
        "answer",
        kept=[
            S0 | {"index": 0},
            {
                "kind": "fact",
                "text": "一等5000元",
                "citations": ["C1"],
                "index": 1,
                "revised_from": "可随时申请",
            },
        ],
        revised=1,
    )
    llm = FakeSonnet(out)
    row = score_d4(q(), r, v, Judge(llm).judge(q(), r, v))
    assert row["ver_fact_n"] == 2 and row["ver_fact_supported"] == 2 and not row["ver_hallucinated"]
    assert "改写" in llm.requests[0]["messages"][0]["content"]


def test_agent_result_rebuilt_from_trace():
    from contract_review.judge import result_from_trace

    trace = {
        "question": "q",
        "tenant": "huaxin",
        "cost_usd": 0.001,
        "latency_ms": 5000,
        "result": {
            "kind": "partial",
            "sentences": [S0],
            "refuse_reason": "",
            "unanswered": "难度",
            "clarification": "",
            "invalid_citations": [],
        },
        "citations": {"C1": DOC},
        "steps": [{"step": 1}],
        "trace_id": "t1",
    }
    r = result_from_trace(trace)
    assert r.kind == "partial" and r.unanswered == "难度" and r.citations["C1"] == DOC
    assert r.steps == 1 and r.trace["trace_id"] == "t1"
