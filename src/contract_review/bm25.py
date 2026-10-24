"""手写 BM25。中文按相邻两字切词（另保留单字），不依赖分词库。"""

from __future__ import annotations

import math
import re
import unicodedata
from collections import Counter
from collections.abc import Callable

_TOKEN = re.compile(r"[㐀-鿿]+|[a-z0-9]+(?:\.[0-9]+)?")


def tokenize(text: str) -> list[str]:
    text = unicodedata.normalize("NFKC", text).lower()
    out: list[str] = []
    for run in _TOKEN.findall(text):
        if run[0] >= "㐀":
            out += list(run)  # 单字：保证"缓""考"这类单字查询也能命中
            out += [run[i : i + 2] for i in range(len(run) - 1)]
        else:
            out.append(run)
    return out


class BM25:
    def __init__(self, docs: list[str], k1: float = 1.5, b: float = 0.75) -> None:
        self.k1, self.b = k1, b
        self.tfs = [Counter(tokenize(d)) for d in docs]
        self.lens = [sum(tf.values()) for tf in self.tfs]
        self.avg = sum(self.lens) / len(self.lens) if docs else 0.0
        df: Counter[str] = Counter()
        for tf in self.tfs:
            df.update(tf.keys())
        n = len(docs)
        self.idf = {t: math.log(1 + (n - f + 0.5) / (f + 0.5)) for t, f in df.items()}
        # 倒排表：只给含查询词的文档打分
        self.postings: dict[str, list[int]] = {}
        for i, tf in enumerate(self.tfs):
            for t in tf:
                self.postings.setdefault(t, []).append(i)

    def search(
        self, query: str, k: int, allow: Callable[[int], bool] | None = None
    ) -> list[tuple[int, float]]:
        scores: dict[int, float] = {}
        for t in set(tokenize(query)):
            idf = self.idf.get(t)
            if idf is None:
                continue
            for i in self.postings[t]:
                if allow is not None and not allow(i):
                    continue
                f = self.tfs[i][t]
                norm = f + self.k1 * (1 - self.b + self.b * self.lens[i] / self.avg)
                scores[i] = scores.get(i, 0.0) + idf * f * (self.k1 + 1) / norm
        return sorted(scores.items(), key=lambda x: -x[1])[:k]
