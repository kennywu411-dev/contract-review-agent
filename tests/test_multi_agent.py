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


def test_subagent_brief_restates_perspective_rule():
    # 小样里专职审查员把"只约束对方"的违约金/免责当成我方风险（视角错误 2/4）：片段里要重申视角规则
    llm = RoutedLLM()
    review_contract_multi(CONTRACT, "huaxin", llm, lambda: box())
    sub = [
        q["messages"][0]["content"]
        for q in llm.requests
        if "plan_review" not in {t["name"] for t in q["tools"]}
    ]
    assert sub and all("只约束对方、保护我方" in c for c in sub)


def test_split_contract_accepts_bracket_headings():
    from contract_review.multi_agent import split_contract

    header, clauses = split_contract(
        "合同\n第一条【价款】\n单价\n第二条 付款\n乙方\n第3条：交付\n第四条、验收\n依照第二条约定执行"
    )
    assert list(clauses) == ["第一条", "第二条", "第三条", "第四条"]
    assert "依照第二条约定执行" in clauses["第四条"]


def test_unparseable_contract_falls_back_to_single_agent():
    # 审查 C1：识别不出条款时不能产出"空审查"，回退单 Agent 审全文
    contract = "采购合同\n一、价款\n单价3600元\n二、验收\n交付即视为合格"
    plan = {"our_side": "甲", "assignments": [{"clause": "第一条", "groups": ["pricing"]}]}

    class Fallback(RoutedLLM):
        def create(self, **kw):
            from types import SimpleNamespace as NS

            names = {t["name"] for t in kw["tools"]}
            if "plan_review" in names:
                return super().create(**kw)
            self.requests.append(kw)
            return NS(
                content=[
                    tool(
                        "submit_review",
                        {
                            "our_side": "甲",
                            "summary": "单",
                            "findings": [f("第一条", "unit_price")],
                        },
                    )
                ],
                stop_reason="tool_use",
                usage=usage(),
            )

    r = review_contract_multi(contract, "huaxin", Fallback(plan=plan), lambda: box())
    assert r.trace["fallback_single"] and r.findings and r.findings[0]["risk_type"] == "unit_price"


def test_failed_group_does_not_fail_review():
    # 审查 I1：一组异常，其余两组的结果照常汇总，标注哪组没审
    class Boom(RoutedLLM):
        def create(self, **kw):
            content = kw["messages"][0]["content"]
            if "【责任与违约】" in content:
                raise RuntimeError("timeout")
            return super().create(**kw)

    extra = {"pricing": {"our_side": "甲", "summary": "p", "findings": [f("第四条", "inspection")]}}
    r = review_contract_multi(
        CONTRACT, "huaxin", Boom(extra=extra), lambda: box([ch("law", "common", "法条")])
    )
    assert r.kind == "review" and [x["clause"] for x in r.findings] == ["第四条"]
    assert r.trace["failed_groups"] == ["liability"] and "责任与违约" in r.summary


def test_invalid_citation_kept_visible():
    # 审查 I2：子 agent 引用了没检索过的编号，要进 invalid_citations，而不是悄悄丢掉
    extra = {
        "pricing": {
            "our_side": "甲",
            "summary": "p",
            "findings": [f("第四条", "inspection", cites=("C1", "C9"))],
        }
    }
    r = review_contract_multi(
        CONTRACT, "huaxin", RoutedLLM(extra=extra), lambda: box([ch("law", "common", "法条")])
    )
    assert r.invalid_citations == ["pricing:C9"] and all(
        c in r.citations for c in r.findings[0]["citations"]
    )


def test_missing_or_hallucinated_assignments_do_not_trigger_fallback():
    # 主审常写"缺失（保密条款）"提示缺条款，或多写一个不存在的条号：都不是解析失败，不回退单 Agent
    plan = PLAN | {
        "assignments": PLAN["assignments"]
        + [
            {"clause": "缺失（保密条款）", "groups": ["rights"]},
            {"clause": "第二十条", "groups": ["liability"]},
        ]
    }
    r = review_contract_multi(CONTRACT, "huaxin", RoutedLLM(plan=plan), lambda: box())
    assert not r.trace.get("fallback_single") and r.trace["ignored_assignments"] == ["第二十条"]


DEF_CONTRACT = """数据产品开发合同
甲方（委托方）：云启科技有限公司
乙方（开发方）：杭州某某软件有限公司
第一条 定义
本合同中下列用语的含义是：（一）“交付成果”，指乙方最终交付的可执行程序，不包括源代码。
第二条 标的、数量和价款
单价：人民币1800元/人天。
第三条 知识产权
交付成果的知识产权归甲方所有。
第四条 争议解决
由乙方住所地人民法院管辖。
"""


def _sub_contents(llm):
    return {
        g: next(
            q["messages"][0]["content"]
            for q in llm.requests
            if f"【{GROUPS[g]['label']}】" in q["messages"][0]["content"]
        )
        for g in GROUPS
    }


def test_definitions_clause_sent_to_every_group():
    # ADR 013：主审从不分派"定义"条款，靠定义构成的组合风险（交付成果不含源代码）全部漏检
    plan = {
        "our_side": "甲",
        "assignments": [
            {"clause": "第二条", "groups": ["pricing"]},
            {"clause": "第三条", "groups": ["rights"]},
            {"clause": "第四条", "groups": ["rights"]},
        ],
    }
    llm = RoutedLLM(plan=plan)
    review_contract_multi(DEF_CONTRACT, "yunqi", llm, lambda: box())
    subs = _sub_contents(llm)
    # 分到条款的组都带上定义条款；没分到条款的组（责任组）不需要
    assert "不包括源代码" in subs["pricing"] and "不包括源代码" in subs["rights"]
    assert "不包括源代码" not in subs["liability"]


def test_unassigned_clause_routed_by_keywords():
    # ADR 013：主审漏分的条款（保密、争议解决）按关键词兜底分组，不能没人审
    plan = {"our_side": "甲", "assignments": [{"clause": "第二条", "groups": ["pricing"]}]}
    llm = RoutedLLM(plan=plan)
    r = review_contract_multi(DEF_CONTRACT, "yunqi", llm, lambda: box())
    subs = _sub_contents(llm)
    assert "由乙方住所地人民法院管辖" in subs["rights"]
    assert "知识产权归甲方所有" in subs["rights"]
    assert "由乙方住所地人民法院管辖" not in subs["pricing"]
    assert r.trace["auto_assigned"] == {"第三条": ["rights"], "第四条": ["rights"]}


def test_briefs_ask_for_threshold_and_cross_clause_checks():
    llm = RoutedLLM()
    review_contract_multi(CONTRACT, "huaxin", llm, lambda: box())
    plan_msg = next(
        q["messages"][0]["content"]
        for q in llm.requests
        if "plan_review" in {t["name"] for t in q["tools"]}
    )
    assert "每个条款都要分派" in plan_msg
    assert all("红线" in c and "结合" in c for c in _sub_contents(llm).values())
