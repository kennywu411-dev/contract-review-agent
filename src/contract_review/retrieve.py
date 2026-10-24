"""检索：租户过滤 → BM25 / 向量 → RRF 融合 → 交叉编码器重排。

过滤在每一路检索内部做（allow 回调），而不是检索完再删，保证前 k 个里不会被别家公司的规范占位。
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal, Protocol

from contract_review.bm25 import BM25, tokenize
from contract_review.chunking import Chunk, embed_text
from contract_review.vector import Encoder, VectorIndex

Mode = Literal["bm25", "vector", "hybrid", "hybrid_rerank"]
MODES: tuple[Mode, ...] = ("bm25", "vector", "hybrid", "hybrid_rerank")
RRF_K = 60

# 问题里提到的租户公司（全称或简称）→ 租户 id，用来发现"当前是 A 公司、问的却是 B 公司的规范"
TENANT_ALIASES = {
    "huaxin": ("华信制造有限公司", "华信制造", "华信"),
    "yunqi": ("云启科技有限公司", "云启科技", "云启"),
    "ruifeng": ("瑞丰贸易有限公司", "瑞丰贸易", "瑞丰"),
}


class Reranker(Protocol):
    def score(self, query: str, texts: list[str]) -> list[float]: ...


@dataclass
class Hit:
    chunk: Chunk
    score: float
    bm25_rank: int | None = None
    vec_rank: int | None = None
    index: int = -1  # 在 Retriever.chunks 里的下标


@dataclass
class SearchResult:
    hits: list[Hit]
    tenant_mismatch: str | None = None
    latency_ms: dict[str, float] = field(default_factory=dict)


def mentioned_tenant(query: str, tenant: str) -> str | None:
    for sid, names in TENANT_ALIASES.items():
        if sid != tenant and any(n in query for n in names):
            if not any(n in query for n in TENANT_ALIASES.get(tenant, ())):
                return sid
    return None


class Retriever:
    def __init__(
        self,
        chunks: list[Chunk],
        encoder: Encoder,
        reranker: Reranker | None,
        cache_dir: Path,
        vector_backend: Literal["numpy", "milvus"] = "numpy",
    ) -> None:
        self.chunks = chunks
        self.encoder, self.reranker = encoder, reranker
        texts = [embed_text(c) for c in chunks]
        self.texts = texts
        self.bm25 = BM25(texts)
        strategy = chunks[0].chunk_id.split(":")[1] if chunks else "empty"
        self.vector_backend = vector_backend
        if vector_backend == "milvus":
            from contract_review.milvus_index import MilvusVectorIndex

            self.vectors = MilvusVectorIndex.build(
                texts,
                [c.tenant for c in chunks],
                encoder,
                cache_dir / f"vectors-{strategy}",
                f"chunks_{strategy}",
            )
        else:
            self.vectors = VectorIndex.build(texts, encoder, cache_dir / f"vectors-{strategy}")

    def search(
        self,
        query: str,
        tenant: str,
        k: int = 5,
        mode: Mode = "hybrid_rerank",
        pool: int = 15,  # ADR 002：候选 15 与 30 的 R@5 相同，耗时减半
    ) -> SearchResult:
        t0 = time.perf_counter()
        lat: dict[str, float] = {}
        if not tokenize(query):
            # 空串、纯标点没有任何信息；向量路仍会返回"最近"的块，那是噪声，不是答案
            return SearchResult([], None, {"total": (time.perf_counter() - t0) * 1000})
        allowed = {tenant, "common"}

        def allow(i: int) -> bool:
            return self.chunks[i].tenant in allowed

        bm_ranks: dict[int, int] = {}
        vec_ranks: dict[int, int] = {}
        if mode != "vector":
            t = time.perf_counter()
            bm = self.bm25.search(query, pool, allow)
            bm_ranks = {i: r for r, (i, _) in enumerate(bm, 1)}
            lat["bm25"] = (time.perf_counter() - t) * 1000
        if mode != "bm25":
            t = time.perf_counter()
            qvec = self.encoder.encode([query])[0]
            vec = self.vectors.search(qvec, pool, allow, tenants=sorted(allowed))
            vec_ranks = {i: r for r, (i, _) in enumerate(vec, 1)}
            lat["vector"] = (time.perf_counter() - t) * 1000

        ids = set(bm_ranks) | set(vec_ranks)
        fused = {
            i: sum(1 / (RRF_K + r) for r in (bm_ranks.get(i), vec_ranks.get(i)) if r) for i in ids
        }
        order = sorted(ids, key=lambda i: -fused[i])
        hits = [Hit(self.chunks[i], fused[i], bm_ranks.get(i), vec_ranks.get(i), i) for i in order]

        if mode == "hybrid_rerank" and self.reranker is not None and hits:
            t = time.perf_counter()
            cand = hits[:pool]
            scores = self.reranker.score(query, [self.texts[h.index] for h in cand])
            for h, s in zip(cand, scores, strict=True):
                h.score = s
            hits = sorted(cand, key=lambda h: -h.score)
            lat["rerank"] = (time.perf_counter() - t) * 1000
        lat["total"] = (time.perf_counter() - t0) * 1000
        return SearchResult(hits[:k], mentioned_tenant(query, tenant), lat)
