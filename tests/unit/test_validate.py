"""
Unit tests for validate.py — the Validation Worker, including the
document-type mismatch gate (checked before schema/business validation).
"""

from datetime import date

from schema import Invoice, LineItem
from validate import validation_worker


def make_invoice(**overrides) -> Invoice:
    defaults = dict(
        vendor_name="SuperStore",
        customer_name="Test Customer",
        invoice_number="1",
        invoice_date=date(2012, 1, 1),
        line_items=[LineItem(description="Item", quantity=1, unit_price=10.0, amount=10.0)],
        subtotal=10.0,
        total=10.0,
    )
    defaults.update(overrides)
    return Invoice(**defaults)


def test_validation_worker_short_circuits_on_document_type_mismatch():
    invoice = make_invoice(
        document_type_match=False, document_type_note="looks like a school marksheet"
    )
    result = validation_worker({"schema_id": "invoice-v1", "document": invoice})

    assert result.status == "failed"
    assert result.state["document_type_mismatch"] is True
    assert "Invoice" in result.reason
    assert "school marksheet" in result.reason


def test_validation_worker_document_type_mismatch_skips_schema_and_business_validation():
    # subtotal is deliberately wrong (would normally flag an error) — the
    # mismatch gate should fire first and never reach that check.
    invoice = make_invoice(subtotal=999.0, document_type_match=False)
    result = validation_worker({"schema_id": "invoice-v1", "document": invoice})

    assert result.status == "failed"
    assert "flags" not in result.state


def test_validation_worker_default_document_type_match_is_unaffected():
    invoice = make_invoice()  # document_type_match defaults to True
    result = validation_worker({"schema_id": "invoice-v1", "document": invoice})

    assert result.status == "ok"
    assert "document_type_mismatch" not in result.state
    assert result.state["flags"] == []