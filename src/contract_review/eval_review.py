"""合同审查评测：全部自动判分（修改建议的质量另由 Sonnet 抽样评，见 ADR 007）。

口径：
- 检出：金标准风险所在条款上有一条审查意见（缺失型：有一条 clause 为空、类型为保密的意见）。
- 类型判对：检出的意见里有一条的 risk_type 与金标准一致。
- 依据正确：检出的意见引用的原文里，至少一条落在该风险的合法依据集合（doc_id + 条号）；
  单价风险引用了有数据的查表结果也算。
- 误报：既不对应金标准风险、也不是临界条款的意见，按条款去重计数；
  落在"对我方有利"条款上的单独记为视角错误。
- 反问：4 份当事人空白的合同应当反问；其余合同反问算多余。反问合同不计入检出。
"""

from __future__ import annotations

import json
import statistics as st
from datetime import datetime
from pathlib import Path

from contract_review.contract_evalset import RISK_TYPES, Contract
from contract_review.eval_agent import _pct, _rate
from contract_review.eval_retrieval import RUNS_DIR


def _basis_ok(f: dict, gold: dict, citations: dict) -> bool:
    allowed = {(d, a) for d, a in gold["basis"]}
    for cid in f["citations"]:
        c = citations.get(cid)
        if not c:
            continue
        if c["kind"] == "table":
            if gold["type"] == "unit_price" and c.get("rows"):
                return True
        elif any((c["doc_id"], a) in allowed for a in c.get("articles", [])):
            return True
    return False


def _matches(f: dict, gold: dict) -> bool:
    if gold["clause"] is None:
        return f["clause"] is None and f["risk_type"] == gold["type"]
    # v2 组合风险：落在任何一个相关条款上都算检出
    return f["clause"] in (gold.get("clauses") or [gold["clause"]])


def _type_ok(f: dict, gold: dict) -> bool:
    return f["risk_type"] == gold["type"] or f["risk_type"] in gold.get("alt_types", [])


def score_contract(c: Contract, r: dict) -> dict:
    findings = r["findings"] if r["kind"] == "review" else []
    used: set[int] = set()
    gold_rows = []
    for g in c.gold:
        hits = [i for i, f in enumerate(findings) if _matches(f, g)]
        used.update(hits)
        fs = [findings[i] for i in hits]
        gold_rows.append(
            {
                "clause": g["clause"],
                "type": g["type"],
                "level": g.get("level"),
                "category": g.get("category"),
                "detected": bool(fs),
                "type_ok": any(_type_ok(f, g) for f in fs),
                "basis_ok": any(_basis_ok(f, g, r["citations"]) for f in fs),
                "level_ok": any(f["level"] == g.get("level") for f in fs),
                "human": any(f["needs_human_review"] for f in fs),
            }
        )
    # 严格命中：条款和类型都对，每处风险最多算一条意见
    strict_hits = sum(1 for g in c.gold if any(_matches(f, g) and _type_ok(f, g) for f in findings))
    border = []
    for b in c.borderline:
        hits = [i for i, f in enumerate(findings) if f["clause"] == b["clause"]]
        used.update(hits)
        border.append(
            {
                "clause": b["clause"],
                "type": b["type"],
                "flagged": bool(hits),
                "human": any(findings[i]["needs_human_review"] for i in hits),
            }
        )
    acceptable = 0
    for i, f in enumerate(findings):
        if i not in used and any(
            f["clause"] in a["clauses"] and f["risk_type"] in a["types"] for a in c.acceptable
        ):
            used.add(i)
            acceptable += 1
    favorable = {f["clause"] for f in c.favorable}
    rest = [f for i, f in enumerate(findings) if i not in used]
    fav_flagged = {f["clause"] for f in rest if f["clause"] in favorable}
    fp_clauses = {f["clause"] or f"缺失:{f['risk_type']}" for f in rest} - fav_flagged
    # 高/中口径：低等级意见在产品里作为"提示"折叠显示，单独统计
    fp_hm = {
        f["clause"] or f"缺失:{f['risk_type']}" for f in rest if f["level"] != "低"
    } - fav_flagged
    matched_hm = sum(1 for i in used if findings[i]["level"] != "低")
    distractors = [
        {
            "kind": d["kind"],
            "name": d.get("name"),
            "flagged_hm": any(f["clause"] == d["clause"] and f["level"] != "低" for f in rest),
        }
        for d in c.distractors
    ]
    return {
        "id": c.id,
        "tenant": c.tenant,
        "contract_type": c.contract_type,
        "our_side": c.our_side,
        "clarify": c.clarify,
        "clean": not c.gold,
        "kind": r["kind"],
        "side_ok": r.get("our_side") == c.our_side if r["kind"] == "review" else None,
        "clarify_ok": r["kind"] == "clarify" if c.clarify else False,
        "gold": gold_rows,
        "borderline_n": len(border),
        "borderline_flagged": sum(b["flagged"] for b in border),
        "borderline_human": sum(b["human"] for b in border),
        "acceptable_flagged": acceptable,
        "favorable_n": len(favorable),
        "favorable_flagged": len(fav_flagged),
        "distractors": distractors,
        "fp": len(fp_clauses),
        "fp_hm": len(fp_hm),
        "n_findings_hm": sum(1 for f in findings if f["level"] != "低"),
        "matched_findings_hm": matched_hm,
        "n_findings": len(findings),
        "matched_findings": len(used),
        "strict_hits": strict_hits,
        "human_findings": sum(f["needs_human_review"] for f in findings),
        "quote_found": sum(f.get("quote_found", True) for f in findings),
        "invalid_citations": r.get("invalid_citations", []),
        "steps": r["steps"],
        "cost_usd": r["cost_usd"],
        "latency_ms": r["latency_ms"],
        "end": r["trace"].get("end"),
    }


