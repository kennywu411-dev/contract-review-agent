import json as _json
from types import SimpleNamespace as _NS

import pytest

from docpilot.agent import AgentResult
from docpilot.verify import HaikuVerifier, extract_numbers, numbers_supported, verify_result


@pytest.mark.parametrize(
    "text,nums",
    [
        ("奖励标准为每生每年10000元", {"10000"}),
        ("按学生数的15%评定，3000元/人", {"15", "3000"}),
        ("单次休学一般一年，累计不超过两年", {"1", "2"}),
        ("最长学习年限为六年（含休学）", {"6"}),
        ("1万元", {"10000"}),
        ("10,000元", {"10000"}),
        ("绩点3.5及以上", {"3.5"}),
        ("第十二条规定", set()),
        ("财教〔2024〕181号", set()),
        ("没有数字", set()),
    ],
)
def test_extract_numbers(text, nums):
    assert extract_numbers(text) == nums


def test_chinese_numerals_normalized():
    assert numbers_supported("累计不超过两年", ["休学累计不得超过2年"])


def test_numbers_must_appear_in_sources():
    assert not numbers_supported("一等奖学金5000元", ["一等奖学金4000元"])
    assert numbers_supported("一等奖学金5000元", ["……", "一等 5000 元/人"])


def test_numbers_checked_against_table_rows():
    rows = [["bnu", "京师奖学金", "一等", 5000, 10]]
    assert numbers_supported("京师一等5000元，比例10%", [], [rows])
    assert not numbers_supported("京师一等6000元", [], [rows])


DOC = {
    "kind": "doc",
    "doc_id": "d",
    "pages": [5],
    "articles": [],
    "text": "一等奖学金5000元/人，比例10%。",
}


def agent_result(sentences, kind="answer", unanswered=""):
    return AgentResult(
        "q", "bnu", kind, sentences=sentences, unanswered=unanswered, citations={"C1": DOC}
    )


class FakeJudgeLLM:
    model = "claude-haiku-5-5"

    def __init__(self, labels=None, fail=False):
        self.labels, self.fail, self.requests = labels or {}, fail, []

    def create(self, **kw):
        self.requests.append(kw)
        if self.fail:
            raise TimeoutError("verifier timeout")
        n = kw["messages"][0]["content"].count("<句子")
        out = [
            {"index": i, "label": self.labels.get(i, "supported"), "reason": ""} for i in range(n)
        ]
        usage = _NS(
            input_tokens=100,
            output_tokens=20,
            cache_read_input_tokens=0,
            cache_creation_input_tokens=0,
        )
        return _NS(content=[_NS(type="text", text=_json.dumps({"labels": out}))], usage=usage)


def fact(text, cites=("C1",)):
    return {"kind": "fact", "text": text, "citations": list(cites)}


def test_supported_sentences_kept():
    v = verify_result(agent_result([fact("一等5000元。")]), HaikuVerifier(FakeJudgeLLM()))
    assert v.kind == "answer" and len(v.kept) == 1 and v.dropped == []


def test_number_mismatch_dropped_before_llm():
    llm = FakeJudgeLLM()
    v = verify_result(
        agent_result([fact("一等6000元。"), fact("一等5000元。")]), HaikuVerifier(llm)
    )
    assert [d["reason"] for d in v.dropped] == ["数字与原文不符"] and v.kind == "partial"
    assert llm.requests[0]["messages"][0]["content"].count("<句子") == 1


def test_unsupported_by_verifier_dropped():
    v = verify_result(
        agent_result([fact("一等5000元。"), fact("可以随时申请。")]),
        HaikuVerifier(FakeJudgeLLM({1: "unsupported"})),
    )
    assert len(v.kept) == 1 and v.dropped[0]["reason"].startswith("校验未通过")


def test_all_dropped_becomes_refuse():
    v = verify_result(agent_result([fact("无引用的话", cites=())]), HaikuVerifier(FakeJudgeLLM()))
    assert v.kind == "refuse" and "可靠依据" in v.refuse_reason


def test_note_with_number_becomes_fact():
    note = {"kind": "note", "text": "一般需要3个工作日", "citations": []}
    v = verify_result(agent_result([fact("一等5000元。"), note]), HaikuVerifier(FakeJudgeLLM()))
    assert len(v.kept) == 1 and v.dropped[0]["reason"] == "无引用"


def test_plain_note_kept_without_citation():
    note = {"kind": "note", "text": "具体以教务处通知为准。", "citations": []}
    v = verify_result(agent_result([fact("一等5000元。"), note]), HaikuVerifier(FakeJudgeLLM()))
    assert len(v.kept) == 2 and v.kind == "answer"


def test_verifier_error_degrades_to_refuse():
    v = verify_result(agent_result([fact("一等5000元。")]), HaikuVerifier(FakeJudgeLLM(fail=True)))
    assert v.kind == "refuse" and v.verifier_error


def test_partial_kept_when_unanswered_declared():
    r = agent_result(
        [{"kind": "note", "text": "难度无法评价。", "citations": []}, fact("一等5000元。")],
        kind="partial",
        unanswered="难度",
    )
    v = verify_result(r, HaikuVerifier(FakeJudgeLLM()))
    assert v.kind == "partial" and len(v.kept) == 2


def test_refuse_and_clarify_pass_through():
    for k in ("refuse", "clarify", "fallback"):
        r = AgentResult("q", "bnu", k)
        llm = FakeJudgeLLM()
        assert verify_result(r, HaikuVerifier(llm)).kind == (k if k != "fallback" else "refuse")
        assert llm.requests == []
