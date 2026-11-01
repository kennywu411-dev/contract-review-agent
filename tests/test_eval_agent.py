from contract_review.eval_agent import score_one, summarize_agent
from contract_review.evalset import Question


def q(type_="answerable", subtype=None, gold=None):
    gold = gold if gold is not None else [{"doc_id": "d", "page": 5, "clause": None}]
    return Question(
        "bnu-001",
        "huaxin",
        "问",
        type_,
        subtype,
        "doc",
        None,
        gold if type_ != "refuse" else [],
        "",
        "",
    )


def res(
    kind="answer", cites=("C1",), citations=None, tools=("search_docs",), cost=0.001, ms=3000.0
):
    citations = citations or {
        "C1": {
            "kind": "doc",
            "doc_id": "d",
            "pages": [5],
            "articles": [],
            "text": "x",
            "chunk_id": "d:fixed:0",
        }
    }
    steps = [{"tool_calls": [{"name": t} for t in tools]}]
    return {
        "kind": kind,
        "sentences": [{"text": "x", "citations": list(cites)}] if kind == "answer" else [],
        "citations": citations,
        "invalid_citations": [],
        "steps": len(steps),
        "cost_usd": cost,
        "latency_ms": ms,
        "trace": {"steps": steps, "end": "final_answer"},
    }


def test_cited_gold_counts_as_hit():
    s = score_one(q(), res())
    assert s["cite_hit"] is True and s["answered"] is True


def test_uncited_retrieval_is_not_a_hit():
    s = score_one(q(), res(cites=()))
    assert s["cite_hit"] is False


def test_table_question_routes_to_sql():
    gold = [{"table": "scholarships", "where": "1"}, {"doc_id": "d", "page": 5, "clause": None}]
    cites = {"T1": {"kind": "table", "sql": "select", "columns": ["a"], "rows": [[1]]}}
    s = score_one(q("table", gold=gold), res(cites=("T1",), citations=cites, tools=("run_sql",)))
    assert s["used_sql"] is True and s["cite_hit"] is True


def test_refuse_question_scoring():
    assert score_one(q("refuse"), res("refuse", cites=()))["correct_refuse"] is True
    assert score_one(q("refuse"), res("fallback", cites=()))["correct_refuse"] is True
    assert score_one(q("refuse"), res())["correct_refuse"] is False


def test_summary_rates():
    rows = [
        score_one(q(), res()),
        score_one(q(), res("refuse", cites=())),
        score_one(q("refuse"), res("refuse", cites=())),
        score_one(q("answerable", "clarify"), res("clarify", cites=())),
    ]
    s = summarize_agent(rows)
    assert s["answerable"]["false_refusal_rate"] == 0.5
    assert s["answerable"]["cite_hit_rate"] == 0.5
    assert s["refuse"]["correct_refuse_rate"] == 1.0
    assert s["clarify"]["clarify_rate"] == 1.0
    assert s["cost"]["total_usd"] > 0 and "p95" in s["latency_ms"]


def test_empty_or_failed_sql_does_not_count():
    gold = [{"table": "scholarships", "where": "1"}, {"doc_id": "d", "page": 5, "clause": None}]
    empty = {"T1": {"kind": "table", "sql": "select", "columns": ["a"], "rows": []}}
    s = score_one(q("table", gold=gold), res(cites=("T1",), citations=empty, tools=("run_sql",)))
    assert s["cite_hit"] is False
    r = res(cites=(), tools=())
    r["trace"]["steps"] = [{"tool_calls": [{"name": "run_sql", "is_error": True}]}]
    assert score_one(q("table", gold=gold), r)["used_sql"] is False
