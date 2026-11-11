"""金额测算的规则部分（quant.py）：数字解析、原文核对、每个测算项的算式、汇总和挂接。全部不调用模型。"""

import pytest

from contract_review import quant
from contract_review.quant import (
    QuantParams,
    attach,
    check_figures,
    cn_to_number,
    compute,
    numbers_in,
    summarize,
    verify_figure,
)

P = QuantParams(
    capital_cost_rate_pct=6.0,
    scenario_delay_days=30,
    std_payment_days=60,
    std_receivable_days=60,
    max_deposit_pct=20.0,
    max_penalty_cap_pct=20.0,
    price_alert_pct=10.0,
    std_retention_pct=5.0,
    default_delivery_days=30,
)


def figs(**values):
    return [
        {
            "field": k,
            "label": quant.FIELDS[k][1],
            "value": v,
            "quote": "",
            "clause": "第一条",
            "ok": True,
            "why": "",
        }
        for k, v in values.items()
    ]


def line(item, price, qty, avg=None, ok=True, why=""):
    ledger = {"item": item, "avg": avg, "n": 10} if avg else None
    return {
        "item": item,
        "unit": "吨",
        "unit_price": price,
        "quantity": qty,
        "quote": f"品名：{item}",
        "clause": "第一条",
        "ok": ok,
        "why": why,
        "ledger": ledger,
    }


def by_key(items):
    return {i.key: i for i in items}


@pytest.mark.parametrize(
    "text,value",
    [
        ("三十", 30),
        ("一百八十", 180),
        ("一百零五", 105),
        ("十五", 15),
        ("零点二", 0.2),
        ("五十万", 500000),
        ("一千", 1000),
        ("两", 2),
        ("4821.5", 4821.5),
        ("159,300", 159300),
    ],
)
def test_cn_to_number(text, value):
    assert cn_to_number(text) == pytest.approx(value)


def test_cn_to_number_rejects_words():
    assert cn_to_number("合同") is None and cn_to_number("") is None and cn_to_number("点") is None


def test_numbers_in_separates_ratios_from_plain_numbers():
    pct, plain = numbers_in(
        "每逾期一日，按合同总额的万分之五支付违约金，总额不超过合同总额的百分之十"
    )
    assert sorted(pct) == pytest.approx([0.05, 10])
    assert 1 in plain and 100 not in plain and 10000 not in plain  # 比例里的"百""万"不算普通数字
    pct, plain = numbers_in("支付合同总额的30%作为预付款；余款在45日内付清，日费率 0.05%，千分之一")
    assert sorted(pct) == pytest.approx([0.05, 0.1, 30]) and 45 in plain
    assert numbers_in("合同总额：人民币159,300元")[1] == [159300]
    assert numbers_in("人民币50万元")[1] == [500000]


CONTRACT = (
    "第一条 标的\n单价：人民币1593元/桶；数量：100；合同总额：人民币159,300元。\n"
    "第二条 定金\n甲方应当支付合同总额百分之二十五的定金。\n"
    "第三条 预付款\n甲方应当支付合同总额百分之三十的预付款；乙方违约的，应当双倍返还预付款。\n"
    "第四条 违约责任\n甲方逾期付款的，每逾期一日，应当按合同总额的千分之三支付违约金。"
    "任何一方违约的，应当按照守约方实际损失的百分之一百三十支付违约金。\n"
)


def test_verify_figure_accepts_number_written_in_chinese():
    assert verify_figure("deposit_pct", 25, "支付合同总额百分之二十五的定金", CONTRACT) == (
        True,
        "",
    )
    assert verify_figure("contract_total", 159300, "合同总额：人民币159,300元", CONTRACT)[0]
    assert verify_figure(
        "jia_late_daily_pct", 0.3, "每逾期一日，应当按合同总额的千分之三支付违约金", CONTRACT
    )[0]


