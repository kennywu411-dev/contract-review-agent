from contract_review.chunking import Chunk
from contract_review.eval_retrieval import is_hit, summarize
from contract_review.evalset import Question


def ch(doc, pages, text="正文"):
    return Chunk(f"{doc}:section:0", doc, "huaxin", "t", [], text, pages, [0])


def test_pdf_gold_matches_by_page():
    gold = [{"doc_id": "a", "page": 12, "clause": None}]
    assert is_hit(ch("a", [11, 12]), gold)
    assert not is_hit(ch("a", [13]), gold) and not is_hit(ch("b", [12]), gold)


def test_html_gold_matches_by_clause():
    gold = [{"doc_id": "h", "page": None, "clause": "第二十八条"}]
    assert is_hit(ch("h", [], "第二十八条　学生可以申请休学"), gold)
    assert not is_hit(ch("h", [], "第二十九条　复学"), gold)


def test_table_only_gold_entries_are_ignored():
    gold = [{"table": "scholarships", "where": "1"}, {"doc_id": "a", "page": 3, "clause": None}]
    assert is_hit(ch("a", [3]), gold)


def q(id_, type_, tenant="huaxin", source="doc", subtype=None):
    gold = [] if type_ == "refuse" else [{"doc_id": "a", "page": 1, "clause": None}]
    return Question(
        id_, tenant, "问", type_, subtype, source, "u" if source != "doc" else None, gold, "", ""
    )


def test_refuse_questions_excluded_from_recall():
    rows = [
        {"qid": "1", "config": "c", "rank": 1, "top_score": 0.9},
        {"qid": "2", "config": "c", "rank": None, "top_score": 0.1},
    ]
    s = summarize(rows, {"1": q("1", "answerable"), "2": q("2", "refuse")})
    assert s["c"]["overall"]["recall@5"] == 1.0 and s["c"]["overall"]["n"] == 1
    assert s["c"]["refuse_top_scores"] == [0.1]


def test_summary_groups():
    qs = {
        "1": q("1", "answerable", "huaxin", "public"),
        "2": q("2", "table", "yunqi", "doc"),
        "3": q("3", "answerable", "yunqi", "doc", "cross_school"),
    }
    rows = [
        {"qid": "1", "config": "c", "rank": 2, "top_score": 0.8},
        {"qid": "2", "config": "c", "rank": 7, "top_score": 0.5},
        {"qid": "3", "config": "c", "rank": None, "top_score": 0.2},
    ]
    s = summarize(rows, qs)["c"]
    assert s["overall"]["recall@1"] == 0 and round(s["overall"]["recall@5"], 3) == 0.333
    assert s["overall"]["recall@10"] == 2 / 3
    assert s["by_type"]["table"]["recall@10"] == 1.0
    assert s["by_school"]["huaxin"]["recall@5"] == 1.0
    assert s["by_source_type"]["public"]["n"] == 1
    assert s["by_subtype"]["cross_school"]["recall@10"] == 0.0
    assert round(s["overall"]["mrr"], 4) == round((1 / 2 + 1 / 7) / 3, 4)


def test_html_clause_inside_merged_section_chunk():
    gold = [{"doc_id": "h", "page": None, "clause": "第二十八条"}]
    assert is_hit(ch("h", [], "第二十七条　休学\n第二十八条　复学"), gold)
    assert not is_hit(ch("h", [], "第二十七条　依照第二十八条处理"), gold)


def test_clause_gold_matches_article_of_sub_items():
    c = ch("h", [], "2.二年级只能在学院内转专业。")
    c.articles = ["第十二条"]
    assert is_hit(c, [{"doc_id": "h", "page": None, "clause": "第十二条"}])
