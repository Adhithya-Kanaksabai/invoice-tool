"""
report.py — the Report Worker (T10).

Per D7: this is a rendering of already-computed data (field_status from
extraction, business/schema flags from validate.py, confidence from
confidence.py) — not new validation logic of its own. Per D13, the three
signals stay visibly separate here: each field's report entry carries
field_status, confidence, AND its flags independently, rather than being
collapsed into one combined pass/fail bit. A field can be confidence-flagged
without a business-rule error, or vice versa.
"""

from __future__ import annotations

from confidence import CONFIDENCE_THRESHOLD
from orchestrator import WorkerResult
from schema import Flag
from schema_registry import get_schema


def _field_value(invoice, field_name: str):
    value = getattr(invoice, field_name, None)
    return value.isoformat() if hasattr(value, "isoformat") else value


def build_report(state: dict) -> dict:
    doc_schema = get_schema(state["schema_id"])
    invoice = state["invoice"]
    flags: list[Flag] = state.get("flags", [])
    confidence: dict[str, float] = state.get("confidence", {})

    field_names = [
        name
        for name in doc_schema.model.model_fields
        if name not in {"line_items", "field_status", "source_note"}
    ]

    flags_by_field: dict[str, list[Flag]] = {}
    for f in flags:
        flags_by_field.setdefault(f.field, []).append(f)

    grouped: dict[str, list[dict]] = {"pass": [], "warnings": [], "errors": []}
    for name in field_names:
        field_flags = flags_by_field.get(name, [])
        has_error = any(f.severity == "error" for f in field_flags)
        has_warning = any(f.severity == "warning" for f in field_flags)
        score = confidence.get(name)
        low_confidence = score is not None and score < CONFIDENCE_THRESHOLD
        status = invoice.field_status.get(name)
        ambiguous_or_unreadable = status in ("ambiguous", "unreadable")

        entry = {
            "field": name,
            "value": _field_value(invoice, name),
            "field_status": status,
            "confidence": score,
            "source_note": invoice.source_note.get(name),
            "flags": [
                {"layer": f.layer, "severity": f.severity, "reason": f.reason} for f in field_flags
            ],
        }

        if has_error:
            grouped["errors"].append(entry)
        elif has_warning or low_confidence or ambiguous_or_unreadable:
            grouped["warnings"].append(entry)
        else:
            grouped["pass"].append(entry)

    return grouped


def report_worker(state: dict) -> WorkerResult:
    return WorkerResult(status="ok", state={**state, "report": build_report(state)})