@pytest.mark.parametrize(
    "name,value,quote,why",
    [
        ("deposit_pct", 30, "支付合同总额百分之二十五的定金", "摘录里没有这个数"),  # 数字抄错
        ("deposit_pct", 25, "支付合同总额百分之二十五的保证金", "找不到"),  # 摘录是编的
        ("deposit_pct", 30, "支付合同总额百分之三十的预付款", "定金"),  # 预付款不能当定金
        (
            "jia_lump_pct",
            130,
            "按照守约方实际损失的百分之一百三十支付违约金",
            "实际损失",
        ),  # 基数是损失
        ("jia_late_daily_pct", 25, "支付合同总额百分之二十五的定金", "按日"),
        ("contract_total", 25, "支付合同总额百分之二十五的定金", "没有这个数"),  # 比例不能当金额
        ("made_up", 1, "数量：100", "未知字段"),
        ("delivery_days", -1, "数量：100", "有效"),
        ("delivery_days", 100, "", "找不到"),
    ],
)
def test_verify_figure_rejects(name, value, quote, why):
    ok, reason = verify_figure(name, value, quote, CONTRACT)
    assert not ok and why in reason


def test_check_figures_locates_clause_and_voids_conflicting_copies():
    clauses = {
        "第一条": "第一条 标的\n单价：人民币1593元/桶；数量：100；合同总额：人民币159,300元。",
        "第二条": "第二条 定金\n甲方应当支付合同总额百分之二十五的定金。",
    }
    out = check_figures(
        [
            {"field": "deposit_pct", "value": 25, "quote": "支付合同总额百分之二十五的定金"},
            {"field": "delivery_days", "value": 100, "quote": "数量：100"},
            {"field": "delivery_days", "value": 1593, "quote": "单价：人民币1593元/桶"},
        ],
        CONTRACT,
        clauses,
    )
    assert out[0]["ok"] and out[0]["clause"] == "第二条"
    assert not out[1]["ok"] and not out[2]["ok"] and "两个不同的数" in out[1]["why"]


# ---- 手算核对的三个例子（算式写在注释里）----


def test_example_1_deposit_penalty_and_capital_cost():
    # 合同总额 159,300；定金 25%；交付期 15 日；账期 60 日（= 标准）；甲方日千分之三无上限；乙方日万分之五、上限 10%
    items = by_key(
        compute(
            figs(
                contract_total=159300,
                delivery_days=15,
                deposit_pct=25,
                payment_days=60,
                jia_late_daily_pct=0.3,
                yi_late_daily_pct=0.05,
                yi_late_cap_pct=10,
            ),
            "甲",
            P,
            [line("液压油", 1593, 100, 1659.4)],
        )
    )
    # 资金占用：159300 × 25% × (15 + 60) / 365 × 6% = 39825 × 0.205479 × 0.06 = 490.99
    assert items["payment_capital"].amount == pytest.approx(490.99)
    # 定金超额：159300 × (25% − 20%) = 7965
    assert items["deposit_excess"].amount == pytest.approx(7965.00)
    # 违约金：我方 0.3% × 30 = 9% → 14337；对方 0.05% × 30 = 1.5% → 2389.5；差 11947.5
    assert items["penalty_gap"].amount == pytest.approx(11947.50)
    assert "penalty_over_cap" not in items  # 9% 没超过公司上限 20%
    assert "price_overpay" not in items  # 单价低于台账均价
    s = summarize(list(items.values()))
    assert s["sure_total"] == pytest.approx(490.99)
    assert s["exposure_total"] == pytest.approx(7965 + 11947.5)
    assert s["worst_total"] == pytest.approx(20403.49)
    assert [x["key"] for x in s["priorities"]] == [
        "penalty_gap",
        "deposit_excess",
        "payment_capital",
    ]