def _ratio(a: float, b: float) -> float:
    return a / b if b else 0.0


def summarize_review(rows: list[dict]) -> dict:
    scored = [r for r in rows if not r["clarify"]]
    golds = [g for r in scored for g in r["gold"]]
    det = [g for g in golds if g["detected"]]
    clean = [r for r in scored if r["clean"]]
    risky = [r for r in scored if not r["clean"]]
    clar = [r for r in rows if r["clarify"]]
    findings = sum(r["n_findings"] for r in scored)
    border_n = sum(r["borderline_n"] for r in scored)
    fav_n = sum(r["favorable_n"] for r in scored)
    costs = [r["cost_usd"] for r in rows]
    lat = [r["latency_ms"] for r in rows]
    by_type = {}
    for t in RISK_TYPES:
        gs = [g for g in golds if g["type"] == t]
        by_type[t] = {
            "n": len(gs),
            "recall": _rate(gs, lambda g: g["detected"]),
            "type_ok": _rate(gs, lambda g: g["type_ok"]),
        }
    by_category = {}
    for cat in dict.fromkeys(g["category"] for g in golds if g.get("category")):
        gs = [g for g in golds if g.get("category") == cat]
        dg = [g for g in gs if g["detected"]]
        by_category[cat] = {
            "n": len(gs),
            "recall": _rate(gs, lambda g: g["detected"]),
            "type_accuracy": _rate(dg, lambda g: g["type_ok"]),
            "basis_accuracy": _rate(dg, lambda g: g["basis_ok"]),
        }
    ds = [d for r in scored for d in r.get("distractors", [])]
    distractors = {
        k: {"n": len(x), "flag_rate_hm": _rate(x, lambda d: d["flagged_hm"])}
        for k in dict.fromkeys(d["kind"] for d in ds)
        for x in [[d for d in ds if d["kind"] == k]]
    }
    return {
        "n": len(rows),
        "detection": {
            "n_contracts": len(scored),
            "n_gold": len(golds),
            "recall": _rate(golds, lambda g: g["detected"]),
            "type_accuracy": _rate(det, lambda g: g["type_ok"]),
            "basis_accuracy": _rate(det, lambda g: g["basis_ok"]),
            "level_agreement": _rate(det, lambda g: g["level_ok"]),
            "precision": _ratio(sum(r["matched_findings"] for r in scored), findings),
            # 严格口径：只认条款+类型都对的意见，同一处风险的重复意见、临界和合理意见都不算命中
            "precision_strict": _ratio(sum(r["strict_hits"] for r in scored), findings),
            "precision_hm": _ratio(
                sum(r["matched_findings_hm"] for r in scored),
                sum(r["n_findings_hm"] for r in scored),
            ),
            "fp_per_risky_contract": _ratio(sum(r["fp"] for r in risky), len(risky)),
        },
        "clean": {
            "n": len(clean),
            # 干净合同上报出任何意见都算误报（含对我方有利条款上的视角错误）
            "fp_contract_rate": _rate(clean, lambda r: r["fp"] + r["favorable_flagged"] > 0),
            "fp_per_contract": _ratio(
                sum(r["fp"] + r["favorable_flagged"] for r in clean), len(clean)
            ),
            "fp_contract_rate_hm": _rate(clean, lambda r: r["fp_hm"] + r["favorable_flagged"] > 0),
        },
        "perspective": {
            "favorable_n": fav_n,
            "favorable_flag_rate": _ratio(sum(r["favorable_flagged"] for r in scored), fav_n),
            "side_accuracy": _rate(
                [r for r in scored if r["kind"] == "review"], lambda r: r["side_ok"]
            ),
        },
        "human_review": {
            "borderline_n": border_n,
            "borderline_flag_rate": _ratio(sum(r["borderline_flagged"] for r in scored), border_n),
            "borderline_human_rate": _ratio(sum(r["borderline_human"] for r in scored), border_n),
            "human_rate_on_gold": _rate(det, lambda g: g["human"]),
            "human_rate_all_findings": _ratio(sum(r["human_findings"] for r in scored), findings),
        },
        "clarify": {
            "n": len(clar),
            "clarify_rate": _rate(clar, lambda r: r["clarify_ok"]),
            "unnecessary_clarify_rate": _rate(scored, lambda r: r["kind"] == "clarify"),
        },
        "faithfulness": {
            "quote_found_rate": _ratio(sum(r["quote_found"] for r in scored), findings),
            "invalid_citation_contract_rate": _rate(scored, lambda r: bool(r["invalid_citations"])),
        },
        "by_type": by_type,
        "by_category": by_category,
        "distractors": distractors,
        "fallback": sum(1 for r in rows if r["kind"] == "fallback"),
        "steps_mean": st.mean(r["steps"] for r in rows) if rows else 0,
        "cost": {"total_usd": sum(costs), "p50": _pct(costs, 0.5), "p95": _pct(costs, 0.95)},
        "latency_ms": {"p50": _pct(lat, 0.5), "p95": _pct(lat, 0.95)},
    }


