"""
schema.py — the data contract only.

This defines the shape of a valid invoice. It does NOT contain business
validation logic — see business_validate.py for that. Pydantic's own type
checking here IS the schema validation layer (required fields, types,
date/number parsing) — that's why schema_validate.py is thin, it mostly just
catches and reports Pydantic's own errors cleanly.

FieldStatus and the `source_note` field are adapted from the
"never force every field to be populated, and always retain some form of
provenance" pattern (see design.md's "Borrowed ideas" section) — scoped down
to what's achievable without a layout/bbox model: a text description of
where a value came from, not pixel coordinates.
"""

from __future__ import annotations

from datetime import date
from enum import Enum

from pydantic import BaseModel


class FieldStatus(str, Enum):
    """
    Explicit status per field instead of forcing every field to resolve to a
    value. The model should use AMBIGUOUS/UNREADABLE/MISSING rather than
    guessing when uncertain — this is what the extraction prompt instructs.
    """

    EXTRACTED = "extracted"
    MISSING = "missing"  # field not present on this invoice at all
    AMBIGUOUS = "ambiguous"  # multiple candidate values, unclear which is right
    UNREADABLE = "unreadable"  # region exists but is illegible (blur, cutoff, etc.)


class LineItem(BaseModel):
    description: str
    quantity: float
    unit_price: float
    amount: float  # quantity * unit_price, PRE-TAX / net of any per-line VAT — must be
    # consistent with subtotal, which is always the pre-tax sum (see
    # business_validate.py::check_line_items_sum). If the invoice prints both a net
    # and a tax-inclusive column per line, use the net one.


class Invoice(BaseModel):
    vendor_name: str
    customer_name: str  # the "Bill To" party — who owes the money, not who issued the invoice
    invoice_number: str
    invoice_date: date
    due_date: date | None = None
    currency: str = (
        "USD"  # decided: single currency per invoice, no multi-currency detection (see design.md)
    )

    line_items: list[LineItem]

    subtotal: float
    # Not every invoice has every one of these — real invoices in this project's
    # test set never show tax at all, but do show discount and/or shipping. All
    # three are optional and treated as 0 when absent; see D16 in design.md and
    # business_validate.py::check_total_arithmetic for the generalized formula.
    discount: float | None = None
    shipping: float | None = None
    tax: float | None = None
    total: float

    # Per-field status, keyed by field name. Populated by extract.py from the
    # model's own reporting of extracted/missing/ambiguous/unreadable — this
    # is a DIFFERENT signal from confidence.py's heuristic confidence score
    # (which is derived from validation outcomes) and from business
    # validation status. All three stay separate in the report — see D5/D13.
    field_status: dict[str, FieldStatus] = {}

    # Citation-level grounding: a short text description of where a value
    # was read from (e.g. "table row 3", "top-right header block"), NOT
    # pixel bounding boxes. Deliberately the cheap version of grounding —
    # see design.md D14 for why bbox-level grounding is out of scope.
    source_note: dict[str, str] = {}

    # Document-level plausibility signal — DIFFERENT from field_status, which
    # is per-field. This is the model's own judgment of whether the uploaded
    # document is even the right document type at all (e.g. a marksheet
    # uploaded as an Invoice). Defaults to True so old cached extractions
    # (from before this field existed) backfill cleanly on model_validate().
    document_type_match: bool = True
    document_type_note: str | None = None  # what it looks like instead, if not a match


# NOTE: no field_confidence here. Confidence is computed downstream in
# confidence.py from validation + retry signals, never supplied by the LLM.


class Flag(BaseModel):
    """One thing a human should look at before trusting the extraction."""

    field: str
    reason: str
    layer: str  # "schema" | "business"
    severity: str  # "error" | "warning"


class ReceiptItem(BaseModel):
    description: str
    quantity: float
    unit_price: float
    amount: float


class Receipt(BaseModel):
    """
    The second document type registered in schema_registry.py — deliberately
    a different shape than Invoice, not a relabeled copy: a receipt has a
    merchant and a transaction, not a vendor/customer billing relationship,
    so there's no due_date or customer_name here, but there IS a tip and a
    payment_method, neither of which exist on Invoice. This is the actual
    test of D12/D15's claim that a second schema needs its own model and
    rules, sharing only FieldStatus/Flag's shape with the first — see D17 in
    design.md.
    """

    merchant_name: str
    transaction_id: str | None = None
    # Optional, not required like Invoice.invoice_date: a real phone-photo
    # receipt can be too blurry/cropped for a date to be legible at all (see
    # the CORD-v2 external benchmark — 16/20 extraction failures traced to
    # exactly this field being hard-required against genuinely illegible
    # photos, e.g. tests/cord_benchmark/images/cord_014.jpg). Forcing a
    # best-guess date onto an undated receipt is worse than admitting
    # "unknown" — a fabricated date is silently wrong in a way a missing one
    # isn't.
    transaction_date: date | None = None
    payment_method: str | None = None
    currency: str = "USD"

    items: list[ReceiptItem]

    subtotal: float
    tax: float | None = None
    tip: float | None = None
    total: float

    field_status: dict[str, FieldStatus] = {}
    source_note: dict[str, str] = {}
    document_type_match: bool = True
    document_type_note: str | None = None