def test_example_2_price_overpay_and_prepayment():
    # 单价 6200，台账均价 4821.5，数量 100，总额 620,000；预付 30%，账期 15 日，交付期 30 日
    items = by_key(
        compute(
            figs(
                contract_total=620000,
                delivery_days=30,
                prepay_pct=30,
                payment_days=15,
            ),
            "甲",
            P,
            [line("冷轧钢板", 6200, 100, 4821.5)],
        )
    )
    # 比价：(6200 − 4821.5) × 100 = 137,850；高出 28.6% > 10%
    assert items["price_overpay"].amount == pytest.approx(137850.00)
    assert items["price_overpay"].counted and "28.6%" in items["price_overpay"].formula
    # 资金占用：620000 × 30% × (30 + 60)/365 × 6% = 186000 × 0.246575 × 0.06 = 2751.78
    #         + 620000 × 70% × (60 − 15)/365 × 6% = 434000 × 0.123288 × 0.06 = 3210.41 → 5962.19
    assert items["payment_capital"].amount == pytest.approx(5962.19)
    assert (
        "2,751.78" in items["payment_capital"].formula
        and "3,210.41" in items["payment_capital"].formula
    )


def test_example_3_lump_sum_penalty_over_company_cap_counts_once():
    # 总额 200,000；甲方一次性违约金 50%；乙方日万分之三、上限 5%
    items = compute(
        figs(contract_total=200000, jia_lump_pct=50, yi_late_daily_pct=0.03, yi_late_cap_pct=5),
        "甲",
        P,
    )
    k = by_key(items)
    # 敞口差：200000 × 50% − 200000 × (0.03% × 30 = 0.9%) = 100000 − 1800 = 98200
    assert k["penalty_gap"].amount == pytest.approx(98200)
    # 超出公司上限：100000 − 200000 × 20% = 60000
    assert k["penalty_over_cap"].amount == pytest.approx(60000)
    s = summarize(items)
    assert s["worst_total"] == pytest.approx(98200)  # 两个口径同组，只计较大的一项
    assert [x["key"] for x in s["priorities"]] == ["penalty_gap"]


def test_penalty_cap_applies_and_missing_counterparty_penalty_counts_as_zero():
    items = by_key(
        compute(figs(contract_total=100000, jia_late_daily_pct=1, jia_late_cap_pct=20), "甲", P)
    )
    # 1% × 30 = 30% → 上限 20% → 20000；对方没约定按 0
    assert items["penalty_gap"].amount == pytest.approx(20000)
    assert any("按 0 计" in a for a in items["penalty_gap"].assumptions)
    assert "penalty_over_cap" not in items


def test_symmetric_or_favourable_penalty_gives_no_item():
    sym = figs(
        contract_total=100000,
        jia_late_daily_pct=0.05,
        jia_late_cap_pct=10,
        yi_late_daily_pct=0.05,
        yi_late_cap_pct=10,
    )
    assert compute(sym, "甲", P) == []
    fav = figs(contract_total=100000, jia_late_daily_pct=0.03, yi_late_daily_pct=0.3)
    assert compute(fav, "甲", P) == []


def test_price_within_company_tolerance_is_shown_but_not_counted():
    items = compute(figs(contract_total=5000), "甲", P, [line("冷轧钢板", 5000, 1, 4821.5)])
    assert items[0].status == "within_standard" and not items[0].counted
    assert summarize(items)["worst_total"] == 0


def test_price_without_ledger_is_unquantifiable():
    items = compute([], "甲", P, [line("轴承", 5000, 1)])
    assert items[0].status == "unquantifiable" and "台账" in items[0].note


def test_multi_line_overpay_sums_only_lines_over_the_company_threshold():
    # 五行标的，只有电动机超标：(3269 − 2563.6) × 100 = 70,540；其余行高一点或低于均价，不计
    lines = [
        line("深沟球轴承", 9.14, 50000, 9.444),
        line("不锈钢无缝管", 38.74, 50000, 38.257),  # 高 1.3%，在 10% 以内
        line("三相异步电动机", 3269, 100, 2563.6),
        line("液压油", 1628, 100, 1659.4),
        line("冷轧钢板", 5400, 50, 4821.5),  # 高 12.0%：(5400 − 4821.5) × 50 = 28,925
        line("定制夹具", 800, 10),  # 台账里没有，不比价
    ]
    item = compute(figs(contract_total=1), "甲", P, lines)[0]
    assert item.amount == pytest.approx(70540 + 28925) and item.counted
    assert (
        "三相异步电动机" in item.formula
        and "冷轧钢板" in item.formula
        and "不锈钢无缝管" not in item.formula
    )
    assert "合计 99,465.00 元" in item.formula
    assert any("1 行标的在台账里没有" in a for a in item.assumptions)
    assert len(item.inputs) == 2


