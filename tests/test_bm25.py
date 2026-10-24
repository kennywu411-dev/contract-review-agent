from contract_review.bm25 import BM25, tokenize

DOCS = [
    "本科生转专业管理办法 第三条 学生可以申请转专业一次",
    "国家奖学金每生每年10000元",
    "学生应当遵守学校规定 学生应当按时注册",
    "缓考申请应在考试前提交",
]


def test_tokenize_bigrams():
    toks = tokenize("转专业，GPA 3.5！")
    assert {"转专", "专业", "转", "gpa", "3.5"} <= set(toks)
    assert "，" not in toks and "！" not in toks


def test_tokenize_nfkc():
    assert tokenize("ＧＰＡ") == tokenize("gpa")


def test_exact_term_ranks_first():
    assert BM25(DOCS).search("怎么转专业", k=2)[0][0] == 0


def test_idf_downweights_common_terms():
    docs = ["学生 学生 学生 学生", "学生 缓考", "学生 注册"]
    assert BM25(docs).search("学生缓考", k=3)[0][0] == 1


def test_allow_filter_applied_before_topk():
    bm = BM25(DOCS)
    assert bm.search("学生", k=1)[0][0] == 2
    assert [i for i, _ in bm.search("学生", k=1, allow=lambda i: i == 0)] == [0]


def test_empty_query_returns_empty():
    assert BM25(DOCS).search("", k=5) == []
    assert BM25(DOCS).search("！！", k=5) == []
