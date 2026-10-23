import pytest

from docpilot.chunking import Chunk
from docpilot.retrieve import Retriever


def ch(i, school, text, doc="d"):
    return Chunk(f"{doc}{i}:block:0", f"{doc}{i}", school, "规定", [], text, [1], [0])


CHUNKS = [
    ch(0, "bnu", "北师大转专业每学年申请一次"),
    ch(1, "upc", "中石油转专业每学年申请一次"),
    ch(2, "national", "国家奖学金每生每年一万元"),
    ch(3, "bnu", "缓考须在考前提交申请"),
    ch(4, "zzu", "郑大转专业办法"),
]


class FakeReranker:
    """分数 = 与查询共享的字符数，用来检验重排确实改变了顺序。"""

    def __init__(self, boost=None):
        self.boost = boost or {}
        self.seen = []

    def score(self, query, texts):
        self.seen.append(list(texts))
        return [
            len(set(query) & set(t)) / 10 + sum(v for k, v in self.boost.items() if k in t)
            for t in texts
        ]


@pytest.fixture
def retr(tmp_path, fake_encoder):
    return Retriever(CHUNKS, fake_encoder, FakeReranker(), tmp_path)


@pytest.mark.parametrize("mode", ["bm25", "vector", "hybrid", "hybrid_rerank"])
def test_school_filter_holds_after_fusion_and_rerank(retr, mode):
    res = retr.search("转专业申请", "bnu", k=5, mode=mode)
    assert res.hits and {h.chunk.school for h in res.hits} <= {"bnu", "national"}


def test_national_docs_included_for_every_school(retr):
    for school in ("bnu", "upc", "zzu"):
        res = retr.search("国家奖学金多少钱", school, k=5, mode="hybrid")
        assert any(h.chunk.school == "national" for h in res.hits)


def test_rrf_prefers_docs_found_by_both(tmp_path, fake_encoder):
    r = Retriever(CHUNKS, fake_encoder, None, tmp_path)
    res = r.search("北师大转专业", "bnu", k=2, mode="hybrid")
    top = res.hits[0]
    assert top.chunk.doc_id == "d0" and top.bm25_rank == 1 and top.vec_rank == 1


def test_rerank_reorders_pool(tmp_path, fake_encoder):
    rr = FakeReranker(boost={"缓考须在考前提交申请": 5})
    r = Retriever(CHUNKS, fake_encoder, rr, tmp_path)
    res = r.search("转专业申请", "bnu", k=3, mode="hybrid_rerank")
    assert res.hits[0].chunk.doc_id == "d3" and rr.seen


def test_mentioned_other_school_is_flagged(retr):
    assert retr.search("郑大的转专业要求是什么", "bnu").school_mismatch == "zzu"
    assert retr.search("北师大转专业", "bnu").school_mismatch is None
    assert retr.search("北京师范大学转专业", "upc").school_mismatch == "bnu"


@pytest.mark.parametrize("q", ["", "转" * 5000, "GPA 3.5", "2025"])
def test_degenerate_queries(retr, q):
    res = retr.search(q, "bnu", k=5)
    assert isinstance(res.hits, list)
    assert all(h.chunk.school in ("bnu", "national") for h in res.hits)


def test_latency_recorded_per_stage(retr):
    lat = retr.search("转专业", "bnu", mode="hybrid_rerank").latency_ms
    assert {"bm25", "vector", "rerank", "total"} <= set(lat)
