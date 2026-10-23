from types import SimpleNamespace as NS

from contract_review.contract_evalset import load_contracts
from contract_review.eval_contract_retrieval import basis_hit, build_queries


def test_queries_use_clause_text_and_skip_empty_basis():
    qs = build_queries(load_contracts())
    assert qs and all(q["basis"] for q in qs)
    q = next(q for q in qs if q["contract_id"] == "goods-r1" and q["type"] == "inspection")
    assert "视为" in q["query"] and "第四条" not in q["query"]  # 只用条款正文，不带条号
    missing = [q for q in qs if q["clause"] is None]
    assert missing and all("保密" in q["query"] for q in missing)
    assert len({(q["contract_id"], q["clause"], q["type"]) for q in qs}) == len(qs)


def test_basis_hit_by_doc_and_article():
    basis = [["law-civil-code", "第六百二十一条"], ["huaxin-contract-policy", "第十五条"]]
    assert basis_hit(
        NS(doc_id="law-civil-code", articles=["第六百二十条", "第六百二十一条"]), basis
    )
    assert not basis_hit(NS(doc_id="law-civil-code", articles=["第十五条"]), basis)
    assert basis_hit(NS(doc_id="huaxin-contract-policy", articles=["第十五条"]), basis)