def test_one_unverified_line_blocks_the_whole_price_comparison():
    lines = [
        line("冷轧钢板", 6200, 100, 4821.5),
        line("液压油", 1628, 10, 1659.4, ok=False, why="摘录里没有这个单价"),
    ]
    item = compute([], "甲", P, lines)[0]
    assert item.status == "needs_check" and item.amount is None and "液压油" in item.note


def test_check_lines_verifies_item_price_and_quantity_against_the_text():
    text = "第一条 标的\n（一）品名：冷轧钢板；数量：50；单价：人民币4850元/吨；\n（二）品名：液压油；数量：100；单价：人民币1628元/桶；\n"
    clauses = {"第一条": text}
    raw = [
        {
            "item": "冷轧钢板",
            "unit": "吨",
            "unit_price": 4850,
            "quantity": 50,
            "quote": "品名：冷轧钢板；数量：50；单价：人民币4850元/吨",
        },
        {
            "item": "液压油",
            "unit": "桶",
            "unit_price": 1682,
            "quantity": 100,
            "quote": "品名：液压油；数量：100；单价：人民币1628元/桶",
        },
        {
            "item": "液压油",
            "unit": "桶",
            "unit_price": 4850,
            "quantity": 100,
            "quote": "品名：冷轧钢板；数量：50；单价：人民币4850元/吨",
        },
        {
            "item": "液压油",
            "unit": "桶",
            "unit_price": 1628,
            "quantity": 0,
            "quote": "品名：液压油；数量：100；单价：人民币1628元/桶",
        },
    ]
    out = quant.check_lines(raw, text, clauses)
    assert out[0]["ok"] and out[0]["clause"] == "第一条"
    assert [o["why"] for o in out[1:]] == [
        "摘录里没有这个单价",
        "摘录里没有这个品名",
        "单价或数量不是有效的数值",
    ]


def test_flagged_topics_are_unquantifiable_not_guessed():
    items = by_key(
        compute(
            figs(contract_total=100000, deposit_pct=10),
            "甲",
            P,
            uncomputable=[
                {
                    "topic": "payment",
                    "quote": "收到下游客户付款后",
                    "reason": "付款时间取决于第三方",
                },
                {"topic": "penalty_jia", "quote": "实际损失的", "reason": "以实际损失为基数"},
                {"topic": "retention", "quote": "另行约定", "reason": "没有写明比例"},
            ],
        )
    )
    assert {k: i.status for k, i in items.items()} == {
        "payment_capital": "unquantifiable",
        "penalty_gap": "unquantifiable",
        "retention": "unquantifiable",
    }
    assert items["payment_capital"].note == "付款时间取决于第三方"


def test_counterparty_penalty_based_on_loss_makes_gap_unquantifiable_but_cap_still_computed():
    items = by_key(
        compute(
            figs(contract_total=100000, jia_lump_pct=50),
            "甲",
            P,
            uncomputable=[{"topic": "penalty_yi", "quote": "", "reason": "对方违约金以损失为基数"}],
        )
    )
    assert items["penalty_gap"].status == "unquantifiable"
    assert items["penalty_over_cap"].amount == pytest.approx(30000)


def test_unverified_field_blocks_the_amount():
    bad = figs(contract_total=100000) + [
        {
            "field": "deposit_pct",
            "label": "定金比例",
            "value": 30,
            "quote": "x",
            "clause": None,
            "ok": False,
            "why": "摘录里没有这个数",
        }
    ]
    items = by_key(compute(bad, "甲", P))
    assert (
        items["deposit_excess"].status == "needs_check" and items["deposit_excess"].amount is None
    )
    assert items["payment_capital"].status == "needs_check"
    assert summarize(list(items.values()))["needs_check"] == 2


