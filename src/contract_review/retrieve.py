"""检索：学校过滤 → BM25 / 向量 → RRF 融合 → 交叉编码器重排。

过滤在每一路检索内部做（allow 回调），而不是检索完再删，保证前 k 个里不会被别校内容占位。
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

# 问题里提到的学校（全称或常用简称）→ 学校 id，用来发现"选的是 A 校、问的是 B 校"
TENANT_ALIASES = {
    "huaxin": ("北京师范大学", "北师大"),
    "yunqi": ("中国石油大学", "中石油", "石大华东", "石油大学"),
    "njust": ("南京理工大学", "南理工"),
    "lzu": ("兰州大学", "兰大"),
    "cumt": ("中国矿业大学", "矿大"),
    "ruifeng": ("郑州大学", "郑大"),
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
            # "中国石油大学"里没有别校简称；但"北京师范大学"不能被当成别的学校
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
    ) -> None:
        self.chunks = chunks
        self.encoder, self.reranker = encoder, reranker
        texts = [embed_text(c) for c in chunks]
        self.texts = texts
        self.bm25 = BM25(texts)
        strategy = chunks[0].chunk_id.split(":")[1] if chunks else "empty"
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
            vec = self.vectors.search(qvec, pool, allow)
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
