"""Claude API 封装：模型、价格、成本。价格来自 claude-api skill（2026-10-06），变动时只改这里。"""

from __future__ import annotations

import os
from typing import Any, Protocol

from docpilot.config import ROOT

HAIKU = "claude-haiku-5-5"
SONNET = "claude-sonnet-5-5"
DEFAULT_MODEL = HAIKU

# 美元 / 百万 token：(输入, 输出, 缓存读取)。缓存写入按输入价 1.25 倍计（与 DataPilot 一致）
PRICING = {
    HAIKU: (0.10, 0.50, 0.01),
    SONNET: (2.00, 10.00, 0.20),
}


def cost_usd(model: str, usage: Any) -> float:
    inp, out, read = PRICING[model]
    cache_read = getattr(usage, "cache_read_input_tokens", 0) or 0
    cache_write = getattr(usage, "cache_creation_input_tokens", 0) or 0
    return (
        usage.input_tokens * inp
        + usage.output_tokens * out
        + cache_read * read
        + cache_write * inp * 1.25
    ) / 1e6


class LLM(Protocol):
    model: str

    def create(self, **kwargs: Any) -> Any: ...


def _load_dotenv() -> None:
    env = ROOT / ".env"
    if "ANTHROPIC_API_KEY" in os.environ or not env.exists():
        return
    for line in env.read_text(encoding="utf-8").splitlines():
        key, sep, value = line.partition("=")
        if sep and key.strip() == "ANTHROPIC_API_KEY":
            os.environ["ANTHROPIC_API_KEY"] = value.strip().strip('"')


class AnthropicLLM:
    def __init__(self, model: str = DEFAULT_MODEL) -> None:
        import anthropic

        _load_dotenv()
        self.model = model
        self.client = anthropic.Anthropic(max_retries=3)

    def create(self, **kwargs: Any) -> Any:
        return self.client.messages.create(model=self.model, **kwargs)
