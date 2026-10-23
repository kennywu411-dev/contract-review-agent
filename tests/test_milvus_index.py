import pytest

from contract_review.retrieve import Retriever
from tests.test_retrieve import CHUNKS, FakeReranker

pytest.importorskip("pymilvus")


@pytest.fixture
def pair(tmp_path, fake_encoder):
    numpy_r = Retriever(CHUNKS, fake_encoder, FakeReranker(), tmp_path / "np")
    milvus_r = Retriever(
        CHUNKS, fake_encoder, FakeReranker(), tmp_path / "mv", vector_backend="milvus"
    )
    return numpy_r, milvus_r


@pytest.mark.parametrize("mode", ["vector", "hybrid", "hybrid_rerank"])
def test_milvus_matches_numpy(pair, mode):
    a, b = pair
    for tenant in ("huaxin", "yunqi"):
        ra = a.search("转专业申请", tenant, k=3, mode=mode)
        rb = b.search("转专业申请", tenant, k=3, mode=mode)
        assert [h.chunk.chunk_id for h in ra.hits] == [h.chunk.chunk_id for h in rb.hits]


def test_milvus_filter_pushed_down(pair):
    _, b = pair
    hits = b.vectors.search(b.encoder.encode(["转专业"])[0], 5, tenants=["yunqi", "common"])
    assert hits and {CHUNKS[i].tenant for i, _ in hits} <= {"yunqi", "common"}


def test_milvus_reuses_collection_when_unchanged(tmp_path, fake_encoder):
    from contract_review.milvus_index import MilvusVectorIndex

    texts, tenants = ["甲方付款", "乙方交货"], ["huaxin", "common"]
    first = MilvusVectorIndex.build(texts, tenants, fake_encoder, tmp_path / "v", "t")
    assert first.rebuilt is True
    again = MilvusVectorIndex.build(texts, tenants, fake_encoder, tmp_path / "v", "t")
    assert again.rebuilt is False
    changed = MilvusVectorIndex.build(texts, ["yunqi", "common"], fake_encoder, tmp_path / "v", "t")
    assert changed.rebuilt is True
