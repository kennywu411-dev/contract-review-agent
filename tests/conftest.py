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
    _skip_without_local_data(items)
    if config.getoption("-m") and "slow" in config.getoption("-m"):
        return
    skip = pytest.mark.skip(reason="需要真模型，用 -m slow 运行")
    for item in items:
        if "slow" in item.keywords:
            item.add_marker(skip)


@pytest.fixture(scope="session", autouse=True)
def _ledger_db():
    """台账库由仓库里的构造 CSV 生成；干净检出时还没有，先建好再跑测试。"""
    from contract_review.config import LEDGER_DB, LEDGER_SRC
    from contract_review.ledger_db import build_ledger_db

    if not LEDGER_DB.exists():
        build_ledger_db(LEDGER_SRC, LEDGER_DB)


# 依赖 `cra ingest` 下载入库的法规语料，或本地评测运行记录；干净检出时没有，跳过而不是报错
_NEEDS_CORPUS = {
    "test_pdf_served_for_known_doc",
    "test_highlight_returns_page_boxes",
    "test_html_doc_blocks",
    "test_basis_articles_exist_in_corpus",
    "test_v2_basis_articles_exist_in_corpus",
}
_NEEDS_RUNS = {"test_runs_listing_mixed_types"}


def _skip_without_local_data(items):
    from contract_review.config import DATA_DIR, ROOT

    no_corpus = not (DATA_DIR / "raw").is_dir()
    no_runs = not (ROOT / "runs").is_dir()
    for item in items:
        name = item.originalname
        if no_corpus and name in _NEEDS_CORPUS:
            item.add_marker(pytest.mark.skip(reason="需要先运行 cra ingest"))
        elif no_runs and name in _NEEDS_RUNS:
            item.add_marker(pytest.mark.skip(reason="没有本地评测运行记录"))
