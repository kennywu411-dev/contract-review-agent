import json

import pytest
from fastapi.testclient import TestClient

from contract_review.agent import AgentResult
from contract_review.api import create_app
from contract_review.verify import VerifiedAnswer


class FakeEngine:
    def __init__(self):
        self.calls = []

    def ask(self, question, tenant, on_step, history=""):
        self.calls.append((question, tenant, history))
        on_step({"step": 1, "tool_calls": [{"name": "search_docs", "input": {"query": question}}]})
        raw = AgentResult(
            question,
            tenant,
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

    def review(self, contract, tenant, on_step, history="", question=""):
        from contract_review.review_agent import ReviewResult
        from contract_review.review_verify import VerifiedReview

        self.calls.append(("review", contract, tenant, history, question))
        on_step({"step": 1, "tool_calls": [{"name": "search_docs", "input": {"query": "检验期"}}]})
        finding = {
            "clause": "第四条",
            "quote": "视为合格",
            "risk_type": "inspection",
            "level": "高",
            "reason": "检验期过短",
            "suggestion": "改为三十日",
            "citations": ["C1"],
            "needs_human_review": False,
            "quote_found": True,
        }
        if question:
            r = ReviewResult(tenant, "followup", answer="建议改为……", answer_clause="第四条")
        else:
            r = ReviewResult(tenant, "review", our_side="甲", findings=[finding])
        r.trace = {"trace_id": "rev1", "steps": []}
        return r, VerifiedReview(findings=r.findings)


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
    data = client.get("/api/tenants").json()
    assert {"id": "huaxin", "name": "华信制造有限公司（构造）"} in data and all(
        s["id"] != "common" for s in data
    )


def test_ask_streams_steps_and_answer(client):
    r = client.post("/api/ask", json={"question": "缓考怎么办", "tenant": "huaxin"})
    names = [n for n, _ in events(r)]
    assert names[0] == "step" and "answer" in names and names[-1] == "done"
    verified = dict(events(r))["verified"]
    assert verified["kind"] == "answer" and verified["kept"][0]["citations"] == ["C1"]


@pytest.mark.parametrize(
    "body",
    [
        {"question": "", "tenant": "huaxin"},
        {"question": "x" * 2001, "tenant": "huaxin"},
        {"question": "q", "tenant": "pku"},
    ],
)
def test_ask_validation(client, body):
    assert client.post("/api/ask", json=body).status_code == 422


@pytest.mark.parametrize("doc", ["../../etc/passwd", "%2e%2e%2fsecret", "not-a-doc", "common-xwf"])
def test_pdf_path_traversal_rejected(client, doc):
    assert client.get(f"/api/docs/{doc}/pdf").status_code == 404


def test_pdf_served_for_known_doc(client):
    r = client.get("/api/docs/law-civil-procedure/pdf")
    assert r.status_code == 200 and r.content[:4] == b"%PDF"


def test_highlight_returns_page_boxes(client):
    data = client.get("/api/highlight", params={"chunk_id": "law-civil-procedure:fixed:0"}).json()
    assert data["doc_id"] == "law-civil-procedure" and data["boxes"]
    b = data["boxes"][0]
    assert b["page"] >= 1 and len(b["bbox"]) == 4 and b["page_width"] > 0


def test_html_doc_blocks(client):
    data = client.get("/api/docs/law-civil-code/html").json()
    assert data["title"] and any(b["text"].startswith("第一条") for b in data["blocks"])


def test_runs_listing_mixed_types(client):
    runs = client.get("/api/runs").json()
    kinds = {r["kind"] for r in runs}
    assert (
        kinds
        <= {
            "retrieval",
            "agent",
            "d4",
            "dialog",
            "review",
            "review_d4",
            "review_dialog",
            "contract_retrieval",
        }
        and runs
    )


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
        "/api/tenants",
        headers={"Origin": "http://localhost:3000", "Access-Control-Request-Method": "GET"},
    )
    assert r.headers.get("access-control-allow-origin") == "http://localhost:3000"


