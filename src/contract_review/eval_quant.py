"""金额测算评测（ADR 014）：v3 采购合同上的字段抽取准确率、金额正确率、"无法量化"判断，
以及对照实验，同一份合同，让模型直接给金额（direct）对比 模型抄数 + 规则计算（extract）。

判定方式在跑之前定好（ADR 014"评测设计"一节），写死在这个文件里：
- 金额算对：标准答案有金额时，系统给出金额且 |系统 − 标准| ≤ 标准 × 1%（TOLERANCE）；
- 另报"分毫不差"：|系统 − 标准| ≤ 0.01 元；
- 状态判对：标准是 none（没有这项金额）时系统也没给金额；标准是 unquantifiable 时系统标了无法量化；
- "需人工核对"（needs_check）不算给出金额，但在三分类里一律算错，单独计数；
- 样本：v3 全部 60 份 × 5 个测算项 = 300 项，不抽样；每个方案跑一次。
"""

from __future__ import annotations

import json
import statistics
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from math import comb
from pathlib import Path

from contract_review.config import ROOT
from contract_review.quant_extract import (
    TOPICS,
    direct_amounts,
    ledger_table,
    run_quant,
    topic_outcomes,
)

V3_PATH = ROOT / "evals" / "contracts_v3.jsonl"
EVALSETS = {"v3": "contracts_v3.jsonl", "v3-holdout": "contracts_v3_holdout.jsonl"}
TOLERANCE = 0.01  # 金额相对误差 ≤ 1% 算对
EXACT = 0.011  # "分毫不差"：差不超过 1 分钱（留一点浮点余量）
ARMS = ("extract", "direct-low", "direct-high")


def load_v3(path: Path = V3_PATH) -> list[dict]:
    return [json.loads(line) for line in path.open(encoding="utf-8") if line.strip()]


def judge(gold: dict, pred: tuple[str, float]) -> dict:
    """一项测算的判定。pred = (status, amount)，status ∈ amount / none / unquantifiable / needs_check。"""
    status, amount = pred
    gave = status == "amount" and amount > 0
    if gold["status"] == "amount":
        err = abs(amount - gold["amount"]) if gave else None
        ok = gave and err <= TOLERANCE * gold["amount"]
        return {"ok": bool(ok), "exact": bool(gave and err <= EXACT), "gave_amount": gave}
    if gold["status"] == "none":
        ok = status == "none" or (status == "amount" and amount == 0)
    else:
        ok = status == "unquantifiable"
    return {"ok": bool(ok), "exact": bool(ok), "gave_amount": gave}


def score_fields(c: dict, q: dict) -> dict:
    """字段抽取：标准答案里的每个数字，是否被抄出、通过原文核对且数值相同。"""
    got: dict[str, list] = {}
    for f in q.get("figures", []):
        got.setdefault(f["field"], []).append(f)
    correct, wrong, missing = 0, [], []
    for name, value in c["gold_fields"].items():
        hits = [f for f in got.get(name, []) if f["ok"]]
        if any(abs(float(f["value"]) - value) <= 1e-6 * max(1, abs(value)) for f in hits):
            correct += 1
        elif name in got:
            wrong.append(name)
        else:
            missing.append(name)

    # 标的明细：每行的单价和数量各算一个数字；按品名对应
    # 模型抄品名时可能连规格一起抄（"液压油；规格：L-HM46 200L"）：标准品名是它的子串就算同一行
    def find(item, only_ok):
        return next(
            (
                ln
                for ln in q.get("lines", [])
                if (ln["ok"] or not only_ok)
                and ln["item"]
                and (item in ln["item"] or ln["item"] in item)
            ),
            None,
        )

    matched = set()
    for g in c["gold_lines"]:
        ln, anyln = find(g["item"], True), find(g["item"], False)
        if anyln is not None:
            matched.add(anyln["item"])
        for k in ("unit_price", "quantity"):
            if ln is not None and abs(float(ln[k]) - g[k]) <= 1e-6 * max(1, abs(g[k])):
                correct += 1
            elif anyln is not None:
                wrong.append(f"{g['item']}.{k}")
            else:
                missing.append(f"{g['item']}.{k}")
    seen = {ln["item"] for ln in q.get("lines", [])} - matched | {
        g["item"] for g in c["gold_lines"]
    }
    gold_n = len(c["gold_fields"]) + 2 * len(c["gold_lines"])
    extra = sorted({f["field"] for f in q.get("figures", []) if f["ok"]} - set(c["gold_fields"]))
    extra += [f"line:{i}" for i in sorted(seen - {g["item"] for g in c["gold_lines"]})]
    rejected = [f["field"] for f in q.get("figures", []) if not f["ok"] and not f.get("soft")]
    rejected += [f"line:{ln['item']}" for ln in q.get("lines", []) if not ln["ok"]]
    flags = {u["topic"] for u in q.get("uncomputable", [])}
    gold_flags = set(c["gold_uncomputable"])
    return {
        "gold": gold_n,
        "correct": correct,
        "lines": len(c["gold_lines"]),
        "wrong": wrong,
        "missing": missing,
        "extra": extra,
        "rejected": rejected,
        "flags_gold": len(gold_flags),
        "flags_hit": len(flags & gold_flags),
        "flags_extra": sorted(flags - gold_flags),
    }


def run_one(c: dict, arm: str, llm, sql, tables: dict) -> dict:
    t0 = time.perf_counter()
    row = {"id": c["id"], "arm": arm, "tenant": c["tenant"], "error": ""}
    try:
        if arm == "extract":
            q = run_quant(c["text"], c["tenant"], c["our_side"], llm, sql)
            step = q.pop("step", None) or {}
            pred = topic_outcomes(q)
            row |= {
                "cost_usd": q["cost_usd"],
                "llm_ms": step.get("llm_ms", 0.0),
                "usage": step.get("usage", {}),
                "fields": score_fields(c, q),
                "quant": q,
                "error": q["error"],
            }
        else:
            effort = arm.split("-")[1]
            pred, rec, cost = direct_amounts(
                c["text"], c["tenant"], c["our_side"], llm, tables[c["tenant"]], effort
            )
            pred = {t: pred.get(t, ("none", 0.0)) for t in TOPICS}
            work = (rec["tool_calls"][0]["input"].get("work", "") if rec["tool_calls"] else "")[
                :4000
            ]
            row |= {"cost_usd": cost, "llm_ms": rec["llm_ms"], "usage": rec["usage"], "work": work}
    except Exception as e:  # 单份出错不影响其余
        pred = {t: ("needs_check", 0.0) for t in TOPICS}
        row |= {
            "cost_usd": 0.0,
            "llm_ms": 0.0,
            "usage": {},
            "error": f"{type(e).__name__}: {e}"[:300],
        }
    row["wall_ms"] = (time.perf_counter() - t0) * 1000
    row["pred"] = {t: list(pred[t]) for t in TOPICS}
    row["judge"] = {t: judge(c["gold_amounts"][t], pred[t]) for t in TOPICS}
    return row


