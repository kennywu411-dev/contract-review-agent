"""金额测算接入审查流程：模型只抄数字（假模型），规则核对原文、查台账、计算，再挂到审查意见上。"""

from contract_review.multi_agent import review_contract_multi
from contract_review.quant_extract import (
    EXTRACT_SYSTEM,
    LEDGER_SQL,
    SUBMIT_TERMS,
    apply_to_findings,
    match_ledger,
    run_quant,
    topic_outcomes,
)
from contract_review.review_agent import review_contract
from tests.test_agent import FakeLLM, tool
from tests.test_multi_agent import RoutedLLM
from tests.test_tools import FakeSQL, box

CONTRACT = """工业品买卖合同
甲方（买受人）：华信制造有限公司
乙方（出卖人）：苏州恒锐金属材料有限公司
第一条 标的、数量和价款
品名：冷轧钢板；数量：100；单价：人民币6200元/吨（含税）；合同总额：人民币620,000元。
第二条 交付
乙方应当在合同签订后三十日内向甲方交付货物。
第三条 价款支付
合同签订后五日内，甲方向乙方支付合同总额的百分之三十作为预付款；余款在验收合格后十五日内付清。
第四条 定金
甲方应当在合同签订后三日内向乙方支付合同总额百分之三十的定金。
第五条 争议解决
因本合同发生的争议，由乙方住所地人民法院管辖。
"""
LINE = {
    "item": "冷轧钢板",
    "unit": "吨",
    "unit_price": 6200,
    "quantity": 100,
    "quote": "品名：冷轧钢板；数量：100；单价：人民币6200元/吨",
}
TERMS = {
    "lines": [LINE],
    "figures": [
        {"field": "contract_total", "value": 620000, "quote": "合同总额：人民币620,000元"},
        {
            "field": "delivery_days",
            "value": 30,
            "quote": "乙方应当在合同签订后三十日内向甲方交付货物",
        },
        {
            "field": "prepay_pct",
            "value": 30,
            "quote": "甲方向乙方支付合同总额的百分之三十作为预付款",
        },
        {"field": "payment_days", "value": 15, "quote": "余款在验收合格后十五日内付清"},
        {"field": "deposit_pct", "value": 30, "quote": "支付合同总额百分之三十的定金"},
    ],
    "uncomputable": [],
}
LEDGER = FakeSQL(
    {"columns": ["item", "n", "avg"], "rows": [["冷轧钢板", 10, 4821.5], ["液压油", 10, 1659.4]]}
)


def test_run_quant_extracts_verifies_looks_up_ledger_and_computes():
    llm = FakeLLM([[tool("submit_terms", TERMS)]])
    steps = []
    q = run_quant(CONTRACT, "huaxin", "甲", llm, LEDGER, steps.append)
    req = llm.requests[0]
    assert req["tool_choice"] == {"type": "tool", "name": "submit_terms"}  # 强制结构化输出
    assert req["tools"] == [SUBMIT_TERMS] and req["system"][0]["text"] == EXTRACT_SYSTEM
    assert "main.purchase_history WHERE tenant IN ('huaxin', 'common')" in LEDGER.calls[-1]
    assert LEDGER_SQL in LEDGER.calls[-1]  # SQL 是固定的，不拼模型给的品名
    assert q["status"] == "ok" and all(f["ok"] for f in q["figures"])
    assert q["lines"][0]["ok"] and q["lines"][0]["ledger"] == {
        "item": "冷轧钢板",
        "n": 10,
        "avg": 4821.5,
    }
    got = topic_outcomes(q)
    assert got["unit_price"] == ("amount", 137850.0)
    assert got["deposit"] == ("amount", 62000.0)  # 620000 × (30% − 20%)
    # 资金占用：620000 × 60% × 90/365 × 6% + 620000 × 40% × 45/365 × 6% = 5503.56 + 1834.52
    assert got["payment"] == ("amount", 7338.08)
    assert q["summary"]["worst_total"] == 137850.0 + 62000.0 + 7338.08
    assert [p["key"] for p in q["summary"]["priorities"]][0] == "price_overpay"
    assert steps and steps[0]["agent"] == "quant"
    assert [c["name"] for c in steps[0]["tool_calls"]] == ["submit_terms", "ledger_lookup"]
    by_field = {f["field"]: f["clause"] for f in q["figures"]}
    assert by_field["deposit_pct"] == "第四条" and q["lines"][0]["clause"] == "第一条"


def test_wrong_number_or_made_up_quote_gives_no_amount():
    bad = TERMS | {
        "figures": [
            *TERMS["figures"][:4],
            {"field": "deposit_pct", "value": 20, "quote": "支付合同总额百分之三十的定金"},
        ]
    }
    q = run_quant(CONTRACT, "huaxin", "甲", FakeLLM([[tool("submit_terms", bad)]]), LEDGER)
    got = topic_outcomes(q)
    assert got["deposit"][0] == "needs_check" and got["payment"][0] == "needs_check"
    assert got["unit_price"] == ("amount", 137850.0)  # 没受影响的项目照常算
    assert q["summary"]["needs_check"] == 2


