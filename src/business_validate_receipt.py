"""
business_validate_receipt.py — Layer 2 domain rules for the "receipt-v1"
schema, the second document type registered in schema_registry.py.

Per D15/D17: this module is intentionally independent of business_validate.py
— a receipt's rules never call into an invoice's, even where the pattern
rhymes (both check "items sum to subtotal"). Only the SHAPE
(Callable[..., list[Flag]]) is shared. This is the actual proof that adding a
second document type doesn't require touching or reusing the first one's
domain logic.
"""

from __future__ import annotations

from schema import Flag, Receipt

AMOUNT_TOLERANCE = 0.01


def check_receipt_items_sum(receipt: Receipt, **_) -> list[Flag]:
    """Line items should sum to subtotal."""
    item_sum = round(sum(item.amount for item in receipt.items), 2)
    if abs(item_sum - receipt.subtotal) > AMOUNT_TOLERANCE:
        return [
            Flag(
                field="subtotal",
                reason=f"items sum to {item_sum} but subtotal is {receipt.subtotal}",
                layer="business",
                severity="error",
            )
        ]
    return []


def check_receipt_total_arithmetic(receipt: Receipt, **_) -> list[Flag]:
    """subtotal + tax + tip should equal total. Both tax and tip are optional (absent = 0)."""
    tax = receipt.tax or 0.0
    tip = receipt.tip or 0.0
    expected_total = round(receipt.subtotal + tax + tip, 2)
    if abs(expected_total - receipt.total) > AMOUNT_TOLERANCE:
        return [
            Flag(
                field="total",
                reason=f"subtotal + tax + tip = {expected_total} but total is {receipt.total}",
                layer="business",
                severity="error",
            )
        ]
    return []


def check_receipt_duplicate_transaction_id(
    receipt: Receipt, seen_ids: set[str] | None = None, **_
) -> list[Flag]:
    """
    Duplicate transaction ID within the current batch, if the receipt has one
    at all. Same schema-agnostic `seen_ids` kwarg validate.py passes to every
    schema's rules — see business_validate.py::check_duplicate_invoice_number.
    """
    if receipt.transaction_id and seen_ids is not None and receipt.transaction_id in seen_ids:
        return [
            Flag(
                field="transaction_id",
                reason=f"transaction id {receipt.transaction_id} already seen in this batch",
                layer="business",
                severity="warning",
            )
        ]
    return []


RECEIPT_BUSINESS_RULES = [
    check_receipt_items_sum,
    check_receipt_total_arithmetic,
    check_receipt_duplicate_transaction_id,
]


# Same D6 reasoning as invoice's RETRY_GROUPS: a subtotal mismatch could be
# the subtotal itself or the items it's supposed to sum, so both must be
# re-extracted together, or the retry can't actually resolve the mismatch.
RECEIPT_RETRY_GROUPS: dict[str, list[str]] = {
    "subtotal": ["items", "subtotal"],
    "total": ["subtotal", "tax", "tip", "total"],
}


def retry_field_groups(flags: list[Flag]) -> set[str]:
    """Same shape as business_validate.py's version — kept independent per D15."""
    fields: set[str] = set()
    for f in flags:
        if f.severity != "error":
            continue
        fields.update(RECEIPT_RETRY_GROUPS.get(f.field, [f.field]))
    return fields
