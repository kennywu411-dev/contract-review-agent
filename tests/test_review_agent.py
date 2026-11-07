from contract_review.review_agent import (
    REVIEW_TOOLS,
    normalize_clause,
    review_contract,
    review_system_prompt,
)
from tests.test_agent import FakeLLM, tool
from tests.test_tools import FakeSQL, box, ch

CONTRACT = """工业品买卖合同
甲方（买受人）：华信制造有限公司
乙方（出卖人）：常州驰远电机有限公司
第一条 标的、数量和价款
品名：三相异步电动机；单价：人民币3600元/台。
第二条 检验与验收
乙方交付货物即视为甲方验收合格，甲方此后不得再就质量问题提出异议。
"""

FINDING = {
    "clause": "第2条",
    "quote": "乙方交付货物即视为甲方验收合格",
    "risk_type": "inspection",
    "level": "高",
    "reason": "交付即视为合格，剥夺了合理检验期。",
    "suggestion": "改为收货后三十日内检验。",
    "citations": ["[C1]"],
    "needs_human_review": False,
}
REVIEW = {"our_side": "甲", "summary": "发现 1 处风险。", "findings": [FINDING]}


def test_normalize_clause():
    assert normalize_clause("第2条") == "第二条"
    assert normalize_clause(" 第十二条第二款 ") == "第十二条"
    assert normalize_clause("第23条") == "第二十三条"
    assert normalize_clause("缺失") is None and normalize_clause("") is None


def test_review_routes_through_search_then_submits():
    llm = FakeLLM(
        [[tool("search_docs", {"query": "检验期"}, "a")], [tool("submit_review", REVIEW, "b")]]
    )
    r = review_contract(
        CONTRACT,
        "huaxin",
        llm,
        box([ch("law", "common", "第六百二十一条", articles=["第六百二十一条"])]),
    )
    assert r.kind == "review" and r.our_side == "甲" and r.steps == 2
    f = r.findings[0]
    assert f["clause"] == "第二条" and f["citations"] == ["C1"] and f["quote_found"] is True
    assert r.invalid_citations == []
    user = llm.requests[0]["messages"][0]["content"]
    assert "<合同>" in user and "</合同>" in user and "华信制造有限公司" in user


def test_quote_not_in_contract_flagged():
    bad = REVIEW | {"findings": [FINDING | {"quote": "甲方放弃一切权利"}]}
    llm = FakeLLM([[tool("submit_review", bad)]])
    r = review_contract(CONTRACT, "huaxin", llm, box())
    assert r.findings[0]["quote_found"] is False
    assert r.invalid_citations == ["C1"]  # 没检索过就引用


def test_clarification_ends_review():
    llm = FakeLLM(
        [[tool("ask_clarification", {"question": "我方是甲方还是乙方？", "missing": ["我方身份"]})]]
    )
    r = review_contract(CONTRACT, "huaxin", llm, box())
    assert r.kind == "clarify" and "甲方还是乙方" in r.clarification and r.findings == []


def test_ledger_query_scoped_to_tenant():
    sql = FakeSQL({"columns": ["avg"], "rows": [[2600.0]]})
    llm = FakeLLM(
        [
            [tool("run_sql", {"sql": "select avg(unit_price_yuan) from purchase_history"}, "a")],
            [tool("submit_review", REVIEW, "b")],
        ]
    )
    review_contract(CONTRACT, "huaxin", llm, box(sql=sql))
    assert "tenant IN ('huaxin', 'common')" in sql.calls[0]


def test_prompt_lists_risk_types_and_own_policy():
    p = review_system_prompt("huaxin")
    assert "华信制造有限公司" in p and "合同审查管理规范" in p and "瑞丰" not in p
    for rid in ("penalty", "unit_price", "jurisdiction"):
        assert rid in p
    assert review_system_prompt("huaxin") == review_system_prompt("huaxin")


def test_review_tools_strict_and_terminal():
    names = {t["name"] for t in REVIEW_TOOLS}
    assert names == {"search_docs", "run_sql", "ask_clarification", "submit_review"}
    sub = next(t for t in REVIEW_TOOLS if t["name"] == "submit_review")
    item = sub["input_schema"]["properties"]["findings"]["items"]
    assert item["additionalProperties"] is False and set(item["required"]) == set(
        item["properties"]
    )


def test_history_lists_previous_findings_in_order():
    from contract_review.review_agent import history_from_review

    prev = {
        "kind": "review",
        "our_side": "甲",
        "summary": "两处风险。",
        "findings": [
            FINDING | {"clause": "第二条"},
            FINDING | {"clause": "第一条", "risk_type": "unit_price"},
        ],
    }
    h = history_from_review(prev, [("第2条意见怎么改", "建议改为……")])
    assert "<上一轮审查>" in h and "我方：甲方" in h
    assert h.index("1. 第二条") < h.index("2. 第一条")
    assert "用户：第2条意见怎么改" in h and "助手：建议改为……" in h


def test_followup_answer_ends_turn():
    from contract_review.review_agent import FOLLOWUP_TOOLS

    ans = {
        "clause": "第2条",
        "answer": "建议改为：甲方在收到货物之日起三十日内检验。",
        "citations": ["C1"],
        "needs_human_review": False,
    }
    llm = FakeLLM(
        [[tool("search_docs", {"query": "检验期"}, "a")], [tool("answer_followup", ans, "b")]]
    )
    r = review_contract(
        CONTRACT,
        "huaxin",
        llm,
        box([ch("law", "common", "第六百二十一条")]),
        question="你列的第1条意见怎么改？",
        history="<上一轮审查>…</上一轮审查>",
    )
    assert r.kind == "followup" and r.answer.startswith("建议改为") and r.answer_clause == "第二条"
    assert r.answer_citations == ["C1"]
    req = llm.requests[0]
    assert {t["name"] for t in req["tools"]} == {t["name"] for t in FOLLOWUP_TOOLS}
    content = req["messages"][0]["content"]
    assert (
        content.index("<上一轮审查>")
        < content.index("<合同>")
        < content.index("用户追问：你列的第1条意见怎么改？")
    )


def test_first_turn_has_no_followup_tool():
    llm = FakeLLM([[tool("submit_review", REVIEW)]])
    review_contract(CONTRACT, "huaxin", llm, box())
    assert "answer_followup" not in {t["name"] for t in llm.requests[0]["tools"]}


def test_missing_clause_skips_quote_check():
    # 缺失型意见的摘录常写"无"，不应判成摘录与原文不符
    miss = FINDING | {"clause": "缺失", "quote": "（无保密条款）", "risk_type": "confidentiality"}
    llm = FakeLLM([[tool("submit_review", REVIEW | {"findings": [miss]})]])
    r = review_contract(CONTRACT, "huaxin", llm, box())
    assert r.findings[0]["clause"] is None and r.findings[0]["quote_found"] is True
