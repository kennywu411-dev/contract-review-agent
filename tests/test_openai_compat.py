import json
from types import SimpleNamespace as NS

import httpx
import pytest

from docpilot.llm import cost_usd
from docpilot.openai_compat import OpenAICompatLLM, to_openai_messages, to_openai_tools
from docpilot.tools import TOOLS


def test_tools_converted_to_functions():
    fns = to_openai_tools(TOOLS)
    assert fns[0]["type"] == "function" and fns[0]["function"]["name"] == "search_docs"
    assert fns[0]["function"]["parameters"]["required"] == ["query"]


def test_messages_round_trip_tool_use_and_results():
    system = [{"type": "text", "text": "规则", "cache_control": {"type": "ephemeral"}}]
    msgs = [
        {"role": "user", "content": "缓考怎么办"},
        {
            "role": "assistant",
            "content": [
                NS(type="text", text="先检索"),
                NS(type="tool_use", id="call_1", name="search_docs", input={"query": "缓考"}),
            ],
        },
        {
            "role": "user",
            "content": [
                {
                    "type": "tool_result",
                    "tool_use_id": "call_1",
                    "content": "[C1] …",
                    "is_error": False,
                },
            ],
        },
    ]
    out = to_openai_messages(system, msgs)
    assert out[0] == {"role": "system", "content": "规则"}
    assert out[2]["tool_calls"][0]["function"] == {
        "name": "search_docs",
        "arguments": '{"query": "缓考"}',
    }
    assert out[3] == {"role": "tool", "tool_call_id": "call_1", "content": "[C1] …"}


def fake_transport(reply: dict, seen: list):
    def handler(request: httpx.Request):
        seen.append(json.loads(request.content))
        return httpx.Response(200, json=reply)

    return httpx.MockTransport(handler)


REPLY = {
    "model": "deepseek-flash",
    "choices": [
        {
            "finish_reason": "tool_calls",
            "message": {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {
                        "id": "c9",
                        "type": "function",
                        "function": {"name": "final_answer", "arguments": '{"refuse": true}'},
                    }
                ],
            },
        }
    ],
    "usage": {"prompt_tokens": 1000, "completion_tokens": 50, "prompt_cache_hit_tokens": 600},
}


def test_create_returns_anthropic_shaped_message():
    seen = []
    llm = OpenAICompatLLM("deepseek-flash", api_key="k", transport=fake_transport(REPLY, seen))
    r = llm.create(
        max_tokens=100,
        system=[{"type": "text", "text": "s"}],
        tools=TOOLS,
        messages=[{"role": "user", "content": "q"}],
        output_config={"effort": "low"},
        cache_control={"type": "ephemeral"},
    )
    assert r.stop_reason == "tool_use" and r.content[0].type == "tool_use"
    assert r.content[0].input == {"refuse": True} and r.content[0].name == "final_answer"
    assert r.usage.input_tokens == 400 and r.usage.cache_read_input_tokens == 600
    assert "output_config" not in seen[0] and seen[0]["tools"][0]["type"] == "function"
    assert cost_usd("deepseek-flash", r.usage) > 0


def test_malformed_tool_arguments_become_text():
    bad = json.loads(json.dumps(REPLY))
    bad["choices"][0]["message"]["tool_calls"][0]["function"]["arguments"] = "{not json"
    llm = OpenAICompatLLM("deepseek-flash", api_key="k", transport=fake_transport(bad, []))
    r = llm.create(
        max_tokens=10, system=[], tools=TOOLS, messages=[{"role": "user", "content": "q"}]
    )
    assert r.content[0].type == "text" and r.stop_reason == "end_turn"


def test_missing_key_raises_before_request(monkeypatch):
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    with pytest.raises(RuntimeError, match="DEEPSEEK_API_KEY"):
        OpenAICompatLLM("deepseek-flash", env_file=None)


def test_json_schema_format_mapped_to_json_object():
    seen = []
    reply = {
        "model": "deepseek-flash",
        "choices": [
            {
                "finish_reason": "stop",
                "message": {"role": "assistant", "content": '{"summary": "x"}'},
            }
        ],
        "usage": {"prompt_tokens": 10, "completion_tokens": 5},
    }
    llm = OpenAICompatLLM("deepseek-flash", api_key="k", transport=fake_transport(reply, seen))
    schema = {"type": "object", "properties": {"summary": {"type": "string"}}}
    r = llm.create(
        max_tokens=10,
        messages=[{"role": "user", "content": "q"}],
        output_config={"format": {"type": "json_schema", "schema": schema}},
    )
    assert seen[0]["response_format"] == {"type": "json_object"}
    assert "summary" in seen[0]["messages"][0]["content"] and r.content[0].text.startswith("{")