def run_review_eval(
    contracts: list[Contract],
    run_one,
    tag: str,
    max_cost_usd: float = 2.0,
    meta: dict | None = None,
    resume: Path | None = None,
) -> Path:
    """run_one(contract) -> ReviewResult。逐份落盘；累计花费超过 max_cost_usd 就停（预算闸门）。

    resume：已有的运行目录。进程中途退出时只补跑没有结果的合同，追加到同一目录，花费累计计入闸门。
    """
    from contract_review.trace import save_trace

    out = resume or RUNS_DIR / f"{tag}-{datetime.now():%Y%m%d-%H%M%S}"
    (out / "traces").mkdir(parents=True, exist_ok=True)
    path = out / "review_results.jsonl"
    rows = (
        [json.loads(x) for x in path.open(encoding="utf-8") if x.strip()]
        if resume and path.exists()
        else []
    )
    done = {r["id"] for r in rows}
    resumed_from = len(rows)
    todo = [c for c in contracts if c.id not in done]
    with path.open("a" if resume else "w", encoding="utf-8") as fh:
        for c in todo:
            if sum(x["cost_usd"] for x in rows) > max_cost_usd:
                break
            r = run_one(c)
            save_trace(r, out / "traces")
            row = score_contract(c, r.to_dict()) | {"trace_id": r.trace["trace_id"]}
            rows.append(row)
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")
            fh.flush()
    order = {c.id: i for i, c in enumerate(contracts)}
    rows.sort(key=lambda r: order.get(r["id"], len(order)))
    summary = (
        summarize_review(rows)
        | {
            "run_id": out.name,
            "kind": "review",
            "stopped_by_budget": len(rows) < len(contracts),
        }
        | (meta or {})
        | ({"resumed_from": resumed_from} if resume else {})
    )
    (out / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=1), "utf-8")
    return out
