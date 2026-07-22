"""
Unit tests for llm_usage.py — token accounting and cost estimation.

No API calls: a fake response object with a usage_metadata attribute is all
the real google-genai surface this module touches.
"""

import llm_usage
from llm_usage import (
    MODEL_PRICING,
    TOKENS_KEY,
    accumulate_usage,
    estimate_cost_usd,
    pricing_label,
    usage_from_response,
)


class _FakeUsage:
    def __init__(self, prompt=None, candidates=None, total=None):
        self.prompt_token_count = prompt
        self.candidates_token_count = candidates
        self.total_token_count = total


class _FakeResponse:
    def __init__(self, usage=None):
        if usage is not None:
            self.usage_metadata = usage


def test_usage_from_response_reads_all_three_counts():
    usage = usage_from_response(_FakeResponse(_FakeUsage(1200, 300, 1500)))
    assert usage == {"prompt": 1200, "candidates": 300, "total": 1500, "calls": 1}


def test_usage_from_response_returns_none_without_metadata():
    # A response with no usage_metadata must not be recorded as a real zero —
    # "we don't know" and "it was free" are different claims.
    assert usage_from_response(_FakeResponse()) is None


def test_usage_from_response_derives_total_when_missing():
    usage = usage_from_response(_FakeResponse(_FakeUsage(100, 40, None)))
    assert usage["total"] == 140


def test_accumulate_usage_sums_across_calls():
    state = {}
    accumulate_usage(state, _FakeResponse(_FakeUsage(100, 10, 110)))
    accumulate_usage(state, _FakeResponse(_FakeUsage(200, 20, 220)))
    assert state[TOKENS_KEY] == {"prompt": 300, "candidates": 30, "total": 330, "calls": 2}


def test_accumulate_usage_survives_shallow_state_copies():
    """
    The whole reason accumulation is in-place: workers hand state along as
    {**state, ...} shallow copies. Tokens counted in extract.py must still be
    visible after four more workers have each rebuilt the dict.
    """
    state = {}
    accumulate_usage(state, _FakeResponse(_FakeUsage(100, 10, 110)))
    downstream = {**state, "document": "something"}
    accumulate_usage(downstream, _FakeResponse(_FakeUsage(50, 5, 55)))
    assert downstream[TOKENS_KEY]["total"] == 165
    assert state[TOKENS_KEY]["total"] == 165  # same nested dict, as designed


def test_accumulate_usage_ignores_response_without_metadata():
    state = {}
    accumulate_usage(state, _FakeResponse())
    assert state[TOKENS_KEY] == {"prompt": 0, "candidates": 0, "total": 0, "calls": 0}


def test_estimate_cost_uses_separate_input_and_output_rates():
    price = MODEL_PRICING["gemini-3.1-flash-lite"]
    cost = estimate_cost_usd(1_000_000, 1_000_000, "gemini-3.1-flash-lite")
    assert cost == price.input_per_1m + price.output_per_1m


def test_estimate_cost_scales_linearly():
    cost = estimate_cost_usd(500_000, 0, "gemini-3.1-flash-lite")
    assert cost == MODEL_PRICING["gemini-3.1-flash-lite"].input_per_1m / 2


def test_estimate_cost_returns_none_for_unpriced_model():
    # Not 0.0 — an unknown model must report "unknown", never a free lunch.
    assert estimate_cost_usd(1000, 1000, "some-future-model") is None


def test_pricing_label_always_carries_the_as_of_date():
    label = pricing_label("gemini-3.1-flash-lite")
    assert "estimated" in label
    assert llm_usage.PRICING_AS_OF in label


def test_pricing_label_says_so_when_no_rate_configured():
    label = pricing_label("some-future-model")
    assert "no published rate" in label
