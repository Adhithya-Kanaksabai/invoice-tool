"""
business_validate.py — Layer 2: domain correctness, for the invoice schema.

Answers: "is this correct, given what we know about how invoices work?"
Arithmetic, date ordering, duplicate detection. Assumes the input already
passed schema_validate.py (it's a well-formed Invoice) — this layer is about
whether the well-formed values make sense together.

Kept deliberately separate from schema_validate.py: a malformed value and a
domain-inconsistent value are different failure classes with different retry
implications, and an interviewer will ask about that distinction.

Each rule below is a standalone function: (Invoice, **context) -> list[Flag].
This is what makes them individually registrable in schema_registry.py — a
future second schema (receipt, PO, etc.) supplies its own rule list, this
module's rules are never touched or reused. Only the SHAPE of "a rule is a
callable returning flags" is shared, nothing invoice-specific leaks upward.
"""

from __future__ import annotations

from schema import Flag, Invoice

# Arithmetic tolerance: rounds to the nearest cent rather than exact match
# (decided: see design.md "Decided (previously open)").
AMOUNT_TOLERANCE = 0.01


def check_line_items_sum(invoice: Invoice, **_) -> list[Flag]:
    """Line items should sum to subtotal."""
    line_sum = round(sum(li.amount for li in invoice.line_items), 2)
    if abs(line_sum - invoice.subtotal) > AMOUNT_TOLERANCE:
        return [
            Flag(
                field="subtotal",
                reason=f"line items sum to {line_sum} but subtotal is {invoice.subtotal}",
                layer="business",
                severity="error",
            )
        ]
    return []


def check_total_arithmetic(invoice: Invoice, **_) -> list[Flag]:
    """
    subtotal - discount + shipping + tax should equal total.

    Generalized to cover invoices that carry any subset of {discount, shipping,
    tax} — the test set's real invoices (SuperStore template) never show tax at
    all, and only some show a discount, but all show shipping. Absent fields
    are treated as 0 rather than assumed present. See design.md D16.
    """
    discount = invoice.discount or 0.0
    shipping = invoice.shipping or 0.0
    tax = invoice.tax or 0.0
    expected_total = round(invoice.subtotal - discount + shipping + tax, 2)
    if abs(expected_total - invoice.total) > AMOUNT_TOLERANCE:
        return [
            Flag(
                field="total",
                reason=f"subtotal - discount + shipping + tax = {expected_total} but total is {invoice.total}",
                layer="business",
                severity="error",
            )
        ]
    return []


def check_date_order(invoice: Invoice, **_) -> list[Flag]:
    """invoice_date should not be after due_date."""
    if invoice.due_date and invoice.invoice_date > invoice.due_date:
        return [
            Flag(
                field="due_date",
                reason=f"due date {invoice.due_date} is before invoice date {invoice.invoice_date}",
                layer="business",
                severity="warning",
            )
        ]
    return []


def check_duplicate_invoice_number(
    invoice: Invoice, seen_ids: set[str] | None = None, **_
) -> list[Flag]:
    """
    Duplicate invoice number within the current batch (caller passes the set
    via context). The kwarg is the schema-agnostic `seen_ids` — validate.py
    passes the same kwarg name to every schema's rules regardless of what the
    "id" actually is (invoice_number here, transaction_id for a receipt);
    only this function knows which of its own fields that maps to.
    """
    if seen_ids is not None and invoice.invoice_number in seen_ids:
        return [
            Flag(
                field="invoice_number",
                reason=f"invoice number {invoice.invoice_number} already seen in this batch",
                layer="business",
                severity="warning",
            )
        ]
    return []


# The invoice schema's full business rule set, in the shape schema_registry.py
# expects: list[Callable[..., list[Flag]]]. Order doesn't matter — all rules
# run and their flags are concatenated.
INVOICE_BUSINESS_RULES = [
    check_line_items_sum,
    check_total_arithmetic,
    check_date_order,
    check_duplicate_invoice_number,
]


def validate_business(invoice: Invoice, seen_ids: set[str] | None = None) -> list[Flag]:
    """
    Run all invoice business rules. Returns a list of business-rule flags.
    Empty list == clean. Does NOT raise — issues are surfaced for a human,
    not blocked.

    This function is a convenience wrapper for direct/test use. The
    orchestrator path instead calls each rule in INVOICE_BUSINESS_RULES via
    schema_registry.py, so a second schema doesn't need a matching wrapper
    function — just its own rule list.
    """
    flags: list[Flag] = []
    for rule in INVOICE_BUSINESS_RULES:
        flags.extend(rule(invoice, seen_ids=seen_ids))
    return flags


def failure_reason(flags: list[Flag]) -> str:
    """Turn business flags into a short message to feed back into the model on retry."""
    return "; ".join(f"{f.field}: {f.reason}" for f in flags if f.severity == "error")


# Arithmetic flags implicate a GROUP of fields, not just the one named in the
# flag. A subtotal mismatch could be caused by the subtotal OR the line items
# it's supposed to sum — retrying subtotal alone does nothing if the line
# items were the actual mistake, since re-validating against the same wrong
# line items reproduces the same mismatch. See design.md D6.
RETRY_GROUPS: dict[str, list[str]] = {
    "subtotal": ["line_items", "subtotal"],
    "total": ["subtotal", "discount", "shipping", "tax", "total"],
}


def retry_field_groups(flags: list[Flag]) -> set[str]:
    """
    Which fields need re-extraction, expanded to full dependency groups for
    arithmetic flags. Schema-layer / non-arithmetic flags (missing field)
    stay single-field since they have no dependency to expand.
    """
    fields: set[str] = set()
    for f in flags:
        if f.severity != "error":
            continue
        fields.update(RETRY_GROUPS.get(f.field, [f.field]))
    return fields
