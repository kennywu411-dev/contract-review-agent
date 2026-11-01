"""向量索引：numpy 暴力余弦。9k 级别的块，一次矩阵乘法就够，不需要向量库。

缓存按文本 sha1 校验：只重算内容变了的条目（入库规则改了之后不会用到旧向量）。
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from pathlib import Path
from typing import Protocol

import numpy as np


class Encoder(Protocol):
    def encode(self, texts: list[str]) -> np.ndarray: ...


def _sha1(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8")).hexdigest()


class VectorIndex:
    def __init__(self, vectors: np.ndarray) -> None:
        self.vectors = vectors

    @classmethod
    def build(cls, texts: list[str], encoder: Encoder, cache_path: Path) -> VectorIndex:
        path = cache_path.with_suffix(".npz")
        hashes = [_sha1(t) for t in texts]
        cached: dict[str, np.ndarray] = {}
        if path.exists():
            data = np.load(path)
            cached = dict(zip(data["hashes"].tolist(), data["vectors"], strict=True))
        todo = [i for i, h in enumerate(hashes) if h not in cached]
        if todo:
            fresh = encoder.encode([texts[i] for i in todo])
            for i, v in zip(todo, fresh, strict=True):
                cached[hashes[i]] = v
        vectors = np.stack([cached[h] for h in hashes]).astype(np.float32)
        if todo or not path.exists():
            path.parent.mkdir(parents=True, exist_ok=True)
            np.savez(path, vectors=vectors, hashes=np.array(hashes))
        return cls(vectors)

    def search(
        self,
        qvec: np.ndarray,
        k: int,
        allow: Callable[[int], bool] | None = None,
        tenants: list[str] | None = None,  # numpy 后端用 allow 过滤；参数与 Milvus 后端对齐
    ) -> list[tuple[int, float]]:
        scores = self.vectors @ qvec
        order = np.argsort(-scores)
        out = []
        for i in order:
            if allow is None or allow(int(i)):
                out.append((int(i), float(scores[i])))
                if len(out) == k:
                    break
        return out