def test_missing_contract_total_is_not_guessed():
    items = by_key(compute(figs(deposit_pct=30, jia_lump_pct=50), "甲", P))
    assert all(i.status == "unquantifiable" and i.amount is None for i in items.values())


def test_retention_gap_for_buyer_and_capital_cost_for_seller():
    buyer = by_key(
        compute(figs(contract_total=100000, retention_pct=2, retention_months=12), "甲", P)
    )
    assert (
        buyer["retention"].amount == pytest.approx(3000) and buyer["retention"].nature == "exposure"
    )
    assert compute(figs(contract_total=100000, retention_pct=5, retention_months=12), "甲", P) == []
    seller = by_key(
        compute(figs(contract_total=100000, retention_pct=10, retention_months=24), "乙", P)
    )
    # 100000 × 10% × 24/12 × 6% = 1200
    assert (
        seller["retention"].amount == pytest.approx(1200) and seller["retention"].nature == "sure"
    )


def test_seller_side_long_receivable_and_mirrored_penalty():
    items = by_key(
        compute(
            figs(
                contract_total=365000,
                payment_days=180,
                deposit_pct=40,
                yi_late_daily_pct=1,
                jia_late_daily_pct=0.05,
            ),
            "乙",
            P,
            [line("冷轧钢板", 10, 36500, 5)],
        )
    )
    # 我方收款：365000 × 60% × (180 − 60)/365 × 6% = 4320
    assert items["payment_capital"].amount == pytest.approx(4320)
    assert "price_overpay" not in items and "deposit_excess" not in items  # 只在我方付款时计算
    # 违约金：我方（乙）1% × 30 = 30% → 109500；对方 0.05% × 30 = 1.5% → 5475
    assert items["penalty_gap"].amount == pytest.approx(104025)


def test_default_delivery_days_is_stated_as_an_assumption():
    item = compute(figs(contract_total=100000, prepay_pct=50, payment_days=60), "甲", P)[0]
    # 100000 × 50% × (30 + 60)/365 × 6% = 739.73
    assert item.amount == pytest.approx(739.73)
    assert any("合同没写交付期" in a for a in item.assumptions)


def test_consistency_warning_when_total_does_not_match():
    two = [line("a", 10, 60), line("b", 20, 20)]
    assert quant.consistency_warnings(figs(contract_total=100000), two)
    assert not quant.consistency_warnings(figs(contract_total=1000), two)


def test_attach_puts_amounts_on_findings_and_marks_the_rest():
    items = compute(
        figs(
            contract_total=159300,
            deposit_pct=25,
            delivery_days=15,
            payment_days=60,
            jia_lump_pct=50,
        ),
        "甲",
        P,
    )
    for i in items:
        i.clause = {"deposit": "第六条", "penalty": "第八条", "payment": "第六条"}[i.topic]
    findings = [
        {"clause": "第六条", "risk_type": "deposit"},
        {"clause": "第八条", "risk_type": "penalty"},
        {"clause": "第十三条", "risk_type": "jurisdiction"},
        {"clause": "第一条", "risk_type": "unit_price"},
    ]
    out = attach(findings, items)
    assert out[0]["quant"] == {
        "status": "quantified",
        "keys": ["deposit_excess"],
        "amount": 7965.0,
        "reason": "",
    }
    # 违约金两个口径同组：挂在意见上的金额取较大的一项，不相加
    assert out[1]["quant"]["keys"] == ["penalty_gap", "penalty_over_cap"]
    assert out[1]["quant"]["amount"] == pytest.approx(159300 * 0.5)
    assert out[2]["quant"]["status"] == "unquantifiable" and "争议" in out[2]["quant"]["reason"]
    assert out[3]["quant"]["status"] == "unquantifiable" and out[3]["quant"]["amount"] is None
    assert "quant" not in findings[0]  # 不改传入的意见


def test_tenant_params_load_and_differ():
    hx, rf, yq = (quant.load_params(t) for t in ("huaxin", "ruifeng", "yunqi"))
    assert (hx.std_payment_days, rf.std_payment_days, yq.std_payment_days) == (60, 30, 45)
    assert (hx.price_alert_pct, rf.price_alert_pct, yq.price_alert_pct) == (10, 8, 15)
    assert hx.basis["capital_cost_rate_pct"] == "假设" and "第七条" in hx.basis["std_payment_days"]


