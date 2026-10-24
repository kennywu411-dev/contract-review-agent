"""向量后端微基准：查询向量先编码好，只测检索本身（含租户过滤），两种后端交替跑，排除编码和负载波动。

用法：uv run python scripts/bench_vector_backend.py [--rounds 5]
"""

from __future__ import annotations

import argparse
import json
import time

from contract_review.chunking import embed_text, load_chunks
from contract_review.config import DATA_DIR, DB_PATH
from contract_review.contract_evalset import load_contracts
from contract_review.embed import BgeM3Encoder
from contract_review.eval_contract_retrieval import build_queries
from contract_review.milvus_index import MilvusVectorIndex
from contract_review.vector import VectorIndex

POOL = 15


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--rounds", type=int, default=5)
    args = ap.parse_args()
    chunks = load_chunks(DB_PATH, "fixed")
    texts = [embed_text(c) for c in chunks]
    tenants = [c.tenant for c in chunks]
    enc = BgeM3Encoder()
    cache = DATA_DIR / "index" / "vectors-fixed"
    np_idx = VectorIndex.build(texts, enc, cache)
    mv_idx = MilvusVectorIndex.build(texts, tenants, enc, cache, "chunks_fixed")
    qs = build_queries(load_contracts())
    qvecs = enc.encode([q["query"] for q in qs])
    same = 0
    times = {"numpy": [], "milvus": []}
    for _ in range(args.rounds):
        for q, v in zip(qs, qvecs, strict=True):
            allowed = {q["tenant"], "common"}
            t = time.perf_counter()
            a = np_idx.search(v, POOL, lambda i, al=allowed: tenants[i] in al)
            times["numpy"].append((time.perf_counter() - t) * 1000)
            t = time.perf_counter()
            b = mv_idx.search(v, POOL, tenants=sorted(allowed))
            times["milvus"].append((time.perf_counter() - t) * 1000)
            same += [i for i, _ in a] == [i for i, _ in b]
    out = {
        "chunks": len(chunks),
        "queries": len(qs),
        "rounds": args.rounds,
        "top15_identical_rate": same / (len(qs) * args.rounds),
    }
    for k, xs in times.items():
        xs.sort()
        out[k] = {"p50_ms": round(xs[len(xs) // 2], 3), "p95_ms": round(xs[int(len(xs) * 0.95)], 3)}
    print(json.dumps(out, ensure_ascii=False))


if __name__ == "__main__":
    main()
