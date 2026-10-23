import json
from types import SimpleNamespace as NS

from docpilot.agent import run_agent
from docpilot.trace import load_trace, save_trace
from tests.test_tools import FakeSQL, box, ch


def usage(i=100, o=20, r=0, w=0):
    return NS(
        input_tokens=i, output_tokens=o, cache_read_input_tokens=r, cache_creation_input_tokens=w
    )


def tool(name, inp, id_="t"):
    return NS(type="tool_use", name=name, input=inp, id=id_)


def text(t):
    return NS(type="text", text=t)


class FakeLLM:
    model = "claude-haiku-5-5"

    def __init__(self, replies):
        self.replies, self.requests = list(replies), []

    def create(self, **kw):
        self.requests.append(kw)
        content = self.replies.pop(0) if self.replies else [tool("search_docs", {"query": "再查"})]
        stop = "tool_use" if any(b.type == "tool_use" for b in content) else "end_turn"
        return NS(content=content, stop_reason=stop, usage=usage())


ANSWER = {
    "refuse": False,
    "refuse_reason": "",
    "sentences": [{"text": "缓考须考前申请。", "citations": ["C1"]}],
}


def test_routes_through_tools_then_answers():
    llm = FakeLLM(
        [[tool("search_docs", {"query": "缓考"}, "a")], [tool("final_answer", ANSWER, "b")]]
    )
    r = run_agent("缓考怎么办", "bnu", llm, box([ch("d", "bnu", "考前提交缓考申请")]))
    assert r.kind == "answer" and r.sentences[0]["citations"] == ["C1"] and r.steps == 2
    second = llm.requests[1]["messages"]
    assert (
        second[-1]["content"][0]["type"] == "tool_result"
        and "[C1]" in second[-1]["content"][0]["content"]
    )
    assert r.cost_usd > 0 and r.invalid_citations == []


def test_system_and_tools_are_cached():
    llm = FakeLLM([[tool("final_answer", ANSWER | {"sentences": []})]])
    run_agent("q", "bnu", llm, box())
    req = llm.requests[0]
    assert req["system"][0]["cache_control"] == {"type": "ephemeral"}
    assert req["cache_control"] == {"type": "ephemeral"}


def test_max_steps_enforced():
    llm = FakeLLM([])
    r = run_agent("q", "bnu", llm, box([ch("d", "bnu", "x")]), max_steps=3)
    assert r.kind == "fallback" and r.steps == 3 and r.trace["end"] == "max_steps"


def test_invalid_citation_recorded():
    bad = ANSWER | {"sentences": [{"text": "x", "citations": ["C9", "T1"]}]}
    llm = FakeLLM([[tool("search_docs", {"query": "q"})], [tool("final_answer", bad)]])
    r = run_agent("q", "bnu", llm, box([ch("d", "bnu", "x")]))
    assert r.kind == "answer" and r.invalid_citations == ["C9", "T1"]


def test_multiple_searches_recorded():
    llm = FakeLLM(
        [
            [
                tool("search_docs", {"query": "国家助学金标准"}, "a"),
                tool("search_docs", {"query": "北师大助学金分档"}, "b"),
            ],
            [tool("final_answer", ANSWER)],
        ]
    )
    r = run_agent("q", "bnu", llm, box([ch("d", "bnu", "x")]))
    calls = [
        c["input"]["query"]
        for s in r.trace["steps"]
        for c in s["tool_calls"]
        if c["name"] == "search_docs"
    ]
    assert calls == ["国家助学金标准", "北师大助学金分档"]
    assert len(llm.requests[1]["messages"][-1]["content"]) == 2


def test_text_only_reply_becomes_fallback():
    r = run_agent("q", "bnu", FakeLLM([[text("我直接回答了")]]), box())
    assert r.kind == "fallback" and r.trace["end"] == "no_terminal_tool"


def test_clarify_and_refuse_kinds():
    ask = {"question": "你是哪个专业？", "missing": ["专业"]}
    assert (
        run_agent("q", "bnu", FakeLLM([[tool("ask_clarification", ask)]]), box()).kind == "clarify"
    )
    ref = {"refuse": True, "refuse_reason": "文件未收录", "sentences": []}
    assert run_agent("q", "bnu", FakeLLM([[tool("final_answer", ref)]]), box()).kind == "refuse"


def test_tool_error_continues_conversation():
    sql = FakeSQL({"error": "sql_error", "message": "boom"})
    llm = FakeLLM(
        [[tool("run_sql", {"sql": "select x"})], [tool("final_answer", ANSWER | {"sentences": []})]]
    )
    r = run_agent("q", "bnu", llm, box(sql=sql))
    res = llm.requests[1]["messages"][-1]["content"][0]
    assert res["is_error"] is True and r.kind == "answer"


def test_trace_roundtrip_json(tmp_path):
    llm = FakeLLM([[tool("search_docs", {"query": "q"})], [tool("final_answer", ANSWER)]])
    r = run_agent("缓考", "bnu", llm, box([ch("d", "bnu", "x")]))
    p = save_trace(r, tmp_path)
    t = load_trace(p)
    assert t["question"] == "缓考" and t["result"]["kind"] == "answer"
    assert t["steps"][0]["tool_calls"][0]["name"] == "search_docs"
    assert "C1" in t["citations"]
    json.dumps(t, ensure_ascii=False)
