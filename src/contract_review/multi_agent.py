"""多 Agent 合同审查（ADR 012）：主审规划 → 三个专职审查员并行 → 汇总去重 → 沿用原来的依据校验。

- 主审（planner）：一次调用判断我方立场（看不出就反问），把每个条款分派给一个或多个专职组；
  不审具体条款。
- 专职组只拿到分给它的条款原文 + 全部条款标题（用来发现"缺少某个条款"）+ 合同首部；
  只有价款与履约组带查台账工具。越界报出的类型丢弃（由负责的组报）。
- 汇总：各组的引用编号重新编成全局唯一，按（条款, 类型）去重，保留更高等级、合并依据，按条款号排序。

与单 Agent 共用同一段系统提示词（提示缓存可复用），组别和范围写在用户消息里。
"""

from __future__ import annotations

import re
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor

from contract_review.agent import tool_loop
from contract_review.config import TENANTS
from contract_review.contract_evalset import RISK_TYPES
from contract_review.prompts import review_system_prompt
from contract_review.review_agent import (
    REVIEW_TOOLS,
    ReviewResult,
    _defang,
    _finish,
    normalize_clause,
)
from contract_review.tools import _tool

GROUPS = {
    "pricing": {
        "label": "价款与履约",
        "types": ["payment", "deposit", "unit_price", "inspection"],
        "sql": True,
    },
    "liability": {
        "label": "责任与违约",
        "types": ["penalty", "liability", "termination"],
        "sql": False,
    },
    "rights": {
        "label": "权利与争议",
        "types": ["confidentiality", "ip", "jurisdiction", "auto_renew"],
        "sql": False,
    },
}
# 主审漏分的条款按关键词兜底分组（ADR 013：v2 评测里有保密条款没人审）
ROUTE_KEYWORDS = {
    "pricing": ("付款", "价款", "定金", "预付", "单价", "发票", "检验", "验收", "结算"),
    "liability": ("违约", "赔偿", "责任", "解除", "终止"),
    "rights": ("保密", "知识产权", "著作权", "管辖", "仲裁", "法院", "续期", "期限", "期满"),
}
DEFINITION_HEADINGS = ("定义", "释义", "用语")
SUB_MAX_STEPS = 6
SUB_MAX_TOKENS = 6000
_TOOLS = {t["name"]: t for t in REVIEW_TOOLS}
PLAN_TOOL = _tool(
    "plan_review",
    "提交审查分工：我方立场和每个条款交给哪些专职组。调用后本轮结束。",
    {
        "our_side": {"type": "string", "enum": ["甲", "乙"]},
        "assignments": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "clause": {"type": "string", "description": "条款号，如“第四条”"},
                    "groups": {"type": "array", "items": {"type": "string", "enum": list(GROUPS)}},
                },
                "required": ["clause", "groups"],
                "additionalProperties": False,
            },
        },
    },
)
# 行首"第X条"就是条款标题（"第一条【价款】""第3条：交付"都算）；
# 后面紧跟"的/约定/规定/所…"的是正文引用
_HEAD = re.compile(r"^\s*(第[零一二三四五六七八九十百\d]+条)(?!的|约定|规定|所|中|之|项下|第)")
_LEVEL = {"高": 0, "中": 1, "低": 2}
_CN = {c: i for i, c in enumerate("零一二三四五六七八九")}


def split_contract(text: str) -> tuple[str, dict[str, str]]:
    """返回（首部, {条款号: 条款全文}）。"""
    header: list[str] = []
    clauses: dict[str, list[str]] = {}
    cur = None
    for line in text.splitlines():
        m = _HEAD.match(line)
        if m:
            cur = normalize_clause(m.group(1)) or m.group(1)
            clauses[cur] = [line]
        elif cur:
            clauses[cur].append(line)
        else:
            header.append(line)
    return "\n".join(header), {k: "\n".join(v) for k, v in clauses.items()}


def _clause_no(clause: str | None) -> int:
    if not clause:
        return 10_000
    body = clause[1:-1]
    if body.isdigit():
        return int(body)
    total, cur = 0, 0
    for ch in body:
        if ch in _CN:
            cur = _CN[ch]
        elif ch == "十":
            total += (cur or 1) * 10
            cur = 0
        elif ch == "百":
            total += (cur or 1) * 100
            cur = 0
    return total + cur


def _arabic(no: str) -> str:
    """ "第十二条" → "第12条"，用来在原文里找阿拉伯数字写法的条号。"""
    return f"第{_clause_no(no)}条"


