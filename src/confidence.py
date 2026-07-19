"""
confidence.py — heuristic confidence scoring.

The LLM never supplies a confidence number — self-reported LLM confidence is
not calibrated to anything real. Instead, confidence per field is derived from
actual signals produced elsewhere in the pipeline: did this field need a
retry, did it pass business validation, is it arithmetically consistent.

Start simple (binary-ish scoring). This is intentionally easy to extend later
(e.g. weighting by how many signals agree) but the MVP does not need that
sophistication — a defensible, explainable heuristic beats a fancier one you
can't explain in an interview.
"""

from __future__ import annotations

from orchestrator import WorkerResult
from schema import Flag
from schema_registry import get_scalar_field_names, get_schema

# Threshold below which a field is flagged as low-confidence (decided: 0.7,
# see design.md "Decided (previously open)").
CONFIDENCE_THRESHOLD = 0.7

HIGH = 0.95
RETRIED_BUT_PASSED = 0.75
FAILED = 0.3


def score_fields(
    field_names: list[str],
    business_flags: list[Flag],
    retried_fields: set[str],
) -> dict[str, float]:
    """
    Compute a confidence score per field name.

    - Field has an unresolved business flag -> FAILED
    - Field was retried and now has no flag -> RETRIED_BUT_PASSED (lower than
      a clean first-pass extraction, since it needed correction once)
    - Field passed cleanly on the first try -> HIGH
    """
    flagged_fields = {f.field for f in business_flags if f.severity == "error"}

    scores: dict[str, float] = {}
    for name in field_names:
        if name in flagged_fields:
            scores[name] = FAILED
        elif name in retried_fields:
            scores[name] = RETRIED_BUT_PASSED
        else:
            scores[name] = HIGH
    return scores


def low_confidence_flags(scores: dict[str, float]) -> list[Flag]:
    return [
        Flag(
            field=name,
            reason=f"confidence {score:.2f} below threshold {CONFIDENCE_THRESHOLD}",
            layer="business",
            severity="warning",
        )
        for name, score in scores.items()
        if score < CONFIDENCE_THRESHOLD
    ]


def confidence_worker(state: dict) -> WorkerResult:
    """
    Confidence scoring is a deterministic read of signals already computed by
    the Validation Worker (and, if it ran, the Correction Worker) — there's no
    retry decision to make here, so this always returns status="ok". Kept as
    its own explicit pipeline step (not folded into report.py) so
    state["confidence"] exists as its own signal, per design.md's data flow
    contract and D13 (three signals stay visibly separate).
    """
    doc_schema = get_schema(state["schema_id"])
    field_names = get_scalar_field_names(doc_schema)
    business_flags = [f for f in state.get("flags", []) if f.layer == "business"]
    retried_fields = state.get("retried_fields", set())

    scores = score_fields(field_names, business_flags, retried_fields)
    return WorkerResult(status="ok", state={**state, "confidence": scores})
