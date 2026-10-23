import threading

from contract_review.multi_agent import GROUPS, merge_findings, review_contract_multi
from tests.test_agent import tool, usage
from tests.test_tools import FakeSQL, box, ch

CONTRACT = """工业品买卖合同
甲方（买受人）：华信制造有限公司
乙方（出卖人）：常州驰远电机有限公司
第一条 标的、数量和价款
单价：人民币3600元/台。
第四条 检验与验收
乙方交付即视为甲方验收合格。
第八条 违约责任
甲方逾期付款的，每日按合同总额百分之一支付违约金。
第十三条 争议解决
由乙方住所地人民法院管辖。
"""

PLAN = {
    "our_side": "甲",
    "assignments": [
        {"clause": "第一条", "groups": ["pricing"]},
        {"clause": "第四条", "groups": ["pricing"]},
        {"clause": "第八条", "groups": ["liability"]},
        {"clause": "第十三条", "groups": ["rights"]},
    ],
}


def f(clause, rtype, level="高", cites=("C1",)):
    return {
        "clause": clause,
        "quote": "",
        "risk_type": rtype,
        "level": level,
        "reason": "r",
        "suggestion": "s",
        "citations": list(cites),
        "needs_human_review": False,
    }


class RoutedLLM:
    """按请求内容决定回复：主审规划、三个专职组各自先检索再提交。线程安全。"""

    model = "claude-haiku-5-5"

    def __init__(self, plan=PLAN, extra=None):
        self.plan, self.extra = plan, extra or {}
        self.lock = threading.Lock()
        self.requests = []

    def create(self, **kw):
        from types import SimpleNamespace as NS

        with self.lock:
            self.requests.append(kw)
        content = kw["messages"][0]["content"]
        names = {t["name"] for t in kw["tools"]}
        if "plan_review" in names:
            reply = [tool("plan_review", self.plan, "p")]
        else:
            group = next(g for g in GROUPS if f"【{GROUPS[g]['label']}】" in content)
            if len(kw["messages"]) == 1:
                reply = [tool("search_docs", {"query": group}, f"s-{group}")]
            else:
                reply = [
                    tool(
                        "submit_review",
                        self.extra.get(group, {"our_side": "甲", "summary": group, "findings": []}),
                        f"x-{group}",
                    )
                ]
        stop = "tool_use"
        return NS(content=reply, stop_reason=stop, usage=usage())


def test_groups_cover_all_risk_types_once():
    from contract_review.contract_evalset import RISK_TYPES

    seen = [t for g in GROUPS.values() for t in g["types"]]
    assert sorted(seen) == sorted(RISK_TYPES) and len(seen) == len(set(seen))
    assert GROUPS["pricing"]["sql"] and not GROUPS["liability"]["sql"]


def test_subagents_only_see_assigned_clauses_and_merge():
    extra = {
        "pricing": {"our_side": "甲", "summary": "p", "findings": [f("第四条", "inspection")]},
        "liability": {
            "our_side": "甲",
            "summary": "l",
            "findings": [f("第八条", "penalty"), f("第八条", "jurisdiction")],
        },
        "rights": {
            "our_side": "甲",
            "summary": "r",
            "findings": [f("第十三条", "jurisdiction", "中")],
        },
    }
    llm = RoutedLLM(extra=extra)
    r = review_contract_multi(CONTRACT, "huaxin", llm, lambda: box([ch("law", "common", "法条")]))
    assert r.kind == "review" and r.our_side == "甲"
    sub_first = [
        q["messages"][0]["content"]
        for q in llm.requests
        if "plan_review" not in {t["name"] for t in q["tools"]}
    ]
    liab = next(c for c in sub_first if "【责任与违约】" in c)
    assert "第八条 违约责任" in liab and "乙方交付即视为甲方验收合格" not in liab
    assert "第四条 检验与验收" in liab  # 全部条款标题都给，用于发现缺失条款
    clauses = [(x["clause"], x["risk_type"]) for x in r.findings]
    # 越界的类型（责任组报管辖）被丢弃，其余按条款号排序
    assert clauses == [
        ("第四条", "inspection"),
        ("第八条", "penalty"),
        ("第十三条", "jurisdiction"),
    ]
    # 每个子 agent 的 C1 重新编号成全局唯一，且都能在 citations 里找到
    cites = [c for x in r.findings for c in x["citations"]]
    assert len(set(cites)) == 3 and all(c in r.citations for c in cites)
    assert r.trace["task"] == "review_multi" and r.trace["out_of_scope"] == 1
    assert {s.get("agent") for s in r.trace["steps"]} == {
        "planner",
        "pricing",
        "liability",
        "rights",
    }


def test_pricing_group_gets_sql_tool_others_do_not():
    llm = RoutedLLM()
    review_contract_multi(
        CONTRACT, "huaxin", llm, lambda: box(sql=FakeSQL({"columns": [], "rows": []}))
    )
    for q in llm.requests:
        names = {t["name"] for t in q["tools"]}
        content = q["messages"][0]["content"]
        if "【价款与履约】" in content:
            assert "run_sql" in names
        elif "plan_review" not in names:
            assert "run_sql" not in names


def test_planner_clarification_short_circuits():
    class Ask(RoutedLLM):
        def create(self, **kw):
            from types import SimpleNamespace as NS

            self.requests.append(kw)
            return NS(
                content=[
                    tool(
                        "ask_clarification",
                        {"question": "我方是甲方还是乙方？", "missing": ["我方"]},
                    )
                ],
                stop_reason="tool_use",
                usage=usage(),
            )

    llm = Ask()
    r = review_contract_multi(CONTRACT, "huaxin", llm, lambda: box())
    assert r.kind == "clarify" and len(llm.requests) == 1


def test_merge_keeps_higher_level_and_unions_citations():
    merged = merge_findings(
        [f("第四条", "inspection", "中", ["C1"]), f("第四条", "inspection", "高", ["C2"])]
    )
    assert (
        len(merged) == 1 and merged[0]["level"] == "高" and merged[0]["citations"] == ["C2", "C1"]
    )
