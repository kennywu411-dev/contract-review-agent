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
    # 金标准漏标的正确意见（台账资质过期、责任上限例外对我方同样适用）
    c = contract(
        id="goods-c3",
        gold=[],
        borderline=[],
        favorable=[],
        acceptable=[
            {"clauses": [None, "第一条"], "types": ["other"], "reason": "对方资质已过期"},
            {"clauses": ["第九条"], "types": ["liability"], "reason": "例外对我方同样适用"},
        ],
    )
    r = result(
        [finding(None, "other"), finding("第九条", "liability"), finding("第九条", "penalty")]
    )
    row = score_contract(c, r)
    assert row["acceptable_flagged"] == 2 and row["fp"] == 1  # 类型对不上的仍算误报
    assert row["matched_findings"] == 2


def test_strict_precision_counts_one_correct_finding_per_gold():
    # 同一条款多条意见、类型判错的意见不计入严格精确率
    r = result(
        [
            finding("第四条", "inspection"),
            finding("第四条", "inspection"),
            finding("第一条", "payment"),
        ]
    )
    row = score_contract(contract(), r)
    assert row["strict_hits"] == 1
    s = summarize_review([row])
    assert abs(s["detection"]["precision_strict"] - 1 / 3) < 1e-9


def v2_contract(**kw):
    # v2 难评测集（ADR 013）：组合风险记多个条款、风险分类别、干净合同带干扰项
    base = dict(
        id="v2-goods-r1",
        gold=[
            dict(
                clause="第二十条",
                clauses=["第二十条", "第十一条"],
                type="liability",
                alt_types=[],
                category="combo",
                level="高",
                basis=[[LAW, "第六百二十一条"]],
            ),
            dict(
                clause="第十四条",
                clauses=["第十四条"],
                type="confidentiality",
                alt_types=["penalty"],
                category="hidden",
                level="中",
                basis=[[LAW, "第五百零一条"]],
            ),
        ],
        borderline=[],
        favorable=[],
    )
    base.update(kw)
    return contract(**base)


def test_combo_gold_matches_any_listed_clause():
    row = score_contract(v2_contract(), result([finding("第十一条", "liability")]))
    combo = row["gold"][0]
    assert combo["detected"] and combo["type_ok"] and combo["category"] == "combo"
    assert row["fp"] == 0 and row["strict_hits"] == 1


def test_alt_types_count_as_type_ok():
    row = score_contract(v2_contract(), result([finding("第十四条", "penalty")]))
    assert row["gold"][1]["type_ok"] and row["strict_hits"] == 1


def test_summary_reports_recall_by_category():
    rows = [score_contract(v2_contract(), result([finding("第二十条", "liability")]))]
    s = summarize_review(rows)
    assert s["by_category"]["combo"]["recall"] == 1.0
    assert s["by_category"]["hidden"]["recall"] == 0.0 and s["by_category"]["hidden"]["n"] == 1


def test_distractor_flags_reported_by_kind():
    c = v2_contract(
        id="v2-goods-c1",
        gold=[],
        distractors=[
            dict(clause="第九条", kind="near_threshold", name="d_inspection"),
            dict(clause="第二十三条", kind="legit_exemption", name="d_force_majeure"),
        ],
    )
    r = result([finding("第九条", "inspection"), finding("第二十三条", "liability", level="低")])
    row = score_contract(c, r)
    assert row["distractors"] == [
        {"kind": "near_threshold", "name": "d_inspection", "flagged_hm": True},
        {"kind": "legit_exemption", "name": "d_force_majeure", "flagged_hm": False},
    ]
    s = summarize_review([row])
    assert s["distractors"]["near_threshold"] == {"n": 1, "flag_rate_hm": 1.0}
    assert s["distractors"]["legit_exemption"]["flag_rate_hm"] == 0.0


def test_run_review_eval_resumes_existing_run(tmp_path, monkeypatch):
    # 进程中途退出（ADR 013 全量跑到 17 份时会话结束）：续跑只补没跑的合同，不重复花钱
    import json
    from types import SimpleNamespace as NS

    import contract_review.eval_review as er

    monkeypatch.setattr(er, "RUNS_DIR", tmp_path)
    calls = []

    def run_one(c):
        calls.append(c.id)
        d = result([finding("第四条", "inspection")])
        return NS(trace={"trace_id": f"t-{c.id}"} | d["trace"], to_dict=lambda: d)

    cs = [contract(id=f"goods-r{i}") for i in range(1, 4)]
    first = er.run_review_eval(cs[:2], run_one, "x")
    out = er.run_review_eval(cs, run_one, "x", resume=first)
    assert out == first and calls == ["goods-r1", "goods-r2", "goods-r3"]
    rows = [json.loads(x) for x in (out / "review_results.jsonl").open()]
    assert [r["id"] for r in rows] == ["goods-r1", "goods-r2", "goods-r3"]
    s = json.loads((out / "summary.json").read_text())
    assert s["n"] == 3 and s["resumed_from"] == 2