def merge_findings(findings: list[dict]) -> list[dict]:
    """按（条款, 类型）去重：保留等级更高的那条，依据合并（高等级的在前），转人工取"或"。"""
    best: dict[tuple, dict] = {}
    for f in sorted(findings, key=lambda x: _LEVEL.get(x["level"], 3)):
        key = (f["clause"], f["risk_type"])
        if key not in best:
            best[key] = dict(f)
            continue
        kept = best[key]
        kept["citations"] = list(dict.fromkeys(kept["citations"] + f["citations"]))
        kept["needs_human_review"] = kept["needs_human_review"] or f["needs_human_review"]
    return sorted(best.values(), key=lambda f: (_clause_no(f["clause"]), _LEVEL.get(f["level"], 3)))


class _Locked:
    """检索模型和 DataPilot 连接在线程间共用：调用加锁串行（检索很快，耗时主要在模型调用）。"""

    def __init__(self, obj, lock: threading.Lock) -> None:
        self._obj, self._lock = obj, lock

    def __getattr__(self, name):
        attr = getattr(self._obj, name)
        if not callable(attr):
            return attr

        def call(*a, **kw):
            with self._lock:
                return attr(*a, **kw)

        return call


def _plan(contract: str, tenant: str, llm, toolbox, trace: dict, on_step, hint: str = "") -> tuple:
    company = TENANTS[tenant].removesuffix("（构造）")
    groups = "；".join(
        f"{g}（{v['label']}：{'、'.join(RISK_TYPES[t] for t in v['types'])}）"
        for g, v in GROUPS.items()
    )
    content = (
        f"你是主审，不逐条审查，只做分工。先判断{company}（我方）是甲方还是乙方，看不出就调用 "
        f"ask_clarification；再把每个条款分派给可能涉及的专职组（可以多个）：{groups}。"
        "每个条款都要分派，不要只看标题：风险可能藏在发票、人员、变更、附件等标题看似无关的条款里；"
        "一个条款同时涉及几类风险（例如定金和违约金、付款和验收）就分给几个组。"
        "定义条款会自动发给所有组，不必分派。完成后调用 plan_review。\n"
        f"<合同>\n{_defang(contract).strip()}\n</合同>"
    )
    if hint:
        content += f"\n用户补充：{_defang(hint)}"
    sub = {"steps": []}
    terminal, cost, steps = tool_loop(
        llm=llm,
        system_text=review_system_prompt(tenant),
        tools=[PLAN_TOOL, _TOOLS["ask_clarification"]],
        terminal_tools={"plan_review", "ask_clarification"},
        user_content=content,
        toolbox=toolbox,
        trace=sub,
        max_steps=2,
        on_step=on_step,
    )
    for s in sub["steps"]:
        trace["steps"].append(s | {"agent": "planner"})
    return terminal, cost, steps


def _subagent(group, side, header, headings, parts, contract, tenant, llm, toolbox, on_step):
    g = GROUPS[group]
    company = TENANTS[tenant].removesuffix("（构造）")
    types = "、".join(f"{t}（{RISK_TYPES[t]}）" for t in g["types"])
    content = (
        f"【{g['label']}】你是专职审查员，只负责这些风险类型：{types}。"
        f"我方已确认是{side}方（{company}）。只审查下面分给你的条款；全部条款标题也列出来了，"
        "用来判断有没有缺少你负责的条款。其他类型的问题不要报（由别的审查员负责）。"
        # 片段里看不到全局，容易把对我方有利的单方条款当成风险（ADR 012 小样）：这里重申视角规则
        f"判断每一条前先想清楚它约束谁、保护谁：只约束对方、保护我方（{side}方）的条款，"
        "即使写得过严、可能被法院调整或认定无效，也不要报。"
        # ADR 013：v2 评测里专职组漏掉了刚越过规范红线的数值，以及要结合定义/其他条款才看得出的风险
        "凡是比例、天数、年限、金额这类数值，都要检索本公司规范的对应条款，逐项和红线对照，刚越过一点也算；"
        "还要结合定义条款和其他条款的约定看（例如定义把某个用语限定得很窄、两个条款互为前提或叠加适用）。"
        "完成后调用 submit_review。\n"
        f"<合同首部>\n{_defang(header).strip()}\n</合同首部>\n"
        f"<全部条款标题>\n{headings}\n</全部条款标题>\n"
        f"<合同>\n{_defang(parts).strip()}\n</合同>"
    )
    tools = (
        [_TOOLS["search_docs"]]
        + ([_TOOLS["run_sql"]] if g["sql"] else [])
        + [_TOOLS["submit_review"]]
    )
    sub_trace = {"steps": []}
    terminal, cost, steps = tool_loop(
        llm=llm,
        system_text=review_system_prompt(tenant),
        tools=tools,
        terminal_tools={"submit_review"},
        user_content=content,
        toolbox=toolbox,
        trace=sub_trace,
        max_steps=SUB_MAX_STEPS,
        max_tokens=SUB_MAX_TOKENS,
        on_step=(lambda rec: on_step(rec | {"agent": group})) if on_step else None,
    )
    res = ReviewResult(tenant, "fallback")
    if terminal is not None:
        _finish(res, terminal, toolbox, contract)
    return res, cost, steps, sub_trace


