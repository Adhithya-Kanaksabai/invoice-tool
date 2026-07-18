"""
Unit tests for schema_validate.py — schema-agnostic structural validation.
No API calls needed.
"""

from schema_validate import validate_schema

VALID_RAW = {
    "vendor_name": "SuperStore",
    "customer_name": "Aaron Hawkins",
    "invoice_number": "37425",
    "invoice_date": "2012-10-24",
    "currency": "USD",
    "line_items": [{"description": "Item", "quantity": 4, "unit_price": 49.41, "amount": 197.63}],
    "subtotal": 197.63,
    "discount": 39.53,
    "shipping": 2.84,
    "total": 160.94,
}


def test_validate_schema_accepts_well_formed_invoice():
    instance, flags = validate_schema(VALID_RAW, "invoice-v1")
    assert instance is not None
    assert flags == []


def test_validate_schema_rejects_missing_required_field():
    raw = {k: v for k, v in VALID_RAW.items() if k != "vendor_name"}
    instance, flags = validate_schema(raw, "invoice-v1")
    assert instance is None
    assert any(f.layer == "schema" and f.severity == "error" for f in flags)


def test_validate_schema_rejects_bad_type():
    raw = {**VALID_RAW, "subtotal": "not-a-number"}
    instance, flags = validate_schema(raw, "invoice-v1")
    assert instance is None
    assert any(f.field == "subtotal" for f in flags)


def test_validate_schema_flags_empty_required_field():
    raw = {**VALID_RAW, "vendor_name": ""}
    instance, flags = validate_schema(raw, "invoice-v1")
    assert instance is not None  # parses fine structurally, empty string is still a str
    assert any(f.field == "vendor_name" and f.reason == "required field is empty" for f in flags)


def test_validate_schema_unknown_schema_id_raises():
    import pytest

    with pytest.raises(KeyError):
        validate_schema(VALID_RAW, "purchase-order-v1")
