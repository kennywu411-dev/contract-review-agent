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


def test_backfill_keeps_previous_status():
    # 审查 I3：第二次压缩时模型漏掉了上次摘要里"已谈妥"的那条，补录要沿用旧状态，不能退化成"已报"
    first = {
        "our_side": "甲",
        "findings": [
            {"turn": 0, "no": 1, "clause": "第四条", "risk_type": "inspection", "status": "已报"},
            {
                "turn": 0,
                "no": 2,
                "clause": "第六条",
                "risk_type": "deposit",
                "status": "已谈妥不再处理",
            },
        ],
        "decisions": ["第六条已谈妥"],
        "cited": [],
    }
    second = {"our_side": "甲", "findings": [first["findings"][0]], "decisions": [], "cited": []}

    class Seq(FakeSummarizer):
        n = 0

        def create(self, **kw):
            self.payload = first if self.n == 0 else second
            self.n += 1
            return super().create(**kw)

    mem = build(ReviewMemory(mode="compress", keep_last=1, budget_chars=50, summarizer=Seq()))
    mem.render()
    mem.add_followup("q1", followup("第四条"))
    mem.add_followup("q2", followup("第四条"))
    h = mem.render()
    assert (
        "第0轮第2条：第六条" in h
        and "已谈妥不再处理" in h
        and "补录" not in h.split("第六条")[1].split("\n")[0]
    )


def test_compression_is_batched_and_backfill_counted_once():
    # 审查 M1：超预算后不能每轮都调摘要模型；同一条意见的补录只计一次
    s = FakeSummarizer({"our_side": "甲", "findings": [], "decisions": [], "cited": []})
    mem = ReviewMemory(mode="compress", keep_last=2, budget_chars=50, summarizer=s)
    mem.add_review(REVIEW)
    for i in range(6):
        mem.add_followup(f"q{i}", followup("第四条"))
        mem.render()
    assert len(s.calls) <= 3 and mem.backfilled == 2


def test_compress_without_summarizer_uses_rule_ledger_silently():
    # 审查 M2：不给摘要模型就是"纯规则台账"，不算错误；keep_last=0 也不能崩
    mem = build(ReviewMemory(mode="compress", keep_last=0, budget_chars=50, summarizer=None))
    h = mem.render()
    assert mem.compress_errors == 0 and "第0轮第1条：第四条" in h


def test_summary_side_uses_latest_turn():
    # 审查 M4：换回立场发生在保留原文的轮次里时，摘要头部的"我方"也要是最新的
    payload = {"our_side": "乙", "findings": [], "decisions": [], "cited": []}
    mem = ReviewMemory(
        mode="compress", keep_last=1, budget_chars=50, summarizer=FakeSummarizer(payload)
    )
    mem.add_review(REVIEW | {"our_side": "乙"})
    mem.add_review(REVIEW | {"our_side": "甲"}, "换回甲方")
    h = mem.render()
    assert "我方：甲方" in h.split("意见台账")[0]


def test_first_review_marker_after_clarification():
    # 审查 M3：首轮是反问时，"首次审查"标在第一次给出意见的那一轮
    mem = ReviewMemory(mode="full")
    mem.add_review({"kind": "clarify", "clarification": "我方是甲方还是乙方？"})
    mem.add_followup("我方是甲方", REVIEW)
    h = mem.render()
    assert "【第0轮】助手反问" in h and "【第1轮·首次审查】用户：我方是甲方" in h