def _renumber(
    res: ReviewResult, citations: dict, offsets: dict, toolbox, group: str, invalid: list[str]
) -> list[dict]:
    """各组编号重编成全局唯一；引用了本组没检索过的编号，记进 invalid（带组名）再从意见里剔除。"""
    mapping = {}
    for cid, c in toolbox.citations.items():
        kind = cid[0]
        offsets[kind] += 1
        new = f"{kind}{offsets[kind]}"
        mapping[cid] = new
        citations[new] = c
    out = []
    for f in res.findings:
        invalid += [f"{group}:{c}" for c in f["citations"] if c not in mapping]
        out.append(f | {"citations": [mapping[c] for c in f["citations"] if c in mapping]})
    return out


def review_contract_multi(
    contract: str,
    tenant: str,
    llm,
    toolbox_factory,
    on_step=None,
    contract_id: str = "",
    hint: str = "",
) -> ReviewResult:
    """toolbox_factory() -> ToolBox（每个 agent 一个，检索器和查表后端共用并加锁）。

    hint：用户补充的信息（如首轮反问后答"我方是甲方"），交给主审判断立场。
    """
    t0 = time.perf_counter()
    # 检索和查表各一把锁：一次慢查询不会卡住另外两组的检索（审查 M5）
    search_lock, sql_lock = threading.Lock(), threading.Lock()

    def make_box():
        box = toolbox_factory()
        box.retriever = _Locked(box.retriever, search_lock)
        box.sql = _Locked(box.sql, sql_lock)
        return box

    trace = {
        "trace_id": uuid.uuid4().hex[:12],
        "task": "review_multi",
        "model": llm.model,
        "contract_id": contract_id,
        "question": f"审查合同 {contract_id}（多 Agent）".strip(),
        "tenant": tenant,
        "steps": [],
    }
    result = ReviewResult(tenant, "fallback")
    planner_box = make_box()
    terminal, cost, steps = _plan(contract, tenant, llm, planner_box, trace, on_step, hint)
    result.cost_usd, result.steps = cost, steps
    if terminal is None or terminal.name == "ask_clarification":
        if terminal is not None:
            _finish(result, terminal, planner_box, contract)
        trace["end"] = terminal.name if terminal is not None else "planner_failed"
        return _close(result, trace, t0, {})

    side = terminal.input.get("our_side", "")
    header, clauses = split_contract(contract)
    headings = "\n".join(text.splitlines()[0] for text in clauses.values())
    assigned: dict[str, list[str]] = {g: [] for g in GROUPS}
    for a in terminal.input.get("assignments", []):
        no = normalize_clause(a.get("clause", ""))
        for g in a.get("groups", []):
            if no in clauses and g in assigned and no not in assigned[g]:
                assigned[g].append(no)
    # 定义条款发给所有组；主审漏分的条款按关键词兜底（ADR 013）
    defs = [
        no
        for no, text in clauses.items()
        if any(h in text.splitlines()[0] for h in DEFINITION_HEADINGS)
    ]
    covered = {no for v in assigned.values() for no in v}
    auto: dict[str, list[str]] = {}
    for no, text in clauses.items():
        if no in covered or no in defs:
            continue
        gs = [g for g, kws in ROUTE_KEYWORDS.items() if any(k in text for k in kws)]
        for g in gs:
            assigned[g].append(no)
        if gs:
            auto[no] = gs
    trace["assignments"] = assigned
    trace["auto_assigned"] = auto
    trace["definitions"] = defs
    # 分派里引用了解析结果中没有的"第X条"：原文里确实有这一条 → 标题格式没解析出来（要回退）；
    # 原文里没有 → 主审多写了编号（忽略并记录）。"缺失（保密条款）"这类提示不是条款号，不算
    refs = {normalize_clause(a.get("clause", "")) for a in terminal.input.get("assignments", [])}
    missing = sorted(no for no in refs - set(clauses) if no)
    unknown = [no for no in missing if no in contract or _arabic(no) in contract]
    trace["ignored_assignments"] = [no for no in missing if no not in unknown]
    if not clauses or unknown or not any(assigned.values()):
        # 识别不出条款标题（如"一、价款"）或分派落空：不能产出"没问题"的空审查，
        # 回退单 Agent 审全文（审查 C1）
        return _fallback_single(
            contract, tenant, llm, make_box(), on_step, contract_id, result, trace, t0, unknown
        )

    jobs = {}
    with ThreadPoolExecutor(max_workers=len(GROUPS)) as pool:
        for g in GROUPS:
            nos = sorted(set(assigned[g]) | set(defs), key=_clause_no) if assigned[g] else []
            parts = "\n".join(clauses[no] for no in nos)
            box = make_box()
            jobs[g] = (
                box,
                pool.submit(
                    _subagent,
                    g,
                    side,
                    header,
                    headings,
                    parts or "（主审没有分给你条款）",
                    contract,
                    tenant,
                    llm,
                    box,
                    on_step,
                ),
            )
    citations: dict = {}
    offsets = {"C": 0, "T": 0}
    findings, out_of_scope, summaries, invalid, failed = [], 0, [], [], []
    for g, (box, fut) in jobs.items():
        try:
            res, c, s, sub_trace = fut.result()
        except (
            Exception
        ) as e:  # 一组出错（超时、网络）：其余组照常汇总，标注这类风险没审（审查 I1）
            failed.append(g)
            trace.setdefault("group_errors", {})[g] = f"{type(e).__name__}: {e}"[:300]
            summaries.append(f"{GROUPS[g]['label']}：本次未能完成审查，请重试或转人工。")
            continue
        result.cost_usd += c
        result.steps += s
        trace["steps"] += [st | {"agent": g} for st in sub_trace["steps"]]
        allowed = set(GROUPS[g]["types"]) | ({"other"} if GROUPS[g]["sql"] else set())
        for f in _renumber(res, citations, offsets, box, g, invalid):
            if f["risk_type"] in allowed:
                findings.append(f)
            else:
                out_of_scope += 1
        if res.summary:
            summaries.append(f"{GROUPS[g]['label']}：{res.summary}")
    result.kind = "review"
    result.our_side = side
    result.findings = merge_findings(findings)
    result.summary = "\n".join(summaries)
    result.invalid_citations = invalid
    if len(failed) == len(GROUPS):
        result.kind = "fallback"
    trace["out_of_scope"] = out_of_scope
    trace["failed_groups"] = failed
    trace["end"] = "submit_review"
    return _close(result, trace, t0, citations)


