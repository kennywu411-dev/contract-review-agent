"""意见反馈：试用时用户对每条意见打标，按"最后一次打标为准"汇总成采纳率等指标。"""

from fastapi.testclient import TestClient

from contract_review.api import create_app
from contract_review.feedback import FeedbackStore, summarize
from tests.test_api import CONTRACT, FakeEngine, start_session


def entry(label, index=0, level="高", review_id="rev1", risk_type="inspection"):
    return {
        "review_id": review_id,
        "finding_index": index,
        "label": label,
        "finding": {"clause": "第四条", "risk_type": risk_type, "level": level},
    }


def test_summary_uses_latest_label_per_finding(tmp_path):
    store = FeedbackStore(tmp_path / "fb.jsonl")
    store.add("s1", entry("not_risk", 0))
    store.add("s1", entry("useful", 0))  # 改了主意，以最后一次为准
    store.add("s1", entry("not_risk", 1))
    store.add("s1", entry("unclear", 2, level="中"))
    store.add("s1", entry("useful", 3, level="低"))  # 低等级不进主指标
    s = summarize(store.load())
    assert s["rated"] == 3
    assert s["labels"] == {"useful": 1, "not_useful": 0, "not_risk": 1, "unclear": 1}
    assert s["adoption_rate"] == round(1 / 3, 3)
    assert s["false_positive_rate"] == round(1 / 3, 3)
    assert s["by_risk_type"]["inspection"]["rated"] == 3


def test_empty_summary(tmp_path):
    s = summarize(FeedbackStore(tmp_path / "none.jsonl").load())
    assert s["rated"] == 0 and s["adoption_rate"] is None


def make_client(tmp_path):
    return TestClient(create_app(engine=FakeEngine(), feedback_path=tmp_path / "fb.jsonl"))


def test_feedback_endpoint_records_and_summarizes(tmp_path):
    c = make_client(tmp_path)
    sid = start_session(c)
    body = {"session_id": sid, **entry("useful")}
    assert c.post("/api/feedback", json=body).status_code == 200
    s = c.get("/api/feedback/summary").json()
    assert s["rated"] == 1 and s["adoption_rate"] == 1.0
    line = (tmp_path / "fb.jsonl").read_text(encoding="utf-8")
    assert sid in line and CONTRACT not in line  # 只记标签和意见摘要，不记合同原文


def test_feedback_rejects_unknown_session_and_label(tmp_path):
    c = make_client(tmp_path)
    assert (
        c.post("/api/feedback", json={"session_id": "nope", **entry("useful")}).status_code == 404
    )
    sid = start_session(c)
    assert c.post("/api/feedback", json={"session_id": sid, **entry("great")}).status_code == 422
