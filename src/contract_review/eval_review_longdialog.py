"""长对话评测（ADR 011）：一份合同审查后连续追问 8 轮，后面的轮次回指最早的审查和用户中途的决定。

对比三种记忆：full（全部原文）、window（只留最近 2 轮）、compress（超 3000 字时把较早轮次压成
结构化摘要）。第 0 轮复用一次 eval-review 全量运行的审查结果（不再付费），每种方式各走自己的轨迹。

追问脚本（A、B 是首次审查的第 1、2 条意见，X 是"通知与送达"条款）：
1. 你列的第 1 条怎么改 → 条款 A（近期指代）
2. B 条款的依据 → 条款 B
3. X 有没有问题 → 条款 X
4. 第 2 条已谈妥不用管（不计分，后面要用）
5. 换对方视角重审 → 我方 = 对方
6. 换回原立场 → 我方 = 原立场
7. 最开始列的第 1 条是哪一条 → 条款 A（回指第 0 轮）
8. 最开始的意见里除谈妥那条外还要改哪些 → 列出其余条款、不含 B（回指第 0 轮 + 第 4 轮）
"""

from __future__ import annotations

import json
import re
import statistics as st
from datetime import datetime
from pathlib import Path

from contract_review.contract_evalset import Contract, load_contracts, sample_contracts
from contract_review.eval_retrieval import RUNS_DIR
from contract_review.review_agent import normalize_clause
from contract_review.review_memory import ReviewMemory

KEEP_LAST = 2
BUDGET_CHARS = 3000  # 小样里 8 轮全量历史约 5600 字，6000 不会触发压缩（问题日志 P23）
# 前面是"法""典"等的是法条引用，前面是"轮"的是意见编号（第0轮第1条），后面跟"意见"的也是意见编号
_CLAUSE = re.compile(
    r"(?<![法典释则范办例轮])第\s*(?:[0-9]+|[零一二三四五六七八九十百]+)\s*条(?!\s*意见)"
)


_NUM = r"(?:[0-9]+|[零一二三四五六七八九十百]+)"
_ART = rf"第\s*{_NUM}\s*条(?:\s*第\s*{_NUM}\s*款)?"
# 先整体剔除、再抽合同条款号的片段（审查 I5）：
# 书名号法条引用（"《民法典》第七条"）；意见编号（"列的第1条""第0轮第1条""第2条意见"）
_NOT_CLAUSE = re.compile(
    rf"《[^》]{{1,40}}》[^，。；,;\n《]{{0,8}}?{_ART}(?:\s*[、和及与或]\s*{_ART})*"
    rf"|(?:列的|列出的|轮)\s*第\s*{_NUM}\s*条"
    rf"|第\s*{_NUM}\s*条\s*意见"
)
# 排除语境（已谈妥/不再列入的条款不算"列入"）。中间不允许出现"条"，不能跨过别的条款；
# 必须以明确的排除词收尾或开头
_DROP = (
    r"(?:已经?谈妥|不再处理|不再列入|不列入|移出|排除|无需处理|无需修改|不用再管|不用管|不需要再改)"
)
_EXCLUDED = re.compile(
    rf"除了?[^，。；,;\n条]{{0,12}}{_ART}[^，。；,;\n条]{{0,12}}?(?:以外|之外|外)"
    rf"|{_DROP}的?[^，。；,;\n条]{{0,6}}{_ART}"
    rf"|{_ART}[^，。；,;\n条]{{0,16}}?{_DROP}"
)


def clauses_in(text: str) -> set[str]:
    """回答里提到的合同条款号：先剔除法条引用和意见编号，再抽"第X条"。"""
    out = set()
    for m in _CLAUSE.finditer(_NOT_CLAUSE.sub("", text or "")):
        no = normalize_clause(m.group(0))
        if no and len(no) <= 6:  # 合同条款号最多到"第二十X条"，法条号更长
            out.add(no)
    return out


def _notice_clause(c: Contract) -> str:
    for line in c.text.splitlines():
        if line.endswith(" 通知与送达"):
            return line.split(" ")[0]
    raise ValueError(f"{c.id} 没有通知与送达条款")