def _fallback_single(contract, tenant, llm, box, on_step, contract_id, result, trace, t0, unknown):
    from contract_review.review_agent import review_contract

    single = review_contract(contract, tenant, llm, box, on_step=on_step, contract_id=contract_id)
    single.cost_usd += result.cost_usd
    single.steps += result.steps
    single.trace["steps"] = trace["steps"] + single.trace["steps"]
    single.trace |= {
        "task": "review_multi",
        "trace_id": trace["trace_id"],
        "fallback_single": True,
        "unknown_clauses": unknown,
        "assignments": trace.get("assignments", {}),
        "cost_usd": single.cost_usd,
        "latency_ms": (time.perf_counter() - t0) * 1000,
    }
    single.latency_ms = single.trace["latency_ms"]
    return single


def _close(result: ReviewResult, trace: dict, t0: float, citations: dict) -> ReviewResult:
    result.citations = citations
    result.latency_ms = (time.perf_counter() - t0) * 1000
    trace["result"] = {
        "kind": result.kind,
        "our_side": result.our_side,
        "summary": result.summary,
        "findings": result.findings,
        "clarification": result.clarification,
        "invalid_citations": result.invalid_citations,
    }
    trace["citations"] = citations
    trace["cost_usd"] = result.cost_usd
    trace["latency_ms"] = result.latency_ms
    result.trace = trace
    return result
