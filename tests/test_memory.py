import json
from types import SimpleNamespace as NS

from contract_review.memory import ConversationMemory, Turn, extract_slots


def test_extract_slots():
    s = extract_slots("我是计算机专业大二的本科生，想转专业")
    assert s == {"degree": "本科", "grade": "大二", "major": "计算机"}
    assert extract_slots("我是研究生，能拿国奖吗")["degree"] == "研究生"
    assert extract_slots("研究生能拿国奖吗") == {}  # 问到的对象不是学生身份
    assert extract_slots("我是2023级的")["grade"] == "2023级"
    assert extract_slots("缓考怎么办") == {}


def turn(q, a="答案", sources=("《学籍规定》第12页",)):
    return Turn(question=q, answer=a, sources=list(sources), kind="answer")


def test_slots_accumulate_and_latest_wins():
    m = ConversationMemory()
    m.add(turn("我是大一的，能转专业吗"))
    m.add(turn("我是计算机专业的，那大二呢？"))
    assert m.slots == {"grade": "大一", "major": "计算机"}  # "那大二呢"是在问大二，不是身份


def test_render_window_keeps_recent_turns_verbatim():
    m = ConversationMemory(mode="full")
    for i in range(4):
        m.add(turn(f"问题{i}", f"答案{i}"))
    r = m.render()
    assert all(f"问题{i}" in r for i in range(4)) and "《学籍规定》第12页" in r


def test_none_mode_renders_nothing():
    m = ConversationMemory(mode="none")
    m.add(turn("北师大国奖多少钱"))
    assert m.render() == ""


class FakeSummarizer:
    model = "claude-haiku-5-5"

    def __init__(self):
        self.calls = []

    def create(self, **kw):
        self.calls.append(kw)
        u = NS(
            input_tokens=200,
            output_tokens=40,
            cache_read_input_tokens=0,
            cache_creation_input_tokens=0,
        )
        return NS(
            content=[
                NS(
                    type="text",
                    text=json.dumps({"summary": "学生问过国奖金额（1万元）和转专业条件。"}),
                )
            ],
            usage=u,
        )


def test_compression_triggers_over_budget_and_keeps_last_turn():
    s = FakeSummarizer()
    m = ConversationMemory(mode="compress", budget_tokens=60, keep_last=1, summarizer=s)
    m.add(turn("北师大国奖一年多少钱", "国奖每年1万元" * 5))
    m.add(turn("我是大二计算机专业的，转专业有什么条件", "需在春季学期申请" * 5))
    r = m.render()
    assert s.calls and "学生问过国奖金额" in r
    assert "转专业有什么条件" in r  # 最近一轮原样保留
    assert "大二" in r and "计算机" in r  # 槽位不随压缩丢失
    assert "国奖每年1万元国奖每年1万元" not in r  # 旧轮次原文被摘要替代
    assert m.compressions == 1 and m.cost_usd > 0


def test_under_budget_no_compression():
    s = FakeSummarizer()
    m = ConversationMemory(mode="compress", budget_tokens=10_000, summarizer=s)
    m.add(turn("缓考怎么申请"))
    m.add(turn("要什么材料"))
    m.render()
    assert s.calls == [] and m.compressions == 0


def test_summarizer_error_falls_back_to_truncation():
    class Boom(FakeSummarizer):
        def create(self, **kw):
            raise TimeoutError("x")

    m = ConversationMemory(mode="compress", budget_tokens=30, keep_last=1, summarizer=Boom())
    m.add(turn("问题一", "答" * 50))
    m.add(turn("问题二", "答" * 50))
    r = m.render()
    assert "问题二" in r and m.compress_errors == 1


def test_slot_false_positives():
    assert extract_slots("学校的专业设置是怎样的") == {}
    assert extract_slots("读什么专业好") == {}
    assert extract_slots("违约金放大三倍合理吗") == {}
    assert extract_slots("我大三了")["grade"] == "大三"