def summarize(rows: list[dict], contracts: dict[str, dict]) -> dict:
    out: dict = {"contracts": len(rows)}
    n = {"amount": 0, "none": 0, "unquantifiable": 0}
    ok = dict(n)
    exact = gave_on_unq = gave_on_none = needs_check = 0
    by_topic = {t: {"n": 0, "ok": 0} for t in TOPICS}
    all_ok = 0
    for r in rows:
        gold = contracts[r["id"]]["gold_amounts"]
        all_ok += all(r["judge"][t]["ok"] for t in TOPICS)
        for t in TOPICS:
            g, j = gold[t]["status"], r["judge"][t]
            n[g] += 1
            ok[g] += j["ok"]
            by_topic[t]["n"] += 1
            by_topic[t]["ok"] += j["ok"]
            needs_check += r["pred"][t][0] == "needs_check"
            if g == "amount":
                exact += j["exact"]
            elif g == "unquantifiable":
                gave_on_unq += j["gave_amount"]
            else:
                gave_on_none += j["gave_amount"]
    total = sum(n.values())
    out |= {
        "items": total,
        "amount_items": n["amount"],
        "amount_correct": ok["amount"],
        "amount_accuracy": ok["amount"] / n["amount"] if n["amount"] else None,
        "amount_exact": exact,
        "amount_exact_rate": exact / n["amount"] if n["amount"] else None,
        "none_items": n["none"],
        "none_correct": ok["none"],
        "amount_given_on_none": gave_on_none,
        "unquantifiable_items": n["unquantifiable"],
        "unquantifiable_correct": ok["unquantifiable"],
        "unquantifiable_accuracy": ok["unquantifiable"] / n["unquantifiable"]
        if n["unquantifiable"]
        else None,
        "amount_given_on_unquantifiable": gave_on_unq,
        "needs_check_items": needs_check,
        "status_correct": sum(ok.values()),
        "status_accuracy": sum(ok.values()) / total if total else None,
        "contracts_all_correct": all_ok,
        "by_topic": by_topic,
        "cost_usd": sum(r["cost_usd"] for r in rows),
        "cost_p50": statistics.median(r["cost_usd"] for r in rows) if rows else 0,
        "llm_ms_p50": statistics.median(r["llm_ms"] for r in rows) if rows else 0,
        "llm_ms_p95": sorted(r["llm_ms"] for r in rows)[int(0.95 * (len(rows) - 1))] if rows else 0,
        "errors": sum(1 for r in rows if r["error"]),
    }
    if rows and "fields" in rows[0]:
        f = [r["fields"] for r in rows]
        gold_n = sum(x["gold"] for x in f)
        out["fields"] = {
            "gold": gold_n,
            "correct": sum(x["correct"] for x in f),
            "accuracy": sum(x["correct"] for x in f) / gold_n if gold_n else None,
            "wrong": sum(len(x["wrong"]) for x in f),
            "missing": sum(len(x["missing"]) for x in f),
            "extra": sum(len(x["extra"]) for x in f),
            "rejected_by_verification": sum(len(x["rejected"]) for x in f),
            "flags_gold": sum(x["flags_gold"] for x in f),
            "flags_hit": sum(x["flags_hit"] for x in f),
            "flags_extra": sum(len(x["flags_extra"]) for x in f),
        }
        out["ledger_backends"] = sorted({r["quant"].get("ledger_backend") or "" for r in rows})
    return out


def mcnemar(a: list[bool], b: list[bool]) -> dict:
    """配对比较两种方案在同一批测算项上的对错：只看不一致的项，精确二项检验（双侧）。"""
    only_a = sum(1 for x, y in zip(a, b, strict=True) if x and not y)
    only_b = sum(1 for x, y in zip(a, b, strict=True) if y and not x)
    n = only_a + only_b
    p = (
        1.0
        if n == 0
        else min(1.0, 2 * sum(comb(n, k) for k in range(min(only_a, only_b) + 1)) / 2**n)
    )
    return {"only_first": only_a, "only_second": only_b, "p_value": p}


