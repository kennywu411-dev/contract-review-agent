"""合同审查的多轮追问评测（自动判分），对比"带上一轮审查历史"（full）和"不带历史"（none）。

四类追问（第一轮复用一次 eval-review 全量运行的结果，不再付费；当事人空白的合同第一轮现跑）：
- clarify：当事人空白、第一轮反问后，用户答"我方是甲方/乙方"→ 应按该身份完成审查（看检出率）。
- ordinal："你列的第 k 条意见怎么改"→ 回答应落在上一轮第 k 条意见的条款上（必须靠历史）。
- basis："第 X 条的依据是哪条法律或规范"→ 条款对，且引用落在该风险的合法依据集合。
- flip："换成对方视角再看一遍"→ 应以对方身份重审；原先对我方不利的条款多半对对方有利，不应再报。
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from contract_review.contract_evalset import Contract, load_contracts, sample_contracts
from contract_review.eval_agent import _pct, _rate
from contract_review.eval_retrieval import RUNS_DIR
from contract_review.eval_review import _basis_ok, score_contract

N_ORDINAL, N_BASIS, N_FLIP = 8, 8, 6


def score_followup(exp: dict, r: dict, c: Contract) -> dict:
    t = exp["type"]
    if t in ("ordinal", "basis"):
        ok = r["kind"] == "followup" and r["answer_clause"] == exp["clause"]
        out = {"ok": ok}
        if t == "basis":
            f = {"citations": r.get("answer_citations", [])}
            out["basis_ok"] = ok and _basis_ok(
                f, {"type": "", "basis": exp["basis"]}, r["citations"]
            )
        return out
    if t == "flip":
        ok = r["kind"] == "review" and r["our_side"] == exp["side"]
        gold = {g["clause"] for g in c.gold if g["clause"]}
        if not ok:  # 没切换身份时谈不上"原风险还报不报"，不计入
            return {"ok": False, "old_gold_reported": None}
        reported = {f["clause"] for f in r["findings"]} & gold
        return {"ok": True, "old_gold_reported": len(reported) / len(gold) if gold else 0.0}
    # clarify：补充身份后应完成审查
    ok = r["kind"] == "review" and r["our_side"] == exp["side"]
    scored = score_contract(c, r) if ok else None
    recall = (
        sum(g["detected"] for g in scored["gold"]) / len(scored["gold"])
        if scored and scored["gold"]
        else 0.0
    )
    return {"ok": ok, "recall": recall}


def summarize_dialogs(rows: list[dict]) -> dict:
    out: dict = {}
    for mode in sorted({r["mode"] for r in rows}):
        out[mode] = {}
        for t in ("clarify", "ordinal", "basis", "flip"):
            rs = [r for r in rows if r["mode"] == mode and r["type"] == t]
            if not rs:
                continue
            item = {"n": len(rs), "ok_rate": _rate(rs, lambda r: r["ok"])}
            if t == "basis":
                item["basis_ok_rate"] = _rate(rs, lambda r: r.get("basis_ok", False))
            if t == "flip":
                xs = [r["old_gold_reported"] for r in rs if r["old_gold_reported"] is not None]
                item["old_gold_reported"] = sum(xs) / len(xs) if xs else None
            if t == "clarify":
                item["recall"] = sum(r["recall"] for r in rs) / len(rs)
            out[mode][t] = item
    lat = [r["latency_ms"] for r in rows]
    out["cost_usd"] = sum(r["cost_usd"] for r in rows)
    out["latency_ms"] = {"p50": _pct(lat, 0.5), "p95": _pct(lat, 0.95)}
    return out


def build_dialogs(source: Path) -> list[dict]:
    """从全量运行里挑留出集合同造追问（确定性）。

    返回 [{contract_id, type, question, expect, prev}]。
    """
    cs = {c.id: c for c in load_contracts()}
    dev = {c.id for c in sample_contracts(list(cs.values()), 20)}
    rows = [json.loads(x) for x in (source / "review_results.jsonl").open(encoding="utf-8")]
    dialogs: list[dict] = []
    for c in cs.values():
        if c.clarify:
            q = f"我方是{c.our_side}方（{c.our_name}）。"
            dialogs.append(
                {
                    "contract_id": c.id,
                    "type": "clarify",
                    "question": q,
                    "expect": {"type": "clarify", "side": c.our_side},
                    "prev": None,
                }
            )
    ordinal = basis = flip = 0
    for res in sorted(rows, key=lambda r: r["id"]):
        c = cs[res["id"]]
        if c.clarify or not c.gold or c.id in dev or res["kind"] != "review":
            continue
        trace = json.loads((source / "traces" / f"{res['trace_id']}.json").read_text("utf-8"))
        prev = trace["result"]
        gold_clauses = {g["clause"] for g in c.gold if g["clause"]}
        hits = [i for i, f in enumerate(prev["findings"]) if f["clause"] in gold_clauses]
        if ordinal < N_ORDINAL and hits:
            k = hits[-1] + 1  # 尽量问后面的编号，第 1 条太容易猜
            dialogs.append(
                {
                    "contract_id": c.id,
                    "type": "ordinal",
                    "question": f"你列的第{k}条意见，具体怎么改？请给出可以直接替换的条款。",
                    "expect": {"type": "ordinal", "clause": prev["findings"][k - 1]["clause"]},
                    "prev": prev,
                }
            )
            ordinal += 1
        g = next((g for g in c.gold if g["clause"] and g["type"] != "unit_price"), None)
        if basis < N_BASIS and g:
            dialogs.append(
                {
                    "contract_id": c.id,
                    "type": "basis",
                    "question": f"{g['clause']}的问题，依据是哪条法律或公司规范？",
                    "expect": {"type": "basis", "clause": g["clause"], "basis": g["basis"]},
                    "prev": prev,
                }
            )
            basis += 1
        if flip < N_FLIP and len(gold_clauses) >= 2:
            other = "乙" if c.our_side == "甲" else "甲"
            dialogs.append(
                {
                    "contract_id": c.id,
                    "type": "flip",
                    "question": f"换成{other}方的视角，再把这份合同看一遍。",
                    "expect": {"type": "flip", "side": other},
                    "prev": prev,
                }
            )
            flip += 1
    return dialogs


def run_review_dialog_eval(
    source: Path, review_fn, modes: list[str], tag: str, max_cost: float = 0.6
) -> Path:
    """review_fn(contract, history, question) -> ReviewResult。"""
    from contract_review.review_agent import history_from_review
    from contract_review.trace import save_trace

    cs = {c.id: c for c in load_contracts()}
    out = RUNS_DIR / f"{tag}-{datetime.now():%Y%m%d-%H%M%S}"
    (out / "traces").mkdir(parents=True)
    rows, spent = [], 0.0
    dialogs = build_dialogs(source)
    with (out / "dialog_results.jsonl").open("w", encoding="utf-8") as fh:
        for d in dialogs:
            c = cs[d["contract_id"]]
            prev, first_cost = d["prev"], 0.0
            if d["type"] == "clarify":  # 第一轮现跑：应当反问
                first = review_fn(c, "", "")
                save_trace(first, out / "traces")
                prev, first_cost = first.to_dict(), first.cost_usd
                spent += first_cost
            for mode in modes:
                history = history_from_review(prev) if mode == "full" else ""
                r = review_fn(c, history, d["question"])
                save_trace(r, out / "traces")
                row = {
                    "contract_id": c.id,
                    "type": d["type"],
                    "mode": mode,
                    "question": d["question"],
                    "first_kind": prev["kind"] if prev else None,
                    "kind": r.kind,
                    "trace_id": r.trace["trace_id"],
                    "cost_usd": r.cost_usd + (first_cost if mode == modes[0] else 0.0),
                    "latency_ms": r.latency_ms,
                } | score_followup(d["expect"], r.to_dict(), c)
                rows.append(row)
                fh.write(json.dumps(row, ensure_ascii=False) + "\n")
                fh.flush()
                spent += r.cost_usd
            if spent > max_cost:
                break
    summary = summarize_dialogs(rows) | {
        "run_id": out.name,
        "kind": "review_dialog",
        "source_run": source.name,
        "dialogs": len({(r["contract_id"], r["type"]) for r in rows}),
        "stopped_by_budget": spent > max_cost,
    }
    (out / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=1), "utf-8")
    return out
