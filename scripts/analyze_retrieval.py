"""分析一次检索评测：未命中题清单、重排最高分分布。

用法：uv run python scripts/analyze_retrieval.py runs/<run_id> section/hybrid_rerank
"""

import json
import statistics as st
import sys
from pathlib import Path

from contract_review.chunking import load_chunks
from contract_review.config import DB_PATH
from contract_review.evalset import load_questions


def quantiles(xs: list[float]) -> str:
    if not xs:
        return "-"
    q = st.quantiles(xs, n=10) if len(xs) >= 10 else xs
    return (
        f"n={len(xs)} min={min(xs):.3f} p10={q[0]:.3f} 中位={st.median(xs):.3f} max={max(xs):.3f}"
    )


def main() -> None:
    run, config = Path(sys.argv[1]), sys.argv[2]
    qs = {q.id: q for q in load_questions()}
    rows = [json.loads(line) for line in (run / "results.jsonl").open(encoding="utf-8")]
    rows = [r for r in rows if r["config"] == config]
    chunks = {c.chunk_id: c for c in load_chunks(DB_PATH, config.split("/")[0])}
    summary = json.loads((run / "summary.json").read_text(encoding="utf-8"))[config]
    print(f"== {config}：可回答题最高分 {quantiles(summary['answer_top_scores'])}")
    print(f"   拒答题最高分 {quantiles(summary['refuse_top_scores'])}")
    for r in rows:
        q = qs[r["qid"]]
        if q.type == "refuse" or (r["rank"] is not None and r["rank"] <= 5):
            continue
        gold = [
            f"{g['doc_id']}:{g.get('page') or g.get('clause')}" for g in q.gold if "doc_id" in g
        ]
        print(f"\n[{r['qid']} {q.type}/{q.subtype}/{q.source_type} rank={r['rank']}] {q.question}")
        print(f"   gold: {gold}")
        top = [chunks[c] for c in r["top10"][:3]]
        print("   top3: " + " | ".join(f"{c.doc_id}:{c.pages} {c.text[:30]!r}" for c in top))


if __name__ == "__main__":
    main()
