import numpy as np
import pytest

from contract_review.vector import VectorIndex

TEXTS = ["国家奖学金一万元", "转专业申请条件", "缓考办理流程"]


def test_search_returns_most_similar(tmp_path, fake_encoder):
    idx = VectorIndex.build(TEXTS, fake_encoder, tmp_path / "v")
    q = fake_encoder.encode(["转专业要什么条件"])[0]
    assert idx.search(q, k=1)[0][0] == 1


def test_cache_reused_when_unchanged(tmp_path, fake_encoder):
    VectorIndex.build(TEXTS, fake_encoder, tmp_path / "v")
    calls = fake_encoder.calls
    idx = VectorIndex.build(TEXTS, fake_encoder, tmp_path / "v")
    assert fake_encoder.calls == calls and idx.vectors.shape == (3, 64)


def test_stale_cache_is_recomputed(tmp_path, fake_encoder):
    VectorIndex.build(TEXTS, fake_encoder, tmp_path / "v")
    seen = []
    orig = fake_encoder.encode
    fake_encoder.encode = lambda texts: (seen.append(list(texts)), orig(texts))[1]
    changed = [TEXTS[0], "转专业申请条件（修订）", TEXTS[2]]
    idx = VectorIndex.build(changed, fake_encoder, tmp_path / "v")
    assert seen == [["转专业申请条件（修订）"]]
    assert np.allclose(idx.vectors[1], orig(["转专业申请条件（修订）"])[0])


def test_allow_filter(tmp_path, fake_encoder):
    idx = VectorIndex.build(TEXTS, fake_encoder, tmp_path / "v")
    q = fake_encoder.encode(["转专业"])[0]
    assert [i for i, _ in idx.search(q, k=1, allow=lambda i: i != 1)] != [1]


@pytest.mark.slow
def test_bge_m3_similarity_sanity():
    from contract_review.embed import BgeM3Encoder

    v = BgeM3Encoder().encode(["国家奖学金金额", "国奖多少钱", "食堂几点关门"])
    assert v[0] @ v[1] > v[0] @ v[2]


def test_length_sorted_batches_restore_order():
    from contract_review.embed import length_sorted_batches

    texts = ["aaaa", "b", "ccc", "dd", "eeeee"]
    batches = list(length_sorted_batches(texts, 2))
    assert [len(texts[i]) for b in batches for i in b] == [5, 4, 3, 2, 1]
    assert sorted(i for b in batches for i in b) == list(range(5))
