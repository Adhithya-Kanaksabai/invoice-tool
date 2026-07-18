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

from collections.abc import Callable
from dataclasses import dataclass

from pydantic import BaseModel

from business_validate import INVOICE_BUSINESS_RULES, RETRY_GROUPS
from schema import Flag, Invoice

BusinessRule = Callable[..., list[Flag]]


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
}


def get_schema(schema_id: str) -> DocumentSchema:
    if schema_id not in REGISTRY:
        raise KeyError(f"Unknown schema_id '{schema_id}'. Registered: {list(REGISTRY)}")
    return REGISTRY[schema_id]
