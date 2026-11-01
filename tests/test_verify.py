import json as _json
from types import SimpleNamespace as _NS

import pytest

from contract_review.agent import AgentResult
from contract_review.verify import HaikuVerifier, extract_numbers, numbers_supported, verify_result


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
        ("转专业要降一级吗", set()),
        ("最高两万元", {"20000"}),
        ("十万元以下", {"100000"}),
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
    rows = [["huaxin", "京师奖学金", "一等", 5000, 10]]
    assert numbers_supported("京师一等5000元，比例10%", [], [rows])
    assert not numbers_supported("京师一等6000元", [], [rows])


DOC = {
    "kind": "doc",
    "doc_id": "d",
    "pages": [5],
    "articles": [],
    "text": "一等奖学金5000元/人，比例10%。",
}
DOC2 = {"kind": "doc", "doc_id": "e", "pages": [6], "articles": [], "text": "二等奖学金3000元/人。"}


def agent_result(sentences, kind="answer", unanswered=""):
    return AgentResult(
        "q",
        "huaxin",
        kind,
        sentences=sentences,
        unanswered=unanswered,
        citations={"C1": DOC, "C2": DOC2},
    )


class FakeJudgeLLM:
    """labels: 句子序号 → (标签, 支撑编号[, 改写])。默认 supported、由 C1 支撑。"""

    model = "claude-haiku-5-5"

    def __init__(self, labels=None, fail=False):
        self.labels, self.fail, self.requests = labels or {}, fail, []

    def create(self, **kw):
        self.requests.append(kw)
        if self.fail:
            raise TimeoutError("verifier timeout")
        n = kw["messages"][0]["content"].count("<句子 index=")
        out = []
        for i in range(n):
            label, support, *rest = self.labels.get(i, ("supported", ["C1"]))
            out.append(
                {
                    "index": i,
                    "label": label,
                    "support": support,
                    "revised": rest[0] if rest else "",
                    "reason": "",
                }
            )
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


def test_verifier_sees_all_retrieved_sources():
    llm = FakeJudgeLLM()
    verify_result(agent_result([fact("一等5000元。")]), HaikuVerifier(llm))
    prompt = llm.requests[0]["messages"][0]["content"]
    assert "[C1]" in prompt and "[C2]" in prompt and "二等奖学金3000元" in prompt


def test_wrong_citation_is_repaired():
    v = verify_result(
        agent_result([fact("二等3000元。", cites=("C1",))]),
        HaikuVerifier(FakeJudgeLLM({0: ("supported", ["C2"])})),
    )
    assert v.kept[0]["citations"] == ["C2"] and v.repaired == 1


def test_number_mismatch_dropped_even_if_verifier_agrees():
    v = verify_result(
        agent_result([fact("一等6000元。"), fact("一等5000元。")]), HaikuVerifier(FakeJudgeLLM())
    )
    assert [d["reason"] for d in v.dropped] == ["数字与原文不符"] and v.kind == "partial"


def test_unsupported_by_verifier_dropped():
    v = verify_result(
        agent_result([fact("一等5000元。"), fact("可以随时申请。")]),
        HaikuVerifier(FakeJudgeLLM({1: ("unsupported", [])})),
    )
    assert len(v.kept) == 1 and v.dropped[0]["reason"].startswith("校验未通过")


def test_all_dropped_becomes_refuse():
    v = verify_result(
        agent_result([fact("学校会全额报销路费", cites=())]),
        HaikuVerifier(FakeJudgeLLM({0: ("unsupported", [])})),
    )
    assert v.kind == "refuse" and "可靠依据" in v.refuse_reason


def test_uncited_but_supported_fact_gets_citation():
    v = verify_result(agent_result([fact("一等5000元。", cites=())]), HaikuVerifier(FakeJudgeLLM()))
    assert v.kept[0]["citations"] == ["C1"]


def test_note_with_number_becomes_fact():
    note = {"kind": "note", "text": "一般需要3个工作日", "citations": []}
    v = verify_result(agent_result([fact("一等5000元。"), note]), HaikuVerifier(FakeJudgeLLM()))
    assert len(v.kept) == 1 and v.dropped[0]["reason"] == "数字与原文不符"


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
        r = AgentResult("q", "huaxin", k)
        llm = FakeJudgeLLM()
        assert verify_result(r, HaikuVerifier(llm)).kind == (k if k != "fallback" else "refuse")
        assert llm.requests == []


def test_partial_sentence_revised_and_kept():
    llm = FakeJudgeLLM({0: ("partial", ["C1"], "一等奖学金5000元。")})
    v = verify_result(agent_result([fact("一等奖学金5000元，可随时申请。")]), HaikuVerifier(llm))
    assert v.kind == "answer" and v.kept[0]["text"] == "一等奖学金5000元。"
    assert v.kept[0]["revised_from"] == "一等奖学金5000元，可随时申请。" and v.revised == 1


def test_partial_without_revision_dropped():
    v = verify_result(
        agent_result([fact("一等5000元。"), fact("可随时申请。")]),
        HaikuVerifier(FakeJudgeLLM({1: ("partial", ["C1"], "")})),
    )
    assert len(v.kept) == 1 and v.dropped[0]["reason"].startswith("校验未通过")


def test_revised_text_still_number_checked():
    llm = FakeJudgeLLM({0: ("partial", ["C1"], "一等奖学金6000元。")})
    v = verify_result(agent_result([fact("一等奖学金6000元，可随时申请。")]), HaikuVerifier(llm))
    assert v.kind == "refuse" and v.dropped[0]["reason"] == "数字与原文不符"


def test_note_sentences_are_verified_too():
    note = {"kind": "note", "text": "研究生也可以申请国家奖学金。", "citations": []}
    llm = FakeJudgeLLM({1: ("unsupported", [])})
    v = verify_result(agent_result([fact("一等5000元。"), note]), HaikuVerifier(llm))
    assert len(v.kept) == 1 and v.dropped[0]["sentence"]["text"].startswith("研究生")


def test_table_header_numbers_do_not_support():
    tbl = {
        "kind": "table",
        "sql": "s",
        "columns": ["year_2024", "amount"],
        "rows": [["huaxin", 100]],
    }
    r = AgentResult(
        "q",
        "huaxin",
        "answer",
        sentences=[fact("2024年标准为100元", cites=("T1",))],
        citations={"T1": tbl},
    )
    v = verify_result(r, HaikuVerifier(FakeJudgeLLM({0: ("supported", ["T1"])})))
    assert v.kind == "refuse" and v.dropped[0]["reason"] == "数字与原文不符"


def test_repaired_counts_set_changes_only():
    v = verify_result(
        agent_result([fact("一等5000元，二等3000元。", cites=("C2", "C1"))]),
        HaikuVerifier(FakeJudgeLLM({0: ("supported", ["C1", "C2"])})),
    )
    assert v.repaired == 0
    v = verify_result(agent_result([fact("一等5000元。", cites=())]), HaikuVerifier(FakeJudgeLLM()))
    assert v.repaired == 0 and v.cited_added == 1