def run_eval(
    llm,
    sql,
    arms: list[str],
    tag: str = "quant",
    sample: int = 0,
    max_cost: float = 1.5,
    workers: int = 4,
    out_root: Path | None = None,
    evalset: Path = V3_PATH,
) -> Path:
    cs = load_v3(evalset)
    if sample:
        cs = cs[:: max(1, len(cs) // sample)][:sample]
    contracts = {c["id"]: c for c in cs}
    out_dir = (out_root or ROOT / "runs") / f"{tag}-{datetime.now():%Y%m%d-%H%M%S}"
    out_dir.mkdir(parents=True)
    lock = threading.Lock()

    class Locked:
        name = getattr(sql, "name", None)

        def run(self, q):
            with lock:
                return sql.run(q)

    tables = {t: ledger_table(Locked(), t) for t in sorted({c["tenant"] for c in cs})}
    (out_dir / "config.json").write_text(
        json.dumps(
            {
                "evalset": str(evalset.relative_to(ROOT)),
                "contracts": len(cs),
                "arms": arms,
                "model": llm.model,
                "tolerance": TOLERANCE,
                "max_cost": max_cost,
                "ledger_backend": getattr(sql, "name", None),
            },
            ensure_ascii=False,
            indent=1,
        ),
        "utf-8",
    )
    spent = [0.0]
    rows: dict[str, list[dict]] = {a: [] for a in arms}

    def job(c, arm):
        with lock:
            if spent[0] >= max_cost:
                return None
        row = run_one(c, arm, llm, Locked(), tables)
        with lock:
            spent[0] += row["cost_usd"]
        return row

    with (out_dir / "results.jsonl").open("w", encoding="utf-8") as fh:
        for arm in arms:
            with ThreadPoolExecutor(max_workers=workers) as pool:
                for row in pool.map(lambda c, arm=arm: job(c, arm), cs):
                    if row is None:
                        continue
                    rows[arm].append(row)
                    fh.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")
                    fh.flush()
            print(f"{arm}: {len(rows[arm])} 份，累计 ${spent[0]:.4f}")
    summary = {
        "kind": "quant",
        "run": out_dir.name,
        "model": llm.model,
        "tolerance": TOLERANCE,
        "total_cost_usd": spent[0],
        "arms": {a: summarize(rows[a], contracts) for a in arms if rows[a]},
        "paired": {},
    }
    if "extract" in rows and rows.get("extract"):
        base = {r["id"]: r for r in rows["extract"]}
        for arm in arms:
            if arm == "extract" or not rows[arm]:
                continue
            a, b = [], []
            for r in rows[arm]:
                for t in TOPICS:
                    if (
                        r["id"] in base
                        and contracts[r["id"]]["gold_amounts"][t]["status"] == "amount"
                    ):
                        a.append(base[r["id"]]["judge"][t]["ok"])
                        b.append(r["judge"][t]["ok"])
            summary["paired"][f"extract_vs_{arm}"] = mcnemar(a, b) | {"items": len(a)}
    (out_dir / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=1), "utf-8"
    )
    return out_dir


def print_summary(out_dir: Path, name: str = "summary.json") -> None:
    s = json.loads((out_dir / name).read_text("utf-8"))
    print(f"\n运行 {s['run']}，模型 {s['model']}，合计 ${s['total_cost_usd']:.4f}")
    for arm, a in s["arms"].items():
        print(
            f"[{arm}] 金额正确 {a['amount_correct']}/{a['amount_items']}（{a['amount_accuracy']:.1%}），"
            f"分毫不差 {a['amount_exact']}，无法量化判对 {a['unquantifiable_correct']}/"
            f"{a['unquantifiable_items']}（硬算 {a['amount_given_on_unquantifiable']}），"
            f"不该有金额却给了 {a['amount_given_on_none']}，三分类 {a['status_accuracy']:.1%}，"
            f"整份全对 {a['contracts_all_correct']}/{a['contracts']}，"
            f"单份花费 P50 ${a['cost_p50']:.5f}，模型耗时 P50 {a['llm_ms_p50'] / 1000:.1f}s"
        )
        if "fields" in a:
            f = a["fields"]
            print(
                f"    字段 {f['correct']}/{f['gold']}（{f['accuracy']:.1%}），抄错 {f['wrong']}，漏抄 {f['missing']}，"
                f"多抄 {f['extra']}，被原文核对拦下 {f['rejected_by_verification']}；"
                f"算不了的标记 {f['flags_hit']}/{f['flags_gold']}，多标 {f['flags_extra']}"
            )
    for k, v in s["paired"].items():
        print(f"配对 {k}：{v}")


KEY_OF = {
    "unit_price": "price_overpay",
    "payment": "payment_capital",
    "penalty": "penalty_gap",
    "deposit": "deposit_excess",
    "retention": "retention",
}


def run_e2e(
    llm,
    retriever,
    sql,
    tag: str = "quant-e2e",
    sample: int = 20,
    max_cost: float = 1.0,
    workers: int = 2,
) -> Path:
    """完整的多 Agent 审查 + 金额测算（产品路径）跑 v3 的一个小样：金额算对了没有、
    有多少金额挂在了某条审查意见上（其余的在汇总区单独列出）。v3 没有审查意见的标准答案，
    这里不评检出率和误报。"""
    from contract_review.multi_agent import review_contract_multi
    from contract_review.tools import ToolBox

    cs = load_v3()
    cs = cs[:: max(1, len(cs) // sample)][:sample]
    out_dir = ROOT / "runs" / f"{tag}-{datetime.now():%Y%m%d-%H%M%S}"
    (out_dir / "traces").mkdir(parents=True)
    lock = threading.Lock()
    spent = [0.0]

    def job(c):
        with lock:
            if spent[0] >= max_cost:
                return None
        t0 = time.perf_counter()
        r = review_contract_multi(
            c["text"],
            c["tenant"],
            llm,
            lambda: ToolBox(c["tenant"], retriever, sql),
            contract_id=c["id"],
        )
        with lock:
            spent[0] += r.cost_usd
        (out_dir / "traces" / f"{c['id']}.json").write_text(
            json.dumps(r.trace, ensure_ascii=False, default=str), "utf-8"
        )
        pred = topic_outcomes(r.quant) if r.quant else {t: ("needs_check", 0.0) for t in TOPICS}
        attached = {k for f in r.findings for k in f.get("quant", {}).get("keys", [])}
        return {
            "id": c["id"],
            "kind": r.kind,
            "our_side": r.our_side,
            "findings": len(r.findings),
            "findings_quantified": sum(
                1 for f in r.findings if f.get("quant", {}).get("status") == "quantified"
            ),
            "findings_unquantifiable": sum(
                1 for f in r.findings if f.get("quant", {}).get("status") == "unquantifiable"
            ),
            "judge": {t: judge(c["gold_amounts"][t], pred[t]) for t in TOPICS},
            "attached": {t: KEY_OF[t] in attached for t in TOPICS},
            "gold": {t: c["gold_amounts"][t]["status"] for t in TOPICS},
            "worst_total": (r.quant.get("summary") or {}).get("worst_total"),
            "cost_usd": r.cost_usd,
            "quant_cost_usd": r.quant.get("cost_usd", 0.0),
            "quant_ms": r.quant.get("latency_ms", 0.0),
            "wall_ms": (time.perf_counter() - t0) * 1000,
            "sql_backend": r.quant.get("ledger_backend"),
        }

    with ThreadPoolExecutor(max_workers=workers) as pool:
        rows = [r for r in pool.map(job, cs) if r is not None]
    (out_dir / "results.jsonl").write_text(
        "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), "utf-8"
    )
    gold_amount = [(r, t) for r in rows for t in TOPICS if r["gold"][t] == "amount"]
    summary = {
        "kind": "quant_e2e",
        "run": out_dir.name,
        "contracts": len(rows),
        "side_correct": sum(r["our_side"] == "甲" for r in rows),
        "amount_items": len(gold_amount),
        "amount_correct": sum(r["judge"][t]["ok"] for r, t in gold_amount),
        "amount_attached_to_finding": sum(r["attached"][t] for r, t in gold_amount),
        "findings": sum(r["findings"] for r in rows),
        "findings_quantified": sum(r["findings_quantified"] for r in rows),
        "findings_unquantifiable": sum(r["findings_unquantifiable"] for r in rows),
        "cost_usd": sum(r["cost_usd"] for r in rows),
        "quant_cost_usd": sum(r["quant_cost_usd"] for r in rows),
        "cost_p50": statistics.median(r["cost_usd"] for r in rows),
        "quant_cost_share": sum(r["quant_cost_usd"] for r in rows)
        / sum(r["cost_usd"] for r in rows),
        "wall_ms_p50": statistics.median(r["wall_ms"] for r in rows),
        "quant_ms_p50": statistics.median(r["quant_ms"] for r in rows),
        "sql_backends": sorted({r["sql_backend"] or "" for r in rows}),
    }
    (out_dir / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=1), "utf-8"
    )
    print(json.dumps(summary, ensure_ascii=False, indent=1))
    return out_dir


def rescore(run: str) -> Path:
    """用当前的核对和计算规则，对某次运行里 extract 方案保存下来的模型原始输出重新计算和判分。
    不调用模型和台账（台账均价取运行时记录的值）。原来的 summary.json 不动，另写 summary_rescored.json。"""
    from contract_review import quant
    from contract_review.multi_agent import split_contract

    out_dir = ROOT / "runs" / run
    contracts = {c["id"]: c for c in load_v3(_evalset_path(out_dir))}
    rows = []
    for line in (out_dir / "results.jsonl").open(encoding="utf-8"):
        r = json.loads(line)
        if r["arm"] != "extract" or r["id"] not in contracts:
            continue
        c, q = contracts[r["id"]], r["quant"]
        _, clauses = split_contract(c["text"])
        figures = quant.check_figures(q["figures"], c["text"], clauses)
        lines = quant.check_lines(q["lines"], c["text"], clauses)
        old = {ln["item"]: ln.get("ledger") for ln in q["lines"]}
        for ln in lines:
            ln["ledger"] = old.get(ln["item"]) if ln["ok"] else None
        raw_flags = [
            u for u in q["uncomputable"] + q.get("flags_dropped", []) if u.get("source") != "rule"
        ]
        flags, dropped = quant.check_flags(raw_flags, figures, c["text"], clauses)
        items = quant.compute(figures, c["our_side"], quant.load_params(c["tenant"]), lines, flags)
        new = q | {
            "figures": figures,
            "lines": lines,
            "uncomputable": flags,
            "flags_dropped": dropped,
            "items": quant.items_to_dicts(items),
            "summary": quant.summarize(items),
        }
        pred = topic_outcomes(new)
        rows.append(
            r
            | {
                "quant": new,
                "fields": score_fields(c, new),
                "pred": {t: list(pred[t]) for t in TOPICS},
                "judge": {t: judge(c["gold_amounts"][t], pred[t]) for t in TOPICS},
            }
        )
    summary = {
        "kind": "quant",
        "run": run,
        "rescored": True,
        "arms": {"extract": summarize(rows, contracts)},
    }
    (out_dir / "summary_rescored.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=1), "utf-8"
    )
    (out_dir / "results_rescored.jsonl").write_text(
        "".join(json.dumps(r, ensure_ascii=False, default=str) + "\n" for r in rows), "utf-8"
    )
    return out_dir


def _evalset_path(out_dir: Path) -> Path:
    cfg = json.loads((out_dir / "config.json").read_text("utf-8"))
    return ROOT / cfg.get("evalset", str(V3_PATH.relative_to(ROOT)))


def consistency(run_a: str, run_b: str) -> dict:
    """同一批合同跑两次，每个测算项两次的结论是否完全一样（状态相同，金额相差不超过 1 分钱）。"""

    def load(run):
        out = {}
        for line in (ROOT / "runs" / run / "results.jsonl").open(encoding="utf-8"):
            r = json.loads(line)
            out[(r["arm"], r["id"])] = r["pred"]
        return out

    a, b = load(run_a), load(run_b)
    res: dict = {}
    for key in sorted(set(a) & set(b)):
        arm = key[0]
        s = res.setdefault(arm, {"items": 0, "same": 0, "amount_items": 0, "amount_same": 0})
        for t in TOPICS:
            (sa, xa), (sb, xb) = a[key][t], b[key][t]
            same = sa == sb and abs(xa - xb) <= EXACT
            s["items"] += 1
            s["same"] += same
            if "amount" in (sa, sb):
                s["amount_items"] += 1
                s["amount_same"] += same
    return {"runs": [run_a, run_b], "arms": res}
