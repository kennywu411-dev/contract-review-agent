"""OpenAI 兼容接口的模型（DeepSeek 等），对外和 AnthropicLLM 一样：
create(**Anthropic 风格参数) → Anthropic 形状的消息。

这样 agent、工具循环、trace 都不用改（ADR 006 记录两边的差异）：
- 工具：Anthropic 的 {name, description, input_schema} → OpenAI 的 function；全部工具都是 strict 时
  透传 strict 并走 DeepSeek 的 Beta 端点（官网 Tool Calls 指南：strict 是 Beta 功能）。
- 消息：tool_use 块 → assistant.tool_calls；tool_result 块 → role=tool 消息。
- effort、cache_control 没有对应参数，忽略（DeepSeek 的前缀缓存是自动的）。
- 工具参数不是合法 JSON 时先做保守修复（字符串里未转义的英文双引号，问题日志 P22）；
  仍然不合法才当作普通文本返回，交给 agent 按"没调终止工具"处理（不让整题崩）。
"""

from __future__ import annotations

import json
import os
import time
from types import SimpleNamespace
from typing import Any

import httpx

from contract_review.config import ROOT

# 模型 → (API 根地址, key 的环境变量名)
ENDPOINTS = {"deepseek-flash": ("https://api.deepseek.com", "DEEPSEEK_API_KEY")}
_STRUCTURAL = set(",}]:")


def repair_json(raw: str) -> str:
    """修复字符串内部未转义的英文双引号：字符串里的 " 后面（跳过空白）不是 , } ] : 或结尾，
    就当作正文里的引号转义掉。合法 JSON 原样返回。"""
    out: list[str] = []
    in_str = esc = False
    n = len(raw)
    for i, ch in enumerate(raw):
        if not in_str:
            in_str = ch == '"'
            out.append(ch)
            continue
        if esc:
            esc = False
        elif ch == "\\":
            esc = True
        elif ch == '"':
            j = i + 1
            while j < n and raw[j] in " \t\r\n":
                j += 1
            if j >= n or raw[j] in _STRUCTURAL:
                in_str = False
            else:
                out.append("\\")
        out.append(ch)
    return "".join(out)


def _get(b, key, default=None):
    return b.get(key, default) if isinstance(b, dict) else getattr(b, key, default)


def to_openai_tools(tools: list[dict]) -> list[dict]:
    strict = bool(tools) and all(t.get("strict") for t in tools)
    out = []
    for t in tools:
        fn = {"name": t["name"], "description": t["description"], "parameters": t["input_schema"]}
        if strict:
            fn["strict"] = True
        out.append({"type": "function", "function": fn})
    return out


def to_openai_messages(system: list[dict], messages: list[dict]) -> list[dict]:
    out: list[dict] = []
    sys_text = "\n".join(b["text"] for b in system if b.get("type") == "text")
    if sys_text:
        out.append({"role": "system", "content": sys_text})
    for m in messages:
        content = m["content"]
        if isinstance(content, str):
            out.append({"role": m["role"], "content": content})
            continue
        if m["role"] == "assistant":
            text = "".join(_get(b, "text", "") for b in content if _get(b, "type") == "text")
            calls = [
                {
                    "id": _get(b, "id"),
                    "type": "function",
                    "function": {
                        "name": _get(b, "name"),
                        "arguments": json.dumps(_get(b, "input"), ensure_ascii=False),
                    },
                }
                for b in content
                if _get(b, "type") == "tool_use"
            ]
            msg: dict[str, Any] = {"role": "assistant", "content": text}
            if calls:
                msg["tool_calls"] = calls
            out.append(msg)
        else:
            for b in content:
                if _get(b, "type") == "tool_result":
                    out.append(
                        {
                            "role": "tool",
                            "tool_call_id": _get(b, "tool_use_id"),
                            "content": _get(b, "content"),
                        }
                    )
                elif _get(b, "type") == "text":
                    out.append({"role": "user", "content": _get(b, "text")})
    return out


