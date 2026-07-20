"""
Unit tests for schema_registry.py — display_name and the always-excluded
bookkeeping fields (field_status, source_note, document_type_match,
document_type_note) that confidence.py/report.py must never treat as
scoreable content fields.
"""

from schema_registry import REGISTRY, get_scalar_field_names, get_schema


def test_all_registered_schemas_have_a_display_name():
    for schema_id, doc_schema in REGISTRY.items():
        assert doc_schema.display_name, f"{schema_id} is missing display_name"


def test_invoice_display_name():
    assert get_schema("invoice-v1").display_name == "Invoice"


def test_receipt_display_name():
    assert get_schema("receipt-v1").display_name == "Receipt"


def test_get_scalar_field_names_excludes_document_type_bookkeeping_fields():
    doc_schema = get_schema("invoice-v1")
    scalar_fields = get_scalar_field_names(doc_schema)

    assert "document_type_match" not in scalar_fields
    assert "document_type_note" not in scalar_fields
    assert "field_status" not in scalar_fields
    assert "source_note" not in scalar_fields
    # a real content field should still be present
    assert "vendor_name" in scalar_fields