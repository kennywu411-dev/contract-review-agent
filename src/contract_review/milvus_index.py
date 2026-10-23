"""Milvus Lite 向量后端（可选依赖 `uv sync --extra milvus`），与 numpy 后端可切换（ADR 008）。

向量本身仍走 VectorIndex 的 sha1 缓存（不重复编码）；这里只负责把向量和租户同步进 Milvus Lite，
检索时把租户条件下推成过滤表达式，而不是取回后再删，保证前 k 个里不会被别家租户占位。
内容指纹（全部文本 sha1 + 租户）不变时直接复用集合，变了才重建。
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

import numpy as np

from contract_review.vector import Encoder, VectorIndex

BATCH = 500
_SAFE_TENANT = re.compile(r"^[a-z0-9_]+$")


def _fingerprint(texts: list[str], tenants: list[str]) -> str:
    h = hashlib.sha1()
    for t, ten in zip(texts, tenants, strict=True):
        h.update(hashlib.sha1(t.encode("utf-8")).digest())
        h.update(ten.encode("utf-8"))
    return h.hexdigest()


class MilvusVectorIndex:
    def __init__(self, client, collection: str, rebuilt: bool) -> None:
        self.client, self.collection, self.rebuilt = client, collection, rebuilt

    @classmethod
    def build(
        cls,
        texts: list[str],
        tenants: list[str],
        encoder: Encoder,
        cache_path: Path,
        collection: str,
    ) -> MilvusVectorIndex:
        from pymilvus import MilvusClient

        vectors = VectorIndex.build(texts, encoder, cache_path).vectors
        db = cache_path.parent / "milvus.db"
        meta_path = cache_path.with_suffix(".milvus.json")
        name = re.sub(r"[^A-Za-z0-9_]", "_", collection)
        client = MilvusClient(str(db))
        fp = _fingerprint(texts, tenants)
        meta = json.loads(meta_path.read_text("utf-8")) if meta_path.exists() else {}
        if client.has_collection(name) and meta.get("fingerprint") == fp:
            return cls(client, name, rebuilt=False)
        if client.has_collection(name):
            client.drop_collection(name)
        client.create_collection(name, dimension=int(vectors.shape[1]), metric_type="IP")
        rows = [
            {"id": i, "vector": vectors[i].tolist(), "tenant": tenants[i]}
            for i in range(len(texts))
        ]
        for start in range(0, len(rows), BATCH):
            client.insert(name, rows[start : start + BATCH])
        meta_path.write_text(json.dumps({"fingerprint": fp, "rows": len(rows)}), "utf-8")
        return cls(client, name, rebuilt=True)

    def search(
        self, qvec: np.ndarray, k: int, allow=None, tenants: list[str] | None = None
    ) -> list[tuple[int, float]]:
        if tenants is None:
            raise ValueError("Milvus 后端需要 tenants 过滤条件")
        if not all(_SAFE_TENANT.match(t) for t in tenants):  # 拼进过滤表达式，只接受固定取值
            raise ValueError(f"非法租户：{tenants}")
        expr = "tenant in [" + ", ".join(f'"{t}"' for t in tenants) + "]"
        res = self.client.search(
            self.collection,
            data=[np.asarray(qvec, dtype=np.float32).tolist()],
            limit=k,
            filter=expr,
            search_params={"metric_type": "IP"},
        )
        return [(int(h["id"]), float(h["distance"])) for h in res[0]]
