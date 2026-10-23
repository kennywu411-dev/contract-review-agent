"""Agent 评测：路由（查表/反问）、拒答、引用命中、成本与延迟。

"引用命中"：answerable/table 题里，答案引用的 C 片段至少一个落在标准出处（同 D2 的 is_hit 口径），
table 题引用了 T（查表结果）也算。它衡量"答案挂在对的原文上"，句子内容是否被原文支撑由 D4 校验。
"""

from __future__ import annotations

import json
import statistics as st
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

from contract_review.eval_retrieval import RUNS_DIR, is_hit
from contract_review.evalset import Question

REFUSED = ("refuse", "fallback")


def _tools_used(r: dict) -> set[str]:
    """只算执行成功的工具调用（报错的 run_sql 不算"走了查表"）。"""
    return {
        c["name"]
        for s in r["trace"]["steps"]
        for c in s.get("tool_calls", [])
        if not c.get("is_error")
    }


def score_one(q: Question, r: dict) -> dict:
    cited = [c for s in r["sentences"] for c in s["citations"]]
    used = _tools_used(r)
    hit = False
    for cid in cited:
        c = r["citations"].get(cid)
        if not c:
            continue
        if c["kind"] == "table" and q.type == "table":
            hit = hit or bool(c.get("rows"))  # 查出 0 行不算命中
        elif c["kind"] == "doc":
            chunk = SimpleNamespace(
                doc_id=c["doc_id"], pages=c["pages"], articles=c["articles"], text=c["text"]
            )
            hit = hit or is_hit(chunk, q.gold)
    return {
        "qid": q.id,
        "type": q.type,
        "subtype": q.subtype,
        "tenant": q.tenant,
        "source_type": q.source_type,
        "kind": r["kind"],
        "answered": r["kind"] == "answer",
        "used_sql": "run_sql" in used,
        "used_clarify": r["kind"] == "clarify",
        "cite_hit": hit,
        "correct_refuse": r["kind"] in REFUSED if q.type == "refuse" else None,
        "invalid_citations": r["invalid_citations"],
        "steps": r["steps"],
        "cost_usd": r["cost_usd"],
        "latency_ms": r["latency_ms"],
        "end": r["trace"]["end"],
    }


def _rate(rows: list[dict], pred) -> float:
    return sum(1 for r in rows if pred(r)) / len(rows) if rows else 0.0


def _pct(xs: list[float], p: float) -> float:
    xs = sorted(xs)
    return xs[min(len(xs) - 1, int(p * len(xs)))] if xs else 0.0


def summarize_agent(rows: list[dict]) -> dict:
    answerable = [r for r in rows if r["type"] != "refuse" and r["subtype"] != "clarify"]
    table = [r for r in rows if r["type"] == "table"]
    refuse = [r for r in rows if r["type"] == "refuse"]
    clarify = [r for r in rows if r["subtype"] == "clarify"]
    costs = [r["cost_usd"] for r in rows]
    lat = [r["latency_ms"] for r in rows]
    by_sub = {}
    for sub in ("cross_school", "version", "conflict"):
        rs = [r for r in answerable if r["subtype"] == sub]
        by_sub[sub] = {"n": len(rs), "cite_hit_rate": _rate(rs, lambda r: r["cite_hit"])}
    return {
        "n": len(rows),
        "answerable": {
            "n": len(answerable),
            "answer_rate": _rate(answerable, lambda r: r["answered"]),
            "false_refusal_rate": _rate(answerable, lambda r: r["kind"] in REFUSED),
            "clarify_rate": _rate(answerable, lambda r: r["kind"] == "clarify"),
            "cite_hit_rate": _rate(answerable, lambda r: r["cite_hit"]),
            "invalid_citation_rate": _rate(answerable, lambda r: bool(r["invalid_citations"])),
        },
        "table": {"n": len(table), "route_rate": _rate(table, lambda r: r["used_sql"])},
        "refuse": {
            "n": len(refuse),
            "correct_refuse_rate": _rate(refuse, lambda r: r["correct_refuse"]),
            "clarify_rate": _rate(refuse, lambda r: r["kind"] == "clarify"),
        },
        "clarify": {"n": len(clarify), "clarify_rate": _rate(clarify, lambda r: r["used_clarify"])},
        "by_subtype": by_sub,
        "fallback": sum(1 for r in rows if r["kind"] == "fallback"),
        "steps_mean": st.mean(r["steps"] for r in rows) if rows else 0,
        "cost": {
            "total_usd": sum(costs),
            "p50": _pct(costs, 0.5),
            "p95": _pct(costs, 0.95),
        },
        "latency_ms": {"p50": _pct(lat, 0.5), "p95": _pct(lat, 0.95)},
    }


def run_agent_eval(questions: list[Question], run_one, tag: str, max_cost_usd: float = 3.0) -> Path:
    """run_one(question) -> AgentResult。逐题落盘；累计花费超过 max_cost_usd 就停（预算闸门）。"""
    from contract_review.trace import save_trace

    out = RUNS_DIR / f"{tag}-{datetime.now():%Y%m%d-%H%M%S}"
    (out / "traces").mkdir(parents=True)
    rows = []
    with (out / "agent_results.jsonl").open("w", encoding="utf-8") as f:
        for q in questions:
            r = run_one(q)
            save_trace(r, out / "traces")
            row = score_one(q, r.to_dict()) | {"trace_id": r.trace["trace_id"]}
            rows.append(row)
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
            f.flush()
            if sum(r["cost_usd"] for r in rows) > max_cost_usd:
                break
    summary = summarize_agent(rows) | {
        "run_id": out.name,
        "stopped_by_budget": len(rows) < len(questions),
    }
    (out / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=1), "utf-8")
    return out
