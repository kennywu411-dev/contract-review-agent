"""检索评测：切块策略 × 检索模式，Recall@k / MRR，按题型、学校、来源、子类分组。"""

from __future__ import annotations

import json
import random
import re
import time
from collections import defaultdict
from datetime import datetime
from pathlib import Path

from contract_review.chunking import Chunk
from contract_review.config import ROOT
from contract_review.evalset import Question

RUNS_DIR = ROOT / "runs"
K_LIST = (1, 5, 10)


def is_hit(chunk: Chunk, gold: list[dict]) -> bool:
    for g in gold:
        if g.get("doc_id") != chunk.doc_id:
            continue
        if g.get("page") is not None:
            if g["page"] in chunk.pages:
                return True
        elif g.get("clause"):
            clause = g["clause"]
            if clause in chunk.articles or re.search(rf"(^|\n){re.escape(clause)}", chunk.text):
                return True
    return False


def _metrics(ranks: list[int | None]) -> dict:
    n = len(ranks)
    out: dict = {"n": n}
    for k in K_LIST:
        out[f"recall@{k}"] = sum(1 for r in ranks if r is not None and r <= k) / n if n else 0.0
    out["mrr"] = sum(1 / r for r in ranks if r is not None) / n if n else 0.0
    return out


def summarize(rows: list[dict], questions: dict[str, Question]) -> dict:
    by_config: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        by_config[r["config"]].append(r)
    out = {}
    for config, rs in by_config.items():
        scored = [r for r in rs if questions[r["qid"]].type != "refuse"]
        groups: dict[str, dict[str, list]] = {
            "by_type": defaultdict(list),
            "by_school": defaultdict(list),
            "by_source_type": defaultdict(list),
            "by_subtype": defaultdict(list),
        }
        for r in scored:
            q = questions[r["qid"]]
            groups["by_type"][q.type].append(r["rank"])
            groups["by_school"][q.tenant].append(r["rank"])
            groups["by_source_type"][q.source_type].append(r["rank"])
            if q.subtype:
                groups["by_subtype"][q.subtype].append(r["rank"])
        out[config] = {
            "overall": _metrics([r["rank"] for r in scored]),
            **{g: {k: _metrics(v) for k, v in d.items()} for g, d in groups.items()},
            "refuse_top_scores": [
                r["top_score"] for r in rs if questions[r["qid"]].type == "refuse"
            ],
            "answer_top_scores": [r["top_score"] for r in scored],
            "latency_ms_mean": _mean([r.get("latency_ms", 0.0) for r in rs]),
        }
    return out


def _mean(xs: list[float]) -> float:
    return sum(xs) / len(xs) if xs else 0.0


def stratified_sample(qs: list[Question], n: int, seed: int = 0) -> list[Question]:
    """按 type 分层抽样，小样本先跑通再跑全量。"""
    rng = random.Random(seed)
    by_type: dict[str, list[Question]] = defaultdict(list)
    for q in qs:
        by_type[q.type].append(q)
    out: list[Question] = []
    for items in by_type.values():
        take = max(1, round(n * len(items) / len(qs)))
        out += rng.sample(items, min(take, len(items)))
    return sorted(out, key=lambda q: q.id)


def run_retrieval_eval(
    retrievers: dict, questions: list[Question], modes, tag: str, pool: int = 15
) -> Path:
    """retrievers: 切块策略 → Retriever。结果写到 runs/<run_id>/。"""
    run_id = f"{tag}-{datetime.now():%Y%m%d-%H%M%S}"
    out_dir = RUNS_DIR / run_id
    out_dir.mkdir(parents=True)
    rows = []
    for strategy, retr in retrievers.items():
        for mode in modes:
            config = f"{strategy}/{mode}@pool{pool}"
            for q in questions:
                t = time.perf_counter()
                res = retr.search(q.question, q.tenant, k=max(K_LIST), mode=mode, pool=pool)
                rank = next((i for i, h in enumerate(res.hits, 1) if is_hit(h.chunk, q.gold)), None)
                rows.append(
                    {
                        "qid": q.id,
                        "config": config,
                        "rank": rank,
                        "top_score": res.hits[0].score if res.hits else 0.0,
                        "top10": [h.chunk.chunk_id for h in res.hits],
                        "tenant_mismatch": res.tenant_mismatch,
                        "leak": [
                            h.chunk.chunk_id
                            for h in res.hits
                            if h.chunk.tenant not in (q.tenant, "common")
                        ],
                        "latency_ms": (time.perf_counter() - t) * 1000,
                        "stage_ms": res.latency_ms,
                    }
                )
    with (out_dir / "results.jsonl").open("w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    summary = summarize(rows, {q.id: q for q in questions})
    summary["_meta"] = {
        "run_id": run_id,
        "n_questions": len(questions),
        "leaks": sum(len(r["leak"]) for r in rows),
    }
    (out_dir / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=1), encoding="utf-8"
    )
    return out_dir
