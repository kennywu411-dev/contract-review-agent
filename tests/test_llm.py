from types import SimpleNamespace

import pytest

from contract_review.llm import PRICING, cost_usd


def usage(inp=0, out=0, read=0, write=0):
    return SimpleNamespace(
        input_tokens=inp,
        output_tokens=out,
        cache_read_input_tokens=read,
        cache_creation_input_tokens=write,
    )


def test_cost_includes_cache_tiers():
    m = "claude-haiku-5-5"
    i, o, r = PRICING[m]
    got = cost_usd(m, usage(1_000_000, 1_000_000, 1_000_000, 1_000_000))
    assert got == pytest.approx(i + o + r + i * 1.25)


def test_cost_handles_missing_cache_fields():
    u = SimpleNamespace(input_tokens=1000, output_tokens=0)
    assert cost_usd("claude-haiku-5-5", u) == pytest.approx(0.0001)


def test_unknown_model_raises():
    with pytest.raises(KeyError):
        cost_usd("gpt-x", usage(1))
