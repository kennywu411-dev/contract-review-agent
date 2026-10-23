from contract_review.pipeline import answer_question
from contract_review.verify import HaikuVerifier
from tests.test_agent import ANSWER, FakeLLM, tool
from tests.test_tools import box, ch
from tests.test_verify import FakeJudgeLLM


def test_pipeline_attaches_verification_to_trace():
    llm = FakeLLM([[tool("search_docs", {"query": "q"})], [tool("final_answer", ANSWER)]])
    judge = FakeJudgeLLM({0: ("unsupported", [])})
    raw, ver = answer_question(
        "q", "huaxin", llm, box([ch("d", "huaxin", "考前申请")]), HaikuVerifier(judge)
    )
    assert raw.kind == "answer" and ver.kind == "refuse"
    v = raw.trace["verify"]
    assert v["kind"] == "refuse" and v["dropped"][0]["reason"].startswith("校验未通过")
    assert raw.trace["cost_usd"] == raw.cost_usd + ver.cost_usd


def test_turn_from_verified_answer():
    from contract_review.pipeline import turn_from

    llm = FakeLLM([[tool("search_docs", {"query": "q"})], [tool("final_answer", ANSWER)]])
    raw, ver = answer_question(
        "缓考怎么办",
        "huaxin",
        llm,
        box([ch("d", "huaxin", "考前申请")]),
        HaikuVerifier(FakeJudgeLLM()),
    )
    t = turn_from(raw, ver)
    assert t.question == "缓考怎么办" and "缓考须考前申请" in t.answer
    assert t.sources == ["《某规定》第5页"]