def usable_prev(prev: dict) -> bool:
    """首次审查至少 3 条带条款号的意见，且第 1、2 条都有条款号。

    题目按全部意见编号出（与记忆渲染一致），前两条是缺失型意见时编号会错位，所以不入选。
    """
    fs = prev["findings"]
    with_clause = sum(1 for f in fs if f.get("clause"))
    return with_clause >= 3 and bool(fs[0].get("clause")) and bool(fs[1].get("clause"))


def script_turns(prev: dict, c: Contract) -> list[dict]:
    a, b = prev["findings"][0]["clause"], prev["findings"][1]["clause"]
    # 谈妥的是第 2 条意见；它所在条款如果还有别的意见，这个条款仍要列出（P23）
    others = list(
        dict.fromkeys(
            f["clause"] for i, f in enumerate(prev["findings"]) if i != 1 and f.get("clause")
        )
    )
    side = prev["our_side"]
    other = "乙" if side == "甲" else "甲"
    x = _notice_clause(c)
    return [
        {
            "q": "你列的第1条意见，具体怎么改？请给出可以直接替换的条款。",
            "expect": {"type": "clause", "clause": a, "group": "recent"},
        },
        {
            "q": f"{b}的问题，依据是哪条法律或公司规范？",
            "expect": {"type": "clause", "clause": b, "group": "direct"},
        },
        {
            "q": f"{x}（通知与送达）有没有对我方不利的地方？",
            "expect": {"type": "clause", "clause": x, "group": "direct"},
        },
        {
            "q": "补充一个情况：你列的第2条意见，我们已经和对方谈妥，"
            "对方同意按我们的版本改，这条不用再管了。",
            "expect": {"type": "none", "group": "none"},
        },
        {
            "q": f"换成{other}方的视角，再把这份合同看一遍。",
            "expect": {"type": "side", "side": other, "group": "flip"},
        },
        {
            "q": f"好，换回我方原来的立场，按{side}方重新看一遍。",
            "expect": {"type": "side", "side": side, "group": "flip"},
        },
        {
            "q": "最开始（第一次审查时）你列的第1条意见，对应合同的哪一条？",
            "expect": {"type": "clause", "clause": a, "group": "early"},
        },
        {
            "q": "第一次审查列出的意见里，除了我们已经谈妥的那条，"
            "还有哪些需要我们去改？只列条款号。",
            "expect": {
                "type": "list",
                "must": others,
                "must_not": [] if b in others else [b],
                "group": "early",
            },
        },
    ]


def score_turn(exp: dict, r: dict) -> bool | None:
    t = exp["type"]
    if t == "none":
        return None
    if t == "clause":
        return r["kind"] == "followup" and r.get("answer_clause") == exp["clause"]
    if t == "side":
        return r["kind"] == "review" and r.get("our_side") == exp["side"]
    answer = r.get("answer", "")
    mentioned = clauses_in(answer)
    # "除已谈妥的第六条外"这类排除语境里的不算列入；先剔除意见编号，免得"第0轮第2条"被当成排除对象
    listed = clauses_in(_EXCLUDED.sub("", _NOT_CLAUSE.sub("", answer)))
    return set(exp["must"]) <= mentioned and not (set(exp["must_not"]) & listed)


def build_long_dialogs(source: Path, n: int) -> list[dict]:
    """留出集里首次审查至少有 3 条带条款号意见的合同，按 id 取前 n 份（确定性）。"""
    cs = {c.id: c for c in load_contracts()}
    dev = {c.id for c in sample_contracts(list(cs.values()), 20)}
    rows = [json.loads(x) for x in (source / "review_results.jsonl").open(encoding="utf-8")]
    out = []
    for res in sorted(rows, key=lambda r: r["id"]):
        c = cs[res["id"]]
        if c.clarify or not c.gold or c.id in dev or res["kind"] != "review":
            continue
        trace = json.loads((source / "traces" / f"{res['trace_id']}.json").read_text("utf-8"))
        prev = trace["result"]
        if not usable_prev(prev):
            continue
        out.append({"contract_id": c.id, "prev": prev, "turns": script_turns(prev, c)})
        if len(out) == n:
            break
    return out


