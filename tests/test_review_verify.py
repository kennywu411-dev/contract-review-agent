from contract_review.review_verify import law_refs, unretrieved_refs


def test_law_refs_extracts_named_articles_only():
    text = (
        "依据《民法典》第五百八十五条和合同编通则解释第六十五条，违约金过高；"
        "本公司规范第十一条要求不超过20%。合同第八条只约束我方。民事诉讼法第35条允许协议管辖。"
    )
    assert law_refs(text) == {
        ("law-civil-code", "第五百八十五条"),
        ("law-contract-interp-2023", "第六十五条"),
        ("policy", "第十一条"),
        ("law-civil-procedure", "第三十五条"),
    }


def test_law_refs_handles_lists_and_variants():
    assert law_refs("民法典第五百八十六条、第五百八十八条") == {
        ("law-civil-code", "第五百八十六条"),
        ("law-civil-code", "第五百八十八条"),
    }
    assert law_refs("司法解释第67条") == {("law-contract-interp-2023", "第六十七条")}
    assert law_refs("细则第十五条") == {("policy", "第十五条")}
    assert law_refs("第四条约定的检验期过短") == set()


def test_unretrieved_refs():
    citations = {
        "C1": {"kind": "doc", "doc_id": "law-civil-code", "articles": ["第五百八十五条"]},
        "C2": {"kind": "doc", "doc_id": "huaxin-contract-policy", "articles": ["第十一条"]},
    }
    f = {
        "reason": "违反《民法典》第五百八十五条和本公司规范第十一条，另见民法典第五百八十四条。",
        "suggestion": "",
    }
    assert unretrieved_refs(f, citations, "huaxin") == [("law-civil-code", "第五百八十四条")]
