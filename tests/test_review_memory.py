import json
from types import SimpleNamespace as NS

from contract_review.review_memory import ReviewMemory


def finding(clause, rtype="inspection", reason="检验期过短", suggestion="改为三十日"):
    return {
        "clause": clause,
        "risk_type": rtype,
        "level": "高",
        "reason": reason,
        "suggestion": suggestion,
        "citations": ["C1"],
    }


REVIEW = {
    "kind": "review",
    "our_side": "甲",
    "summary": "两处风险",
    "findings": [finding("第四条"), finding("第六条", "deposit")],
}


def followup(clause, answer="建议改为……"):
    return {"kind": "followup", "answer": answer, "answer_clause": clause}


class FakeSummarizer:
    model = "claude-haiku-5-5"

    def __init__(self, payload=None, fail=False):
        self.payload, self.fail, self.calls = payload, fail, []

    def create(self, **kw):
        self.calls.append(kw)
        if self.fail:
            raise RuntimeError("down")
        text = json.dumps(self.payload, ensure_ascii=False)
        return NS(
            content=[NS(type="text", text=text)], usage=NS(input_tokens=100, output_tokens=50)
        )


def build(mem):
    mem.add_review(REVIEW)
    mem.add_followup("你列的第1条怎么改？", followup("第四条"))
    mem.add_followup(
        "第二条意见对方已同意改，不用管了。", followup("第六条", "好的，第六条不再列入。")
    )
    mem.add_review(
        REVIEW | {"our_side": "乙", "findings": [finding("第九条", "liability")]}, "换成乙方视角"
    )
    return mem


def test_full_keeps_every_turn_with_turn_numbers():
    h = build(ReviewMemory(mode="full")).render()
    assert h.startswith("<对话历史>") and h.endswith("</对话历史>")
    assert "【第0轮·首次审查】" in h and "1. 第四条" in h and "2. 第六条" in h
    assert "用户：第二条意见对方已同意改" in h and "【第3轮】用户：换成乙方视角" in h


def test_window_drops_early_turns():
    h = build(ReviewMemory(mode="window", keep_last=2)).render()
    assert "【第0轮" not in h and "第1轮" not in h and "【第3轮】" in h


def test_compress_summarizes_old_turns_and_keeps_recent():
    payload = {
        "our_side": "甲",
        "findings": [
            {"turn": 0, "no": 1, "clause": "第四条", "risk_type": "inspection", "status": "已报"},
            {
                "turn": 0,
                "no": 2,
                "clause": "第六条",
                "risk_type": "deposit",
                "status": "用户已确认不再处理",
            },
        ],
        "decisions": ["第六条：对方已同意修改，不再列入"],
        "cited": ["民法典第六百二十一条"],
    }
    s = FakeSummarizer(payload)
    mem = build(ReviewMemory(mode="compress", keep_last=2, budget_chars=200, summarizer=s))
    h = mem.render()
    assert mem.compressions == 1 and len(s.calls) == 1
    assert "较早对话摘要" in h and "第0轮第2条：第六条" in h and "用户已确认不再处理" in h
    assert "【第0轮" not in h and "【第3轮】" in h  # 首轮原文被压缩，最近两轮保留原文
    assert mem.cost_usd > 0


def test_summary_missing_finding_is_backfilled_by_rule():
    payload = {
        "our_side": "甲",
        "findings": [
            {"turn": 0, "no": 1, "clause": "第四条", "risk_type": "inspection", "status": "已报"}
        ],
        "decisions": [],
        "cited": [],
    }
    mem = build(
        ReviewMemory(
            mode="compress", keep_last=2, budget_chars=200, summarizer=FakeSummarizer(payload)
        )
    )
    h = mem.render()
    assert "第0轮第2条：第六条" in h and "补录" in h and mem.backfilled == 1


def test_summarizer_failure_falls_back_to_rule_ledger():
    mem = build(
        ReviewMemory(
            mode="compress", keep_last=2, budget_chars=200, summarizer=FakeSummarizer(fail=True)
        )
    )
    h = mem.render()
    assert mem.compress_errors == 1 and "第0轮第1条：第四条" in h and "第0轮第2条：第六条" in h


def test_no_compression_under_budget():
    s = FakeSummarizer({})
    mem = ReviewMemory(mode="compress", budget_chars=100000, summarizer=s)
    mem.add_review(REVIEW)
    assert "【第0轮·首次审查】" in mem.render() and not s.calls


def test_truncated_summary_falls_back_to_rule_ledger():
    class Truncated(FakeSummarizer):
        def create(self, **kw):
            r = super().create(**kw)
            r.stop_reason = "max_tokens"
            return r

    mem = build(
        ReviewMemory(
            mode="compress", keep_last=2, budget_chars=200, summarizer=Truncated({"our_side": "甲"})
        )
    )
    h = mem.render()
    assert mem.compress_errors == 1 and "截断" in mem.last_error and "第0轮第2条：第六条" in h


def test_backfill_not_counted_when_fallback_after_success():
    class Flaky(FakeSummarizer):
        n = 0

        def create(self, **kw):
            self.n += 1
            if self.n == 2:
                raise RuntimeError("down")
            return super().create(**kw)

    payload = {"our_side": "甲", "findings": [], "decisions": [], "cited": []}
    mem = build(
        ReviewMemory(mode="compress", keep_last=1, budget_chars=50, summarizer=Flaky(payload))
    )
    mem.render()  # 第一次压缩成功但台账为空 → 补录 2 条（第0轮两条意见）
    mem.add_followup("再问", followup("第四条"))
    mem.render()  # 第二次压缩失败 → 规则兜底，不再计补录
    assert mem.compressions == 1 and mem.compress_errors == 1 and mem.backfilled == 2