# ---- 第一次全量评测后补的规则（ADR 014"第一次结果与修正"）----


def test_loss_based_or_third_party_figures_become_unquantifiable_not_needs_check():
    text = (
        "第五条 价款支付\n甲方在收到其下游客户支付的相应款项后十日内向乙方付款。\n"
        "第八条 违约责任\n任何一方违约的，应当按照守约方因此遭受的实际损失的百分之一百三十向守约方支付违约金。\n"
    )
    clauses = {"第五条": text.split("第八条")[0], "第八条": "第八条" + text.split("第八条")[1]}
    figures = check_figures(
        [
            {
                "field": "payment_days",
                "value": 10,
                "quote": "甲方在收到其下游客户支付的相应款项后十日内向乙方付款",
            },
            {
                "field": "jia_lump_pct",
                "value": 0,
                "quote": "应当按照守约方因此遭受的实际损失的百分之一百三十向守约方支付违约金",
            },
        ],
        text,
        clauses,
    )
    assert [f["soft"] for f in figures] == ["payment", "penalty_jia"] and not any(
        f["ok"] for f in figures
    )
    flags, dropped = quant.check_flags([], figures, text, clauses)
    assert [(u["topic"], u["source"], u["clause"]) for u in flags] == [
        ("payment", "rule", "第五条"),
        ("penalty_jia", "rule", "第八条"),
    ]
    items = by_key(compute(figures + figs(contract_total=100000), "甲", P, uncomputable=flags))
    assert items["payment_capital"].status == "unquantifiable"
    assert (
        items["penalty_gap"].status == "unquantifiable" and "实际损失" in items["penalty_gap"].note
    )


def test_deposit_copied_again_as_prepayment_is_not_double_counted():
    text = "第六条 定金\n甲方应当在合同签订后五日内向乙方支付合同总额百分之四十的定金，定金在最后一期付款时抵作价款。\n"
    quote = "甲方应当在合同签订后五日内向乙方支付合同总额百分之四十的定金"
    figures = check_figures(
        [
            {"field": "deposit_pct", "value": 40, "quote": quote},
            {"field": "prepay_pct", "value": 40, "quote": quote},
        ],
        text,
        {"第六条": text},
    )
    assert figures[0]["ok"] and figures[1]["soft"] == "ignore"
    item = by_key(
        compute(figures + figs(contract_total=100000, delivery_days=30, payment_days=60), "甲", P)
    )
    # 只按定金 40% 计一次：100000 × 40% × 90/365 × 6% = 591.78
    assert item["payment_capital"].amount == pytest.approx(591.78)


def test_check_flags_drops_mislabelled_and_to_be_negotiated_penalties():
    text = (
        "第七条 质量保证金\n质量保证金的比例和期限由双方另行约定。\n"
        "第八条 违约责任\n甲方逾期付款的，每逾期一日，应当按合同总额的千分之三支付违约金。乙方逾期的，双方另行协商。\n"
    )
    raw = [
        {"topic": "penalty_yi", "quote": "乙方逾期的，双方另行协商。", "reason": "没有约定比例"},
        {"topic": "deposit", "quote": "质量保证金的比例和期限由双方另行约定", "reason": "张冠李戴"},
        {
            "topic": "retention",
            "quote": "质量保证金的比例和期限由双方另行约定",
            "reason": "没有写明比例",
        },
        {"topic": "payment", "quote": "合同里没有这句话", "reason": "编的"},
    ]
    kept, dropped = quant.check_flags(raw, [], text, {"第七条": text.split("第八条")[0]})
    assert [u["topic"] for u in kept] == ["retention"] and kept[0]["clause"] == "第七条"
    assert [u["why"] for u in dropped] == [
        "只写了另行协商，等于没有约定，按 0 计",
        "摘录和这个主题对不上",
        "摘录在合同原文里找不到",
    ]
