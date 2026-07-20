"""
confidence.py — heuristic confidence scoring.

The LLM never supplies a confidence number — self-reported LLM confidence is
not calibrated to anything real. Instead, confidence per field is derived from
actual signals produced elsewhere in the pipeline: did this field need a
retry, did it pass business validation, did the LLM itself report uncertainty
about this specific field (field_status).

v1 was a 3-value step function (HIGH/RETRIED_BUT_PASSED/FAILED). A confirmed
business-rule failure (arithmetic/date-order/duplicate) overrides everything
else straight to FAILED — that's proof the value is wrong, not a suspicion,
so nothing else can pull it back up. The one other signal used is
field_status: the LLM's OWN reported uncertainty about a field it still
provided a value for (ambiguous/unreadable) subtracts CONCERN_PENALTY from
the base score.

An OCR-based independent signal (Tesseract cross-checking extracted values)
was built and tried here, then removed: tested against real diverse
documents, it caught 0 of the real extraction errors found, and once gave a
false "agrees" on a genuine bug (a receipt with two different printed
numbers — the model grabbed the wrong one, and OCR's substring match found
that wrong number elsewhere on the same page and reported false agreement).
See GOD_FILE.md for the full writeup. The subtraction-based design (rather
than snapping straight to a fixed value) is kept because it was built to
stack multiple independent concern signals — currently there's only one
(field_status), but the shape is ready to absorb a second real signal (e.g.
self-consistency sampling) without another rewrite.
"""

from __future__ import annotations

from orchestrator import WorkerResult
from schema import FieldStatus, Flag
from schema_registry import get_scalar_field_names, get_schema

# Threshold below which a field is flagged as low-confidence (decided: 0.7,
# see design.md "Decided (previously open)").
CONFIDENCE_THRESHOLD = 0.7

HIGH = 0.95
RETRIED_BUT_PASSED = 0.75
FAILED = 0.3

# Each independent "concern" signal (currently just: the LLM itself
# reporting a field as ambiguous/unreadable) subtracts this much from the
# base score, rather than snapping to a fixed value — a fixed per-signal
# penalty is the simplest way to reflect "more independent doubts is worse"
# without inventing an unexplainable formula, and it's ready to stack a
# second signal later without a rewrite. No floor is needed with only this
# one signal: the lowest reachable result (RETRIED_BUT_PASSED -
# CONCERN_PENALTY = 0.55) is nowhere near FAILED (0.3) — add a floor if a
# future second signal makes stacking reach that low.
CONCERN_PENALTY = 0.2

# field_status values that represent the LLM's OWN reported uncertainty about
# a field it nonetheless provided a value for. MISSING is deliberately
# excluded — a legitimately absent optional field (e.g. no discount on this
# invoice) reporting "missing" is accurate, not a concern.
_UNCERTAIN_STATUSES = {FieldStatus.AMBIGUOUS, FieldStatus.UNREADABLE}


def score_fields(
    field_names: list[str],
    business_flags: list[Flag],
    retried_fields: set[str],
    field_status: dict[str, FieldStatus] | None = None,
) -> dict[str, float]:
    """
    Compute a confidence score per field name.

    - Field has an unresolved business flag -> FAILED, unconditionally (a
      proven-wrong value isn't made "more wrong" by other signals, and
      nothing else can make it less wrong either)
    - Otherwise, start from a base: RETRIED_BUT_PASSED if the field needed a
      correction round (even though it now passes), else HIGH
    - Then subtract CONCERN_PENALTY if the LLM's own field_status reports
      this field as ambiguous or unreadable
    """
    flagged_fields = {f.field for f in business_flags if f.severity == "error"}
    field_status = field_status or {}

    scores: dict[str, float] = {}
    for name in field_names:
        if name in flagged_fields:
            scores[name] = FAILED
            continue

        score = RETRIED_BUT_PASSED if name in retried_fields else HIGH

        if field_status.get(name) in _UNCERTAIN_STATUSES:
            score -= CONCERN_PENALTY

        scores[name] = score
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
    document = state.get("document")
    field_status = getattr(document, "field_status", {}) or {}

    scores = score_fields(field_names, business_flags, retried_fields, field_status)
    return WorkerResult(status="ok", state={**state, "confidence": scores})
