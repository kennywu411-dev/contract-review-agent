import json

import pytest
from fastapi.testclient import TestClient

from docpilot.agent import AgentResult
from docpilot.api import create_app
from docpilot.verify import VerifiedAnswer


class FakeEngine:
    def __init__(self):
        self.calls = []

    def ask(self, question, school, on_step):
        self.calls.append((question, school))
        on_step({"step": 1, "tool_calls": [{"name": "search_docs", "input": {"query": question}}]})
        raw = AgentResult(
            question,
            school,
            "answer",
            sentences=[{"kind": "fact", "text": "考前申请", "citations": ["C1"]}],
            citations={
                "C1": {
                    "kind": "doc",
                    "chunk_id": "bnu-handbook-2025:fixed:3",
                    "doc_id": "bnu-handbook-2025",
                    "pages": [166],
                    "articles": [],
                    "text": "x",
                    "title": "t",
                }
            },
            trace={"trace_id": "abc", "steps": []},
        )
        ver = VerifiedAnswer("answer", kept=raw.sentences)
        return raw, ver


@pytest.fixture
def client():
    return TestClient(create_app(engine=FakeEngine()))


def events(resp):
    out = []
    for line in resp.text.splitlines():
        if line.startswith("event:"):
            name = line.split(":", 1)[1].strip()
        elif line.startswith("data:"):
            out.append((name, json.loads(line.split(":", 1)[1])))
    return out


def test_schools(client):
    data = client.get("/api/schools").json()
    assert {"id": "bnu", "name": "北京师范大学"} in data and all(
        s["id"] != "national" for s in data
    )


def test_ask_streams_steps_and_answer(client):
    r = client.post("/api/ask", json={"question": "缓考怎么办", "school": "bnu"})
    names = [n for n, _ in events(r)]
    assert names[0] == "step" and "answer" in names and names[-1] == "done"
    verified = dict(events(r))["verified"]
    assert verified["kind"] == "answer" and verified["kept"][0]["citations"] == ["C1"]


@pytest.mark.parametrize(
    "body",
    [
        {"question": "", "school": "bnu"},
        {"question": "x" * 2001, "school": "bnu"},
        {"question": "q", "school": "pku"},
    ],
)
def test_ask_validation(client, body):
    assert client.post("/api/ask", json=body).status_code == 422


@pytest.mark.parametrize(
    "doc", ["../../etc/passwd", "%2e%2e%2fsecret", "not-a-doc", "national-xwf"]
)
def test_pdf_path_traversal_rejected(client, doc):
    assert client.get(f"/api/docs/{doc}/pdf").status_code == 404


def test_pdf_served_for_known_doc(client):
    r = client.get("/api/docs/cumt-xuewei-2025/pdf")
    assert r.status_code == 200 and r.content[:4] == b"%PDF"


def test_highlight_returns_page_boxes(client):
    data = client.get("/api/highlight", params={"chunk_id": "cumt-xuewei-2025:fixed:0"}).json()
    assert data["doc_id"] == "cumt-xuewei-2025" and data["boxes"]
    b = data["boxes"][0]
    assert b["page"] >= 1 and len(b["bbox"]) == 4 and b["page_width"] > 0


def test_html_doc_blocks(client):
    data = client.get("/api/docs/national-xwf/html").json()
    assert data["title"] and any(b["text"].startswith("第一条") for b in data["blocks"])


def test_runs_listing_mixed_types(client):
    runs = client.get("/api/runs").json()
    kinds = {r["kind"] for r in runs}
    assert kinds <= {"retrieval", "agent", "d4"} and runs


def test_run_detail_includes_question_text(client):
    runs = [r for r in client.get("/api/runs").json() if r["kind"] == "agent"]
    if not runs:
        pytest.skip("没有 agent 运行")
    d = client.get(f"/api/runs/{runs[0]['run']}").json()
    assert d["rows"] and all("question" in r for r in d["rows"])


def test_run_stages_breakdown(client):
    runs = [r for r in client.get("/api/runs").json() if r["kind"] in ("agent", "d4")]
    if not runs:
        pytest.skip("没有 agent 运行")
    s = client.get(f"/api/runs/{runs[0]['run']}/stages").json()
    assert s["rows"] and {"llm_ms", "search_ms", "sql_ms", "verify_ms", "cost_usd"} <= set(
        s["rows"][0]
    )


def test_bad_run_name_404(client):
    assert client.get("/api/runs/..%2f..%2fetc").status_code == 404
    assert client.get("/api/runs/nope/traces/x").status_code == 404


def test_cors_allows_dev_frontend(client):
    r = client.options(
        "/api/schools",
        headers={"Origin": "http://localhost:3000", "Access-Control-Request-Method": "GET"},
    )
    assert r.headers.get("access-control-allow-origin") == "http://localhost:3000"
