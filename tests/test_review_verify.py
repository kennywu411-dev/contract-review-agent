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


class FakeVerifier:
    def __init__(self, labels):
        self.labels, self.calls = labels, 0

    def label(self, findings, citations, contract):
        self.calls += 1
        return self.labels, 0.001


def _f(reason, cites=("C1",), quote_found=True, human=False):
    return {
        "clause": "第四条",
        "quote": "q",
        "quote_found": quote_found,
        "risk_type": "inspection",
        "level": "高",
        "reason": reason,
        "suggestion": "改为三十日。",
        "citations": list(cites),
        "needs_human_review": human,
    }


CITES = {
    "C1": {"kind": "doc", "doc_id": "law-civil-code", "articles": ["第六百二十一条"], "text": "x"},
    "C2": {"kind": "doc", "doc_id": "law-civil-code", "articles": ["第六百二十条"], "text": "y"},
}


def test_verify_policy_flags_instead_of_deleting():
    from contract_review.review_verify import verify_review

    r = {
        "kind": "review",
        "citations": CITES,
        "findings": [
            _f("检验期过短，依据民法典第六百二十一条。"),
            _f("没有依据的说法。"),
            _f("一半有依据。"),
            _f("依据民法典第六百二十二条。"),
            _f("摘录对不上。", quote_found=False),
        ],
    }
    labels = [
        ("supported", ["C1"], ""),
        ("unsupported", [], ""),
        ("partial", ["C2"], "改写后的理由。"),
        ("supported", ["C1"], ""),
        ("supported", ["C1"], ""),
    ]
    v = verify_review(r, FakeVerifier(labels), "huaxin")
    f = v.findings
    assert len(f) == 5  # 一条都不删
    assert f[0]["needs_human_review"] is False and "verify_flags" not in f[0]
    assert f[1]["needs_human_review"] is True and f[1]["verify_flags"] == ["unsupported"]
    assert f[2]["reason"] == "改写后的理由。" and f[2]["revised_from"] == "一半有依据。"
    assert f[2]["citations"] == ["C2"] and f[2]["needs_human_review"] is False
    assert f[3]["verify_flags"] == ["unretrieved:law-civil-code第六百二十二条"]
    assert f[4]["verify_flags"] == ["quote_mismatch"] and f[4]["needs_human_review"] is True
    assert v.flagged == 3 and v.revised == 1 and v.repaired == 1 and v.cost_usd == 0.001


def test_verify_skips_non_review_and_survives_verifier_error():
    from contract_review.review_verify import verify_review

    class Boom:
        def label(self, *a):
            raise RuntimeError("down")

    assert (
        verify_review(
            {"kind": "clarify", "findings": [], "citations": {}}, Boom(), "huaxin"
        ).findings
        == []
    )
    v = verify_review(
        {"kind": "review", "citations": CITES, "findings": [_f("x")]}, Boom(), "huaxin"
    )
    assert v.verifier_error and v.findings[0]["needs_human_review"] is True
    assert v.findings[0]["verify_flags"] == ["unverified"]


def test_law_refs_catches_other_named_laws():
    # 白名单外的法名也要抽出来，凭记忆引用别的法最该被拦
    assert law_refs("依据《个人信息保护法》第二十一条，受托人应当……") == {
        ("其他:个人信息保护法", "第二十一条")
    }
    assert law_refs("《数据安全法》第27条") == {("其他:数据安全法", "第二十七条")}
    assert law_refs("《民法典》合同编第五百八十五条") == {("law-civil-code", "第五百八十五条")}


def test_law_refs_does_not_absorb_contract_clause():
    assert law_refs("《民法典》第五百八十五条及第八条约定的违约金") == {
        ("law-civil-code", "第五百八十五条")
    }


def test_other_law_is_always_unretrieved():
    f = {"reason": "违反《个人信息保护法》第二十一条。", "suggestion": ""}
    assert unretrieved_refs(f, CITES, "huaxin") == [("其他:个人信息保护法", "第二十一条")]


def test_verifier_support_ids_are_normalized():
    # 校验器返回 "[C1]" 也要认
    from contract_review.review_verify import verify_review

    r = {"kind": "review", "citations": CITES, "findings": [_f("依据民法典第六百二十一条。")]}
    v = verify_review(r, FakeVerifier([("supported", ["[C1]"], "")]), "huaxin")
    assert v.findings[0]["needs_human_review"] is False and "verify_flags" not in v.findings[0]
