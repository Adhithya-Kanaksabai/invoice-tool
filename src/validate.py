"""
validate.py — the Validation Worker (T6/T7).

Wraps the two already-written, separate validation layers into a single
WorkerResult-returning worker for the orchestrator: schema_validate.py
(structural correctness, schema-agnostic) and business_validate.py (domain
correctness, invoice-specific business rules from the registry). Their flag
lists are merged into state["flags"] but the flags themselves stay tagged by
layer ("schema" vs "business") — see D3, this file does not blur that
distinction, it just runs both and concatenates results.

status is "retry" if any error-severity flag exists (the orchestrator hands
off to the Correction Worker per design.md's orchestrator/worker contract),
"ok" otherwise. Warning-severity flags don't trigger a retry — see FR6/D5,
warnings feed confidence scoring instead.
"""

from __future__ import annotations

from orchestrator import WorkerResult
from schema import Flag
from schema_registry import get_schema
from schema_validate import validate_schema


def validation_worker(state: dict) -> WorkerResult:
    """
    Runs schema validation (layer 1) then business validation (layer 2)
    against state["document"], merging both flag lists into state["flags"].

    Business rules come from doc_schema.business_rules (looked up via the
    registry, per schema_id) rather than importing business_validate by name
    — this worker stays schema-agnostic the same way orchestrator.py and
    schema_validate.py do; it never knows "invoice" specifically, only that
    some schema_id has a model and a rule list. The `seen_ids` kwarg is the
    same schema-agnostic name for every schema's "duplicate within this
    batch" check — each schema's own rule decides which of its fields that
    maps to (invoice_number vs transaction_id); see business_validate.py and
    business_validate_receipt.py's duplicate-check functions.

    Extraction (extract.py) already produced a well-typed Pydantic instance,
    so schema_validate.validate_schema here mainly re-checks required-but-empty
    fields (its ValidationError path is defense-in-depth for callers that pass
    a raw dict directly, e.g. tests/eval.py, rather than the extract.py path).
    """
    schema_id = state["schema_id"]
    document = state["document"]
    doc_schema = get_schema(schema_id)

    _, schema_flags = validate_schema(document.model_dump(mode="json"), schema_id)

    seen_ids = state.get("seen_document_ids")
    business_flags: list[Flag] = []
    for rule in doc_schema.business_rules:
        business_flags.extend(rule(document, seen_ids=seen_ids))

    flags: list[Flag] = schema_flags + business_flags
    status = "retry" if any(f.severity == "error" for f in flags) else "ok"

    return WorkerResult(
        status=status,
        state={**state, "flags": flags},
    )
