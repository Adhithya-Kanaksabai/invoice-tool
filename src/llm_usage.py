"""
llm_usage.py — token accounting and cost estimation for the two places that
actually call Gemini (extract.py and retry.py).

Why this exists: "it extracts invoices" and "I know what this system costs per
document" are different claims, and only the second one is measurable. Tokens
are the HARD number — they come straight off the API response's
`usage_metadata`. Cost is DERIVED from a published price list that can change
without notice, so every cost figure this module produces is labelled an
estimate with the date the rate was read. Never present a derived number with
the same confidence as a measured one.

Accumulation is deliberately IN-PLACE on the pipeline state dict. Workers pass
state along as `{**state, ...}` shallow copies, so a nested dict created once
here stays shared across every later copy — which is exactly what's needed to
sum tokens across extract.py's internal retries AND retry.py's correction
turns, without threading a return value through the WorkerResult contract
(which this session must not change).
"""

from __future__ import annotations

from dataclasses import dataclass

# Reserved state key. Underscore-prefixed to mark it as pipeline metadata
# rather than extracted document data — nothing downstream should treat it as
# a schema field.
TOKENS_KEY = "_llm_tokens"


@dataclass(frozen=True)
class ModelPrice:
    """USD per 1,000,000 tokens."""

    input_per_1m: float
    output_per_1m: float


# Read from Google's official Gemini Developer API pricing page (paid tier,
# standard context) on the date below. NOT guessed. If this project ever
# reports a cost for a model that isn't listed here, it reports "unknown"
# rather than silently applying the wrong rate.
#   source: https://ai.google.dev/gemini-api/docs/pricing
PRICING_AS_OF = "2026-07-22"
PRICING_SOURCE = "https://ai.google.dev/gemini-api/docs/pricing"
MODEL_PRICING: dict[str, ModelPrice] = {
    "gemini-3.1-flash-lite": ModelPrice(input_per_1m=0.25, output_per_1m=1.50),
}


def empty_usage() -> dict:
    return {"prompt": 0, "candidates": 0, "total": 0, "calls": 0}


def usage_from_response(response) -> dict | None:
    """
    Pull token counts off a google-genai response's `usage_metadata`.

    Returns None when the response carries no usage metadata at all (some
    error paths, and every test double that doesn't bother to fake it) — a
    missing count must never be silently recorded as zero real usage, and
    must never raise into the extraction path either. Individual counts that
    come back None are coerced to 0; that case means "this call reported
    partial metadata", not "no call happened".
    """
    meta = getattr(response, "usage_metadata", None)
    if meta is None:
        return None

    def _count(attr: str) -> int:
        value = getattr(meta, attr, None)
        return int(value) if value else 0

    prompt = _count("prompt_token_count")
    candidates = _count("candidates_token_count")
    total = _count("total_token_count") or (prompt + candidates)
    return {"prompt": prompt, "candidates": candidates, "total": total, "calls": 1}


def accumulate_usage(state: dict, response) -> dict:
    """
    Add one Gemini call's token usage to `state[TOKENS_KEY]`, in place.

    Safe to call after every single API call — including the ones that go on
    to fail schema validation and get retried. A retried call still burned
    tokens and still cost money, so it still counts. Returns the running
    totals dict for convenience.
    """
    usage = usage_from_response(response)
    running = state.setdefault(TOKENS_KEY, empty_usage())
    if usage is None:
        return running
    for key in ("prompt", "candidates", "total", "calls"):
        running[key] = running.get(key, 0) + usage[key]
    return running


def estimate_cost_usd(prompt_tokens: int, candidates_tokens: int, model_name: str) -> float | None:
    """
    Estimated USD cost of one document's calls. Returns None — not 0.0 — for
    a model with no configured rate, so callers can say "unknown" instead of
    reporting a confidently wrong free lunch.
    """
    price = MODEL_PRICING.get(model_name)
    if price is None:
        return None
    return (prompt_tokens / 1_000_000) * price.input_per_1m + (
        candidates_tokens / 1_000_000
    ) * price.output_per_1m


def pricing_label(model_name: str) -> str:
    """The disclaimer that must travel with every cost number this repo prints."""
    price = MODEL_PRICING.get(model_name)
    if price is None:
        return f"no published rate configured for {model_name} — cost not estimated"
    return (
        f"estimated at ${price.input_per_1m:.2f}/1M input tokens and "
        f"${price.output_per_1m:.2f}/1M output tokens (rate as of {PRICING_AS_OF})"
    )
