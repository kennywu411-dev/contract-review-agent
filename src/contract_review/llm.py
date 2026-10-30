"""Claude API 封装：模型、价格、成本。价格来自 Anthropic 官方价目表，变动时只改这里。"""

from __future__ import annotations

import os
from typing import Any, Protocol

from contract_review.config import ROOT

HAIKU = "claude-haiku-5-5"
SONNET = "claude-sonnet-5-5"
DEFAULT_MODEL = HAIKU

# 美元 / 百万 token：(输入, 输出, 缓存读取)。缓存写入按输入价 1.25 倍计（与 DataPilot 一致）
PRICING = {
    HAIKU: (0.10, 0.50, 0.01),
    SONNET: (2.00, 10.00, 0.20),
    # DeepSeek 官网：高峰价，非高峰为一半；这里统一按高峰价计，花费只会偏高
    "deepseek-flash": (0.30, 1.20, 0.006),
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


MISSING_API_KEY_MSG = "未设置 ANTHROPIC_API_KEY，请在 .env 中填写后重启"


def api_key_missing() -> bool:
    """环境变量和根目录 .env 里都没有 ANTHROPIC_API_KEY。"""
    _load_dotenv()
    return not os.environ.get("ANTHROPIC_API_KEY", "").strip()


class AnthropicLLM:
    """fallbacks=True 时走 beta 接口并开启服务端 refusal fallback（官方对
    Sonnet 5.5 的推荐做法）；被拒时由服务端换模型重跑，响应的 model 字段会变。"""

    def __init__(self, model: str = DEFAULT_MODEL, fallbacks: bool = False) -> None:
        import anthropic

        if model not in PRICING:  # 先校验，避免花了钱才发现算不了成本
            raise ValueError(f"未登记价格的模型：{model}（可选：{', '.join(PRICING)}）")
        _load_dotenv()
        self.model = model
        self.fallbacks = fallbacks
        self.client = anthropic.Anthropic(max_retries=3)

    def create(self, **kwargs: Any) -> Any:
        if self.fallbacks:
            return self.client.beta.messages.create(
                model=self.model,
                betas=["server-side-fallback-2026-07-01"],
                fallbacks="default",
                **kwargs,
            )
        return self.client.messages.create(model=self.model, **kwargs)


def make_llm(model: str):
    """按模型名选封装：claude-* 走 Anthropic SDK，其余走 OpenAI 兼容接口（agent 代码不用改）。"""
    if model.startswith("claude-"):
        return AnthropicLLM(model)
    from contract_review.openai_compat import OpenAICompatLLM

    return OpenAICompatLLM(model)
