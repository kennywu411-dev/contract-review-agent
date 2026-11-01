"""v2 难评测集多次运行的对照（ADR 013）：分类检出、漏检模板、精确率、干扰项误报、花费，全部从落盘结果重算。

用法（在仓库根目录）：uv run python scripts/compare_v2.py runs/<运行1> runs/<运行2> ...
可加 --split dev|holdout 只看开发集（改进轮调参用的 20 份）或留出集（另外 40 份）。
"""

from __future__ import annotations

import argparse
import json
import re
from collections import Counter
from pathlib import Path

from contract_review.contract_evalset import CONTRACTS_V2_PATH, load_contracts
from contract_review.eval_review import score_contract, summarize_review


def dev_ids(cs) -> set[str]:
    """改进轮的开发集：每类合同里编号最小的 1–2 份风险合同和干净合同，共 20 份（固定、与结果无关）。"""
    out, seen = set(), Counter()
    quota = {"r": 10, "c": 10}
    for c in sorted(cs, key=lambda c: (int(re.search(r"(\d+)$", c.id).group(1)), c.id)):
        kind = "r" if c.gold else "c"
        if seen[kind] < quota[kind]:
            out.add(c.id)
            seen[kind] += 1
    return out


def rows_of(run: Path, cs: dict) -> list[dict]:
    rows = []
    for line in (run / "review_results.jsonl").open(encoding="utf-8"):
        r = json.loads(line)
        t = json.loads((run / "traces" / f"{r['trace_id']}.json").read_text(encoding="utf-8"))
        res = t["result"] | {
            "citations": t["citations"],
            "steps": len(t["steps"]),
            "cost_usd": t["cost_usd"],
            "latency_ms": t["latency_ms"],
            "trace": {"end": t.get("end")},
        }
        rows.append(score_contract(cs[r["id"]], res) | {"trace_id": r["trace_id"]})
    return rows


def misses(rows: list[dict], cs: dict) -> Counter:
    out = Counter()
    for r in rows:
        c = cs[r["id"]]
        for g, gr in zip(c.gold, r["gold"], strict=True):
            if not gr["detected"]:
                out[g["variant"]] += 1
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("runs", nargs="+")
    ap.add_argument("--split", choices=["all", "dev", "holdout"], default="all")
    a = ap.parse_args()
    allc = load_contracts(CONTRACTS_V2_PATH)
    dev = dev_ids(allc)
    keep = {
        "all": lambda c: True,
        "dev": lambda c: c.id in dev,
        "holdout": lambda c: c.id not in dev,
    }[a.split]
    cs = {c.id: c for c in allc if keep(c)}
    print(f"口径：{a.split}（{len(cs)} 份，风险 {sum(len(c.gold) for c in cs.values())} 处）")
    for run in map(Path, a.runs):
        rows = [r for r in rows_of(run, {c.id: c for c in allc}) if r["id"] in cs]
        s = summarize_review(rows)
        d, cl = s["detection"], s["clean"]
        print(f"\n== {run.name}（{len(rows)} 份）")
        print(
            f"检出 {d['recall']:.1%}  类型 {d['type_accuracy']:.1%}  依据 {d['basis_accuracy']:.1%}  "
            f"精确率 {d['precision']:.1%} / 高中 {d['precision_hm']:.1%} / 严格 {d['precision_strict']:.1%}"
        )
        print(
            f"干净合同误报（高/中）{cl['fp_contract_rate_hm']:.1%}  每份误报 {cl['fp_per_contract']:.2f}  "
            f"视角错误 {s['perspective']['favorable_flag_rate']:.1%}"
        )
        print(
            "分类检出 " + "  ".join(f"{k} {v['recall']:.1%}" for k, v in s["by_category"].items())
        )
        print(
            "干扰项误报 "
            + "  ".join(
                f"{k} {v['flag_rate_hm']:.0%}（{v['n']}）" for k, v in s["distractors"].items()
            )
        )
        fp_risky = sum(r["fp_hm"] for r in rows if not r["clean"])
        print(f"风险合同上的高/中误报 {fp_risky} 条  兜底 {s['fallback']}")
        print(
            f"花费 ${s['cost']['total_usd']:.3f}（P50 ${s['cost']['p50']:.4f}）  "
            f"延迟 P50 {s['latency_ms']['p50'] / 1000:.1f}s P95 {s['latency_ms']['p95'] / 1000:.1f}s"
        )
        print("漏检模板 " + ", ".join(f"{k}×{v}" for k, v in misses(rows, cs).most_common()))


if __name__ == "__main__":
    main()
