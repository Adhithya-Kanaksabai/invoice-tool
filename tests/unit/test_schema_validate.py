"""
Unit tests for schema_validate.py — schema-agnostic structural validation.
No API calls needed.

Covers BOTH registered schemas on purpose: schema_validate.py's whole design
claim (D13/D17) is that it's schema-agnostic — it takes a schema_id and works
for any model in the registry without modification. That claim is only
actually tested if the same structural checks are exercised against a second,
differently-shaped schema (receipt-v1: `items` not `line_items`, a
`merchant`/`transaction` shape rather than a `vendor`/`customer` one), not
just invoice-v1. Before these receipt cases existed, the genericity was
asserted by the eval set alone, never by a fast unit test.
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

VALID_RECEIPT_RAW = {
    "merchant_name": "Corner Cafe",
    "transaction_id": "TX-1",
    "transaction_date": "2024-03-15",
    "payment_method": "Card",
    "currency": "USD",
    "items": [{"description": "Latte", "quantity": 1, "unit_price": 4.5, "amount": 4.5}],
    "subtotal": 4.5,
    "tax": 0.36,
    "tip": 0.5,
    "total": 5.36,
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


# --- the same structural checks, against the second registered schema -------


def test_validate_schema_accepts_well_formed_receipt():
    instance, flags = validate_schema(VALID_RECEIPT_RAW, "receipt-v1")
    assert instance is not None
    assert flags == []


def test_validate_schema_rejects_missing_required_receipt_field():
    # merchant_name is receipt-v1's structurally-required field, the receipt
    # analogue of invoice's vendor_name — its absence must be a schema error.
    raw = {k: v for k, v in VALID_RECEIPT_RAW.items() if k != "merchant_name"}
    instance, flags = validate_schema(raw, "receipt-v1")
    assert instance is None
    assert any(f.layer == "schema" and f.severity == "error" for f in flags)


def test_validate_schema_rejects_bad_type_on_receipt():
    raw = {**VALID_RECEIPT_RAW, "subtotal": "not-a-number"}
    instance, flags = validate_schema(raw, "receipt-v1")
    assert instance is None
    assert any(f.field == "subtotal" for f in flags)


def test_validate_schema_flags_empty_required_receipt_field():
    raw = {**VALID_RECEIPT_RAW, "merchant_name": ""}
    instance, flags = validate_schema(raw, "receipt-v1")
    assert instance is not None  # empty string still parses as a str, same as invoice
    assert any(f.field == "merchant_name" and f.reason == "required field is empty" for f in flags)


def test_validate_schema_accepts_receipt_with_no_transaction_date():
    # transaction_date is Optional on Receipt (unlike Invoice.invoice_date) --
    # a real phone-photo receipt can be too blurry for a date to be legible at
    # all (the CORD-v2 benchmark finding). A receipt with no date must validate
    # structurally, NOT raise a schema error, or the whole fix is undone at
    # this layer.
    raw = {k: v for k, v in VALID_RECEIPT_RAW.items() if k != "transaction_date"}
    instance, flags = validate_schema(raw, "receipt-v1")
    assert instance is not None
    assert flags == []


def test_validate_schema_rejects_receipt_missing_items():
    # `items` is receipt-v1's list field (the analogue of invoice's
    # line_items) and is structurally required -- a receipt with no items key
    # at all must fail to parse, proving the generic validator reads the
    # SECOND schema's own required set, not a hardcoded invoice one.
    raw = {k: v for k, v in VALID_RECEIPT_RAW.items() if k != "items"}
    instance, flags = validate_schema(raw, "receipt-v1")
    assert instance is None
    assert any(f.field == "items" for f in flags)
