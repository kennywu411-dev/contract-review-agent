"""把一次合同审查全量运行拆成"调试小样"和"留出集"分别汇总（P20：小样参与过调试，留出集没有）。

用法：uv run python scripts/analyze_review.py runs/<run_id> [--sample 20]
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from contract_review.contract_evalset import load_contracts, sample_contracts
from contract_review.eval_review import summarize_review


def brief(s: dict) -> dict:
    d, cl, h = s["detection"], s["clean"], s["human_review"]
    return {
        "n": s["n"],
        "gold": d["n_gold"],
        "recall": round(d["recall"], 3),
        "type_acc": round(d["type_accuracy"], 3),
        "basis_acc": round(d["basis_accuracy"], 3),
        "precision": round(d["precision"], 3),
        "precision_hm": round(d["precision_hm"], 3),
        "clean_n": cl["n"],
        "clean_fp_rate": round(cl["fp_contract_rate"], 3),
        "clean_fp_rate_hm": round(cl["fp_contract_rate_hm"], 3),
        "favorable_flag": round(s["perspective"]["favorable_flag_rate"], 3),
        "side_acc": round(s["perspective"]["side_accuracy"], 3),
        "borderline_human": round(h["borderline_human_rate"], 3),
        "borderline_n": h["borderline_n"],
        "clarify": round(s["clarify"]["clarify_rate"], 3),
        "clarify_n": s["clarify"]["n"],
        "quote_found": round(s["faithfulness"]["quote_found_rate"], 3),
        "cost_total": round(s["cost"]["total_usd"], 4),
        "cost_p50": round(s["cost"]["p50"], 4),
        "lat_p50_s": round(s["latency_ms"]["p50"] / 1000, 1),
        "lat_p95_s": round(s["latency_ms"]["p95"] / 1000, 1),
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("run", type=Path)
    ap.add_argument("--sample", type=int, default=20)
    args = ap.parse_args()
    rows = [json.loads(x) for x in (args.run / "review_results.jsonl").open(encoding="utf-8")]
    dev = {c.id for c in sample_contracts(load_contracts(), args.sample)}
    out = {
        "all": brief(summarize_review(rows)),
        "dev": brief(summarize_review([r for r in rows if r["id"] in dev])),
        "holdout": brief(summarize_review([r for r in rows if r["id"] not in dev])),
    }
    s = summarize_review(rows)
    out["by_type_recall"] = {k: round(v["recall"], 3) for k, v in s["by_type"].items()}
    print(json.dumps(out, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