def test_model_failure_does_not_raise():
    class Boom:
        model = "claude-haiku-5-5"

        def create(self, **kw):
            raise TimeoutError("接口超时")

    q = run_quant(CONTRACT, "huaxin", "甲", Boom(), LEDGER)
    assert q["status"] == "error" and "TimeoutError" in q["error"] and q["items"] == []
    out = apply_to_findings([{"clause": "第四条", "risk_type": "deposit"}], q)
    assert out[0]["quant"]["status"] == "needs_check"


def test_match_ledger_needs_a_single_unambiguous_item():
    table = {
        "rows": {
            "冷轧钢板": (10, 4821.5),
            "液压油": (10, 1659.4),
            "粳米": (10, 5213.6),
            "米": (1, 1.0),
        }
    }
    assert match_ledger("冷轧钢板", table)["avg"] == 4821.5
    assert match_ledger("SPCC 冷轧钢板 1.0mm", table)["item"] == "冷轧钢板"
    assert match_ledger("粳米", table)["item"] == "粳米"  # 完全相同优先，不因为"米"也是子串而放弃
    assert match_ledger("轴承", table) is None and match_ledger("", table) is None
    assert (
        match_ledger("油", {"rows": {"液压油": (1, 1.0), "润滑油": (1, 1.0)}}) is None
    )  # 对上两个：不比


def test_single_agent_review_attaches_amounts_to_findings():
    review = {
        "our_side": "甲",
        "summary": "两处风险",
        "findings": [
            {
                "clause": "第四条",
                "quote": "支付合同总额百分之三十的定金",
                "risk_type": "deposit",
                "level": "中",
                "reason": "超过 20%",
                "suggestion": "改为 20%",
                "citations": [],
                "needs_human_review": False,
            },
            {
                "clause": "第五条",
                "quote": "由乙方住所地人民法院管辖",
                "risk_type": "jurisdiction",
                "level": "中",
                "reason": "异地",
                "suggestion": "改为甲方住所地",
                "citations": [],
                "needs_human_review": False,
            },
        ],
    }
    llm = FakeLLM([[tool("submit_review", review)], [tool("submit_terms", TERMS)]])
    r = review_contract(CONTRACT, "huaxin", llm, box(sql=LEDGER))
    assert r.steps == 1  # 审查 agent 的调用次数不含金额测算
    assert r.findings[0]["quant"]["status"] == "quantified"
    assert r.findings[0]["quant"]["amount"] == 62000.0
    assert r.findings[1]["quant"]["status"] == "unquantifiable"
    assert r.quant["summary"]["worst_total"] > 0 and "step" not in r.quant
    assert r.trace["steps"][-1]["agent"] == "quant" and r.trace["result"]["quant"] == r.quant
    assert r.cost_usd > r.trace["steps"][0]["cost_usd"]  # 测算的花费计入本次审查

    r2 = review_contract(
        CONTRACT, "huaxin", FakeLLM([[tool("submit_review", review)]]), box(), quant=False
    )
    assert r2.quant == {} and "quant" not in r2.findings[0]


def test_multi_agent_runs_quant_in_parallel_and_attaches():
    class WithQuant(RoutedLLM):
        def create(self, **kw):
            if kw["tools"][0]["name"] == "submit_terms":
                from types import SimpleNamespace as NS

                from tests.test_agent import usage

                with self.lock:
                    self.requests.append(kw)
                return NS(
                    content=[tool("submit_terms", TERMS)], stop_reason="tool_use", usage=usage()
                )
            return super().create(**kw)

    plan = {
        "our_side": "甲",
        "assignments": [
            {"clause": "第一条", "groups": ["pricing"]},
            {"clause": "第三条", "groups": ["pricing"]},
            {"clause": "第四条", "groups": ["pricing", "liability"]},
            {"clause": "第五条", "groups": ["rights"]},
        ],
    }
    finding = {
        "clause": "第四条",
        "quote": "支付合同总额百分之三十的定金",
        "risk_type": "deposit",
        "level": "中",
        "reason": "超过 20%",
        "suggestion": "改为 20%",
        "citations": [],
        "needs_human_review": False,
    }
    llm = WithQuant(plan, {"pricing": {"our_side": "甲", "summary": "x", "findings": [finding]}})
    events = []
    r = review_contract_multi(
        CONTRACT, "huaxin", llm, lambda: box(sql=LEDGER), on_step=events.append
    )
    assert r.kind == "review" and r.findings[0]["quant"]["amount"] == 62000.0
    assert (
        r.quant["status"] == "ok" and r.quant["summary"]["priorities"][0]["key"] == "price_overpay"
    )
    assert any(e.get("agent") == "quant" for e in events)
    assert r.trace["steps"][-1]["agent"] == "quant"

    off = review_contract_multi(CONTRACT, "huaxin", WithQuant(plan), lambda: box(), quant=False)
    assert off.quant == {}


def test_unknown_tenant_params_do_not_break_review():
    q = run_quant(
        CONTRACT, "no-such-company", "甲", FakeLLM([[tool("submit_terms", TERMS)]]), LEDGER
    )
    assert q["status"] == "error" and q["items"] == [] and q["cost_usd"] == 0.0