def test_followup_in_same_session_gets_history(client):
    eng = client.app.state.engine
    client.post(
        "/api/ask", json={"question": "北师大国奖多少钱", "tenant": "huaxin", "session_id": "s1"}
    )
    client.post("/api/ask", json={"question": "那研究生呢", "tenant": "huaxin", "session_id": "s1"})
    client.post("/api/ask", json={"question": "缓考怎么办", "tenant": "huaxin", "session_id": "s2"})
    first, second, other = eng.calls
    assert first[2] == "" and "北师大国奖多少钱" in second[2] and other[2] == ""


def test_switching_school_resets_session(client):
    eng = client.app.state.engine
    client.post("/api/ask", json={"question": "国奖多少钱", "tenant": "huaxin", "session_id": "s3"})
    client.post("/api/ask", json={"question": "那研究生呢", "tenant": "yunqi", "session_id": "s3"})
    assert eng.calls[-1][2] == ""


def test_review_run_kind():
    from contract_review.api import _run_kind

    assert _run_kind({"kind": "review", "detection": {}}) == "review"


CONTRACT = (
    "工业品买卖合同\n甲方（买受人）：华信制造有限公司\n第四条 检验与验收\n乙方交付即视为合格。"
)


def test_review_streams_steps_and_findings(client):
    r = client.post("/api/review", json={"contract": CONTRACT, "tenant": "huaxin"})
    ev = events(r)
    names = [n for n, _ in ev]
    assert names[0] == "step" and "review" in names and "verified" in names and names[-1] == "done"
    assert dict(ev)["review"]["findings"][0]["clause"] == "第四条"


def test_review_followup_uses_session_history():
    eng = FakeEngine()
    c = TestClient(create_app(engine=eng))
    c.post("/api/review", json={"contract": CONTRACT, "tenant": "huaxin", "session_id": "s1"})
    r = c.post(
        "/api/review", json={"question": "第1条意见怎么改", "tenant": "huaxin", "session_id": "s1"}
    )
    assert dict(events(r))["review"]["kind"] == "followup"
    _, contract, _, history, question = eng.calls[-1]
    assert contract == CONTRACT and "<上一轮审查>" in history and "1. 第四条" in history
    assert question == "第1条意见怎么改"
    c.post("/api/review", json={"question": "再说说", "tenant": "huaxin", "session_id": "s1"})
    assert "用户：第1条意见怎么改" in eng.calls[-1][3]  # 上一轮追问也进历史


@pytest.mark.parametrize(
    "body,code",
    [
        ({"contract": "", "tenant": "huaxin"}, 422),
        ({"contract": "x" * 20001, "tenant": "huaxin"}, 422),
        ({"contract": CONTRACT, "tenant": "pku"}, 422),
        ({"question": "怎么改", "tenant": "huaxin"}, 400),  # 追问必须在已有会话里
        ({"question": "怎么改", "tenant": "huaxin", "session_id": "nope"}, 400),
    ],
)
def test_review_validation(client, body, code):
    assert client.post("/api/review", json=body).status_code == code


def test_samples_are_constructed_contracts(client):
    data = client.get("/api/samples").json()
    assert data and all(d["constructed"] for d in data)
    assert {"id", "title", "tenant", "text", "our_name"} <= set(data[0])


def test_contract_run_kinds_and_rows(client):
    from contract_review.api import _run_kind

    assert _run_kind({"kind": "contract_retrieval", "modes": {}}) == "contract_retrieval"
    runs = client.get("/api/runs").json()
    reviews = [r for r in runs if r["kind"] == "review"]
    if not reviews:
        pytest.skip("没有合同审查运行")
    assert all(r["n"] for r in reviews)
    d = client.get(f"/api/runs/{reviews[0]['run']}").json()
    assert d["rows"] and {"id", "title", "trace_id"} <= set(d["rows"][0])
