"""对比两次合同审查全量运行（如 Haiku 与 DeepSeek）：都按当前金标准从 trace 重新判分（ADR 010）。

用法：uv run python scripts/compare_models.py runs/<运行A> runs/<运行B>
输出：全部 / 留出集的检出、类型、依据、精确率（含严格口径）、干净合同误报、转人工、反问、
单份花费、模型耗时（各步 llm_ms 之和，不受本机检索负载影响）、兜底数。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from analyze_review import brief, rescore

from contract_review.contract_evalset import load_contracts, sample_contracts
from contract_review.eval_agent import _pct
from contract_review.eval_review import summarize_review


def llm_latency(run: Path, rows: list[dict]) -> dict:
    xs = []
    for r in rows:
        t = json.loads((run / "traces" / f"{r['trace_id']}.json").read_text(encoding="utf-8"))
        xs.append(sum(s["llm_ms"] for s in t["steps"]) / 1000)
    return {"p50_s": round(_pct(xs, 0.5), 1), "p95_s": round(_pct(xs, 0.95), 1)}


def report(run: Path) -> dict:
    rows = [json.loads(x) for x in (run / "review_results.jsonl").open(encoding="utf-8")]
    rows = rescore(run, rows)
    dev = {c.id for c in sample_contracts(load_contracts(), 20)}
    hold = [r for r in rows if r["id"] not in dev]
    s = summarize_review(rows)
    return {
        "run": run.name,
        "all": brief(s),
        "holdout": brief(summarize_review(hold)),
        "fallback": s["fallback"],
        "steps_mean": round(s["steps_mean"], 2),
        "llm_latency": llm_latency(run, rows),
    }


def main() -> None:
    out = [report(Path(p)) for p in sys.argv[1:]]
    print(json.dumps(out, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
