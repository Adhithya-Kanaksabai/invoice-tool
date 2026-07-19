"""
schema_registry.py — the one place "which document type" is looked up.

This is the entire mechanism for "invoice is the first schema of a document
processing engine, not a hardcoded assumption." Workers (extract, validate,
retry) take a schema_id and look up what they need here, instead of
importing Invoice / RETRY_GROUPS / INVOICE_BUSINESS_RULES by name.

Deliberately NOT built: a generic field-type abstraction over Pydantic
(Pydantic already is that layer), a business-rule DSL (rules are plain
Python callables — see business_validate.py), a generic prompt-builder for
arbitrary schemas (prompt quality is schema-specific, not worth abstracting
until a second schema exists to compare against). Adding those now would be
solving problems this project doesn't have yet. See design.md "Second
schema readiness" for the full reasoning.

To add a second document type later: write its Pydantic model, its business
rule functions (same shape as business_validate.py's), its retry groups,
register a new DocumentSchema entry below. Nothing in orchestrator.py,
schema_validate.py's generic path, or retry.py's generic path needs to
change.
"""

from __future__ import annotations

import typing
from collections.abc import Callable
from dataclasses import dataclass

from pydantic import BaseModel

from business_validate import INVOICE_BUSINESS_RULES, RETRY_GROUPS
from business_validate_receipt import RECEIPT_BUSINESS_RULES, RECEIPT_RETRY_GROUPS
from schema import Flag, Invoice, Receipt

BusinessRule = Callable[..., list[Flag]]

# Same on every registered schema's model, regardless of document type — the
# shared FieldStatus/source_note pattern, not per-document-type fields.
_ALWAYS_EXCLUDED_FIELDS = {"field_status", "source_note"}


@dataclass
class DocumentSchema:
    schema_id: str
    model: type[BaseModel]
    business_rules: list[BusinessRule]
    retry_groups: dict[str, list[str]]
    required_fields: list[str]  # for schema_validate.py's empty-check, see D13


REGISTRY: dict[str, DocumentSchema] = {
    "invoice-v1": DocumentSchema(
        schema_id="invoice-v1",
        model=Invoice,
        business_rules=INVOICE_BUSINESS_RULES,
        retry_groups=RETRY_GROUPS,
        required_fields=["vendor_name", "customer_name", "invoice_number"],
    ),
    "receipt-v1": DocumentSchema(
        schema_id="receipt-v1",
        model=Receipt,
        business_rules=RECEIPT_BUSINESS_RULES,
        retry_groups=RECEIPT_RETRY_GROUPS,
        required_fields=["merchant_name"],
    ),
}


def get_schema(schema_id: str) -> DocumentSchema:
    if schema_id not in REGISTRY:
        raise KeyError(f"Unknown schema_id '{schema_id}'. Registered: {list(REGISTRY)}")
    return REGISTRY[schema_id]


def get_scalar_field_names(doc_schema: DocumentSchema) -> list[str]:
    """
    Field names to treat as individually-reportable/scoreable — excludes the
    list-typed field (named differently per schema: "line_items" for
    invoices, "items" for receipts, detected by type not by name) and the two
    always-shared bookkeeping fields. Used by confidence.py and report.py so
    neither hardcodes a schema-specific field name.
    """
    return [
        name
        for name in doc_schema.model.model_fields
        if name not in _ALWAYS_EXCLUDED_FIELDS
        and typing.get_origin(doc_schema.model.model_fields[name].annotation) is not list
    ]


def get_list_field_name(doc_schema: DocumentSchema) -> str:
    """The one list-typed field on this schema ("line_items" for invoices,
    "items" for receipts) — used by app.py and eval.py so neither hardcodes
    which name a given schema uses."""
    for name, field_info in doc_schema.model.model_fields.items():
        if name not in _ALWAYS_EXCLUDED_FIELDS and typing.get_origin(field_info.annotation) is list:
            return name
    raise ValueError(f"schema {doc_schema.schema_id} has no list-typed field")
