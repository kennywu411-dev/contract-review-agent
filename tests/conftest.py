import hashlib

import numpy as np
import pytest


class FakeEncoder:
    """按字符哈希把文本映射到 64 维、已归一化的向量。共享字符越多，向量越相似。"""

    def __init__(self):
        self.calls = 0

    def encode(self, texts):
        self.calls += 1
        out = np.zeros((len(texts), 64), dtype=np.float32)
        for i, t in enumerate(texts):
            for ch in t:
                out[i, int(hashlib.md5(ch.encode()).hexdigest(), 16) % 64] += 1
        norms = np.linalg.norm(out, axis=1, keepdims=True)
        return out / np.where(norms == 0, 1, norms)


@pytest.fixture
def fake_encoder():
    return FakeEncoder()


def pytest_collection_modifyitems(config, items):
    if config.getoption("-m") and "slow" in config.getoption("-m"):
        return
    skip = pytest.mark.skip(reason="需要真模型，用 -m slow 运行")
    for item in items:
        if "slow" in item.keywords:
            item.add_marker(skip)
