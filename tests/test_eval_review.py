from contract_review.contract_evalset import Contract
from contract_review.eval_review import score_contract, summarize_review

LAW = "law-civil-code"


def contract(**kw):
    base = dict(
        id="goods-r1",
        tenant="huaxin",
        contract_type="goods",
        template_id="tpl",
        title="t",
        our_side="甲",
        our_name="华信制造有限公司",
        counterparty="x",
        clarify=False,
        text="",
        gold=[
            dict(clause="第四条", type="inspection", level="高", basis=[[LAW, "第六百二十一条"]]),
            dict(clause=None, type="confidentiality", level="中", basis=[[LAW, "第五百零一条"]]),
            dict(
                clause="第一条",
                type="unit_price",
                level="中",
                basis=[["huaxin-contract-policy", "第四条"]],
            ),
        ],
        borderline=[dict(clause="第九条", type="penalty", basis=[])],
        favorable=[dict(clause="第十条", type="termination")],
    )
    base.update(kw)
    return Contract(**base)


def finding(clause, rtype, cites=("C1",), human=False, level="高", quote_found=True):
    return dict(
        clause=clause,
        risk_type=rtype,
        citations=list(cites),
        needs_human_review=human,
        level=level,
        quote_found=quote_found,
    )


CITES = {
    "C1": {"kind": "doc", "doc_id": LAW, "articles": ["第六百二十一条"]},
    "C2": {"kind": "doc", "doc_id": LAW, "articles": ["第一条"]},
    "T1": {"kind": "table", "rows": [[4800.0]]},
}


def result(findings, kind="review", our_side="甲"):
    return dict(
        kind=kind,
        our_side=our_side,
        findings=findings,
        citations=CITES,
        invalid_citations=[],
        steps=3,
        cost_usd=0.01,
        latency_ms=9000.0,
        trace={"end": "submit_review"},
    )


def test_detection_type_and_basis():
    r = result(
        [
            finding("第四条", "inspection", ["C1"]),
            finding(None, "confidentiality", ["C2"], level="中"),
            finding("第一条", "payment", ["T1"]),
        ]
    )
    row = score_contract(contract(), r)
    g = {d["type"]: d for d in row["gold"]}
    assert all(d["detected"] for d in row["gold"])
    assert (
        g["inspection"]["type_ok"] and g["inspection"]["basis_ok"] and g["inspection"]["level_ok"]
    )
    assert g["confidentiality"]["basis_ok"] is False  # 引用了不在依据集合里的条文
    assert g["unit_price"]["type_ok"] is False  # 条款对、类型判错
    assert row["fp"] == 0


def test_unit_price_table_citation_counts_as_basis():
    row = score_contract(contract(), result([finding("第一条", "unit_price", ["T1"])]))
    up = next(d for d in row["gold"] if d["type"] == "unit_price")
    assert up["type_ok"] and up["basis_ok"]


def test_false_positive_favorable_and_borderline():
    r = result(
        [
            finding("第十条", "termination"),  # 对我方有利的条款被报出
            finding("第十二条", "auto_renew"),  # 干净条款
            finding("第九条", "penalty", human=True),  # 临界条款转人工
        ]
    )
    row = score_contract(contract(), r)
    assert row["fp"] == 1 and row["favorable_flagged"] == 1
    assert row["borderline_flagged"] == 1 and row["borderline_human"] == 1
    assert not any(d["detected"] for d in row["gold"])


def test_clarify_contract_scoring():
    c = contract(clarify=True)
    assert score_contract(c, result([], kind="clarify"))["clarify_ok"] is True
    assert score_contract(c, result([]))["clarify_ok"] is False
    assert score_contract(contract(), result([], kind="clarify"))["clarify_ok"] is False


def test_summary_rates():
    risky = score_contract(contract(), result([finding("第四条", "inspection", ["C1"])]))
    clean = score_contract(
        contract(id="goods-c1", gold=[], borderline=[], favorable=[]),
        result([finding("第三条", "payment")]),
    )
    ask = score_contract(contract(id="goods-r3", clarify=True), result([], kind="clarify"))
    s = summarize_review([risky, clean, ask])
    assert s["n"] == 3 and s["detection"]["n_gold"] == 3
    assert abs(s["detection"]["recall"] - 1 / 3) < 1e-9
    assert s["detection"]["type_accuracy"] == 1.0 and s["detection"]["basis_accuracy"] == 1.0
    assert s["clean"]["n"] == 1 and s["clean"]["fp_contract_rate"] == 1.0
    assert s["clarify"]["clarify_rate"] == 1.0 and s["clarify"]["n"] == 1
    assert s["by_type"]["inspection"]["recall"] == 1.0
    assert s["cost"]["total_usd"] > 0


def test_low_level_findings_reported_separately():
    clean = score_contract(
        contract(id="goods-c2", gold=[], borderline=[], favorable=[]),
        result([finding("第三条", "payment", level="低")]),
    )
    assert clean["fp"] == 1 and clean["fp_hm"] == 0
    s = summarize_review([clean])
    assert s["clean"]["fp_contract_rate"] == 1.0 and s["clean"]["fp_contract_rate_hm"] == 0.0
    assert s["detection"]["precision_hm"] == 0.0  # 没有高/中意见时记 0


def test_acceptable_findings_are_not_false_positives():
    # 全分支审查 Important 2/3：金标准漏标的正确意见（台账资质过期、责任上限例外对我方同样适用）
    c = contract(
        id="goods-c3", gold=[], borderline=[], favorable=[],
        acceptable=[
            {"clauses": [None, "第一条"], "types": ["other"], "reason": "对方资质已过期"},
            {"clauses": ["第九条"], "types": ["liability"], "reason": "例外对我方同样适用"},
        ],
    )
    r = result([finding(None, "other"), finding("第九条", "liability"), finding("第九条", "penalty")])
    row = score_contract(c, r)
    assert row["acceptable_flagged"] == 2 and row["fp"] == 1  # 类型对不上的仍算误报
    assert row["matched_findings"] == 2