def _input_tokens(trace: dict) -> int:
    keys = ("input_tokens", "cache_read_input_tokens", "cache_creation_input_tokens")
    return sum(s["usage"].get(k, 0) for s in trace.get("steps", []) for k in keys)


def summarize_long(rows: list[dict], memory_stats: dict) -> dict:
    out: dict = {}
    for mode in sorted({r["mode"] for r in rows}):
        rs = [r for r in rows if r["mode"] == mode]
        scored = [r for r in rs if r["ok"] is not None]
        item = {
            "turns": len(rs),
            "scored_turns": len(scored),
            "ok": sum(r["ok"] for r in scored) / len(scored) if scored else 0.0,
            "input_tokens_mean": st.mean(r["input_tokens"] for r in rs) if rs else 0,
            "history_chars_mean": st.mean(r["history_chars"] for r in rs) if rs else 0,
            "agent_cost_usd": sum(r["cost_usd"] for r in rs),
        } | memory_stats.get(mode, {})
        for g in ("recent", "direct", "flip", "early"):
            gs = [r for r in scored if r["group"] == g]
            item[f"{g}_ok"] = sum(r["ok"] for r in gs) / len(gs) if gs else None
        item["total_cost_usd"] = item["agent_cost_usd"] + item.get("cost_usd", 0.0)
        out[mode] = item
    return out


def run_long_dialog_eval(
    source: Path, review_fn, summarizer, modes: list[str], n: int, tag: str, max_cost: float
) -> Path:
    """review_fn(contract, history, question) -> ReviewResult。

    summarizer 只给 compress 方式压缩用。
    """
    from contract_review.trace import save_trace

    cs = {c.id: c for c in load_contracts()}
    out = RUNS_DIR / f"{tag}-{datetime.now():%Y%m%d-%H%M%S}"
    (out / "traces").mkdir(parents=True)
    rows: list[dict] = []
    mem_stats: dict = {
        m: {"cost_usd": 0.0, "compressions": 0, "backfilled": 0, "compress_errors": 0}
        for m in modes
    }
    spent, stopped = 0.0, False
    dialogs = build_long_dialogs(source, n)
    with (out / "longdialog_results.jsonl").open("w", encoding="utf-8") as fh:
        for d in dialogs:
            c = cs[d["contract_id"]]
            for mode in modes:
                mem = ReviewMemory(
                    mode=mode, keep_last=KEEP_LAST, budget_chars=BUDGET_CHARS, summarizer=summarizer
                )
                mem.add_review(d["prev"])
                for i, turn in enumerate(d["turns"], 1):
                    history = mem.render()
                    r = review_fn(c, history, turn["q"])
                    save_trace(r, out / "traces")
                    rd = r.to_dict()
                    mem.add_followup(turn["q"], rd)
                    row = {
                        "contract_id": c.id,
                        "mode": mode,
                        "turn": i,
                        "group": turn["expect"]["group"],
                        "question": turn["q"],
                        "kind": r.kind,
                        "ok": score_turn(turn["expect"], rd),
                        "history_chars": len(history),
                        "input_tokens": _input_tokens(r.trace),
                        "cost_usd": r.cost_usd,
                        "latency_ms": r.latency_ms,
                        "trace_id": r.trace.get("trace_id"),
                    }
                    rows.append(row)
                    fh.write(json.dumps(row, ensure_ascii=False) + "\n")
                    fh.flush()
                    spent += r.cost_usd
                s = mem_stats[mode]
                s["cost_usd"] += mem.cost_usd
                s["compressions"] += mem.compressions
                s["backfilled"] += mem.backfilled
                s["compress_errors"] += mem.compress_errors
                spent += mem.cost_usd
            if spent > max_cost:
                stopped = True
                break
    summary = summarize_long(rows, mem_stats) | {
        "run_id": out.name,
        "kind": "review_longdialog",
        "source_run": source.name,
        "dialogs": len({r["contract_id"] for r in rows}),
        "keep_last": KEEP_LAST,
        "budget_chars": BUDGET_CHARS,
        "stopped_by_budget": stopped,
    }
    (out / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=1), "utf-8")
    return out
