"""
schema_validate.py — Layer 1: structural correctness. Schema-agnostic.

Answers: "is this well-formed?" Required fields present, correct types,
dates/numbers parseable. Does NOT check domain correctness (arithmetic, date
ordering) — that's business_validate.py (and is invoice-specific, registered
per-schema — see schema_registry.py).

Most of this is Pydantic's job already. This module exists to (a) catch
Pydantic's ValidationErrors and turn them into Flag objects instead of raw
exceptions, and (b) check required-but-empty fields, which Pydantic's type
system alone doesn't express.

This function takes a `schema_id`, not `Invoice` by name — it works for any
schema registered in schema_registry.py without modification. This is the
"grows into a document processing engine without major refactoring" part:
Layer 1 validation is already fully generic, today, for exactly one reason —
"is this well-formed JSON matching a Pydantic model" never needed to know
what an invoice is in the first place.
"""

from __future__ import annotations

from pydantic import BaseModel, ValidationError

from schema import Flag
from schema_registry import get_schema


def validate_schema(raw: dict, schema_id: str) -> tuple[BaseModel | None, list[Flag]]:
    """
    Attempt to parse raw extraction output into the model registered under
    `schema_id`. Returns (model instance or None, flags). If parsing fails
    entirely, the instance is None and the flags explain why — the caller
    (orchestrator, via the Validation Worker) decides whether that's
    retryable.
    """
    doc_schema = get_schema(schema_id)
    flags: list[Flag] = []

    try:
        instance = doc_schema.model.model_validate(raw)
    except ValidationError as e:
        for err in e.errors():
            flags.append(
                Flag(
                    field=".".join(str(p) for p in err["loc"]),
                    reason=err["msg"],
                    layer="schema",
                    severity="error",
                )
            )
        return None, flags

    for name in doc_schema.required_fields:
        if not getattr(instance, name, None):
            flags.append(
                Flag(
                    field=name,
                    reason="required field is empty",
                    layer="schema",
                    severity="error",
                )
            )

    return instance, flags