def _read_key(env_name: str, env_file) -> str | None:
    if os.environ.get(env_name):
        return os.environ[env_name]
    if env_file and env_file.exists():
        for line in env_file.read_text(encoding="utf-8").splitlines():
            key, sep, value = line.partition("=")
            if sep and key.strip() == env_name and value.strip():
                return value.strip().strip('"')
    return None


class OpenAICompatLLM:
    def __init__(
        self, model: str, api_key: str | None = None, env_file=ROOT / ".env", transport=None
    ):
        base, env_name = ENDPOINTS[model]
        key = api_key or _read_key(env_name, env_file)
        if not key:
            raise RuntimeError(f"缺少 {env_name}：请写进环境变量或仓库根目录的 .env")
        self.model, self.base = model, base
        self.client = httpx.Client(
            headers={"Authorization": f"Bearer {key}"}, timeout=120, transport=transport
        )

    def url_for(self, strict: bool) -> str:
        return f"{self.base}{'/beta' if strict else ''}/chat/completions"

    def create(self, **kw: Any) -> SimpleNamespace:
        system = list(kw.get("system", []))
        fmt = (kw.get("output_config") or {}).get("format")
        if fmt and fmt.get("type") == "json_schema":
            # OpenAI 兼容接口没有 json_schema 约束：改用 json_object，并把 schema 写进 system
            schema = json.dumps(fmt["schema"], ensure_ascii=False)
            system.append(
                {"type": "text", "text": f"只输出一个符合此 JSON Schema 的 JSON 对象：{schema}"}
            )
        body: dict[str, Any] = {
            "model": self.model,
            "max_tokens": kw.get("max_tokens", 4000),
            "messages": to_openai_messages(system, kw["messages"]),
        }
        if fmt and fmt.get("type") == "json_schema":
            body["response_format"] = {"type": "json_object"}
        strict = False
        if kw.get("tools"):
            body["tools"] = to_openai_tools(kw["tools"])
            strict = all(t["function"].get("strict") for t in body["tools"])
        for attempt in range(4):  # 429 / 5xx / 网络错误：指数退避重试
            try:
                r = self.client.post(self.url_for(strict), json=body)
            except httpx.TransportError:
                if attempt == 3:
                    raise
                time.sleep(2**attempt)
                continue
            if r.status_code < 500 and r.status_code != 429 or attempt == 3:
                break
            time.sleep(2**attempt)
        r.raise_for_status()
        data = r.json()
        if not data.get("choices"):
            raise RuntimeError(f"{self.model} 返回了空的 choices")
        choice = data["choices"][0]
        msg = choice["message"]
        blocks: list[SimpleNamespace] = []
        if msg.get("content"):
            blocks.append(SimpleNamespace(type="text", text=msg["content"]))
        malformed = False
        for c in msg.get("tool_calls") or []:
            raw = c["function"]["arguments"] or "{}"
            try:
                args = json.loads(raw)
            except json.JSONDecodeError:
                try:
                    args = json.loads(repair_json(raw))
                except json.JSONDecodeError:
                    args = None
            if args is None:
                malformed = True
                blocks.append(SimpleNamespace(type="text", text=c["function"]["arguments"]))
                continue
            blocks.append(
                SimpleNamespace(type="tool_use", id=c["id"], name=c["function"]["name"], input=args)
            )
        has_tool = any(b.type == "tool_use" for b in blocks)
        finish = choice.get("finish_reason")
        stop = "tool_use" if has_tool else "max_tokens" if finish == "length" else "end_turn"
        if malformed and not has_tool:
            stop = "end_turn"
        u = data.get("usage", {})
        hit = u.get("prompt_cache_hit_tokens", 0)
        usage = SimpleNamespace(
            input_tokens=u.get("prompt_tokens", 0) - hit,
            output_tokens=u.get("completion_tokens", 0),
            cache_read_input_tokens=hit,
            cache_creation_input_tokens=0,
        )
        return SimpleNamespace(
            content=blocks, stop_reason=stop, usage=usage, model=data.get("model", self.model)
        )
