import json

from docpilot.chunking import Chunk
from docpilot.retrieve import Hit, SearchResult
from docpilot.tools import TOOLS, ToolBox


def ch(doc, school, text, pages=(5,), articles=()):
    return Chunk(
        f"{doc}:fixed:0",
        doc,
        school,
        "某规定",
        ["某规定", "第一章"],
        text,
        list(pages),
        [0],
        list(articles),
    )


class FakeRetriever:
    def __init__(self, hits, mismatch=None):
        self.hits, self.mismatch, self.calls = hits, mismatch, []

    def search(self, query, school, k=5, mode="hybrid_rerank"):
        self.calls.append((query, school))
        return SearchResult([Hit(c, 0.9) for c in self.hits][:k], self.mismatch, {"total": 1.0})


class FakeSQL:
    name = "fake"

    def __init__(self, result):
        self.result, self.calls = result, []

    def run(self, sql):
        self.calls.append(sql)
        return self.result


def box(hits=(), sql=None, mismatch=None, school="bnu"):
    return ToolBox(
        school, FakeRetriever(list(hits), mismatch), sql or FakeSQL({"columns": [], "rows": []})
    )


def test_tool_schemas_are_strict():
    for t in TOOLS:
        assert t["strict"] is True and t["input_schema"]["additionalProperties"] is False
        assert set(t["input_schema"]["required"]) == set(t["input_schema"]["properties"])


def test_search_scope_not_model_controlled():
    b = box([ch("d", "bnu", "正文")])
    out = b.execute("search_docs", {"query": "转专业 upc"})
    assert b.retriever.calls == [("转专业 upc", "bnu")] and "[C1]" in out.content
    assert "school" not in TOOLS[0]["input_schema"]["properties"]


def test_citation_ids_increment():
    b = box([ch("a", "bnu", "甲"), ch("b", "national", "乙")])
    b.execute("search_docs", {"query": "q1"})
    out = b.execute("search_docs", {"query": "q2"})
    assert "[C3]" in out.content and set(b.citations) == {"C1", "C2", "C3", "C4"}
    assert b.citations["C2"]["doc_id"] == "b" and b.citations["C1"]["pages"] == [5]


def test_sql_rows_filtered_to_school():
    sql = FakeSQL(
        {"columns": ["school", "name"], "rows": [["bnu", "x"], ["upc", "y"], ["national", "z"]]}
    )
    b = box(sql=sql)
    out = b.execute("run_sql", {"sql": "select school, name from scholarships"})
    data = json.loads(out.content.split("\n", 1)[1])
    assert data["rows"] == [["bnu", "x"], ["national", "z"]] and "[T1]" in out.content
    assert b.citations["T1"]["sql"].startswith("select")


def test_tool_error_returned_to_model():
    b = box(sql=FakeSQL({"error": "sql_error", "message": "no such column"}))
    out = b.execute("run_sql", {"sql": "select nope from t"})
    assert out.is_error and "no such column" in out.content and "T1" not in b.citations
    assert box().execute("no_such_tool", {}).is_error


def test_school_mismatch_surfaced():
    out = box([ch("d", "bnu", "正文")], mismatch="zzu").execute("search_docs", {"query": "郑大"})
    assert "郑州大学" in out.content


def test_empty_search_says_so():
    out = box([]).execute("search_docs", {"query": "食堂几点关门"})
    assert "没有检索到" in out.content and not out.is_error
