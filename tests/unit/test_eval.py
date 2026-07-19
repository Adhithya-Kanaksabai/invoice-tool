"""
Unit tests for eval.py's scoring logic — pure functions, no API calls needed.
Verifies score_document generalizes correctly across both registered
schemas (D17) and that a deliberate mismatch is actually caught.
"""


from eval import _values_match, score_document
from schema import Invoice, Receipt


def test_values_match_numeric_within_tolerance():
    assert _values_match(10.001, 10.0)
    assert not _values_match(10.02, 10.0)


def test_values_match_string_case_insensitive():
    assert _values_match("SuperStore", "superstore")
    assert not _values_match("SuperStore", "Other Co")


def test_values_match_both_none():
    assert _values_match(None, None)
    assert not _values_match(None, "value")
    assert not _values_match("value", None)


def _make_ground_truth_invoice() -> dict:
    return {
        "vendor_name": "Bright Software Co",
        "customer_name": "Dana Kim",
        "invoice_number": "INV-1001",
        "invoice_date": "2024-02-01",
        "due_date": "2024-03-01",
        "currency": "USD",
        "line_items": [
            {
                "description": "Annual SaaS License",
                "quantity": 1,
                "unit_price": 1200.00,
                "amount": 1200.00,
            }
        ],
        "subtotal": 1200.00,
        "discount": None,
        "shipping": None,
        "tax": 96.00,
        "total": 1296.00,
    }


def test_score_document_perfect_invoice_scores_100_percent():
    gt = _make_ground_truth_invoice()
    invoice = Invoice(**gt)
    correct, total = score_document(invoice, gt, "line_items")
    assert correct == total


def test_score_document_catches_one_wrong_field():
    gt = _make_ground_truth_invoice()
    bad = {**gt, "total": 999.0}
    invoice = Invoice(**bad)
    correct, total = score_document(invoice, gt, "line_items")
    assert correct == total - 1


def test_score_document_catches_wrong_line_item_count():
    gt = _make_ground_truth_invoice()
    bad = {
        **gt,
        "line_items": gt["line_items"]
        + [{"description": "Extra", "quantity": 1, "unit_price": 1, "amount": 1}],
    }
    invoice = Invoice(**bad)
    correct, total = score_document(invoice, gt, "line_items")
    assert correct < total


def test_score_document_works_for_receipt_schema_too():
    gt = {
        "merchant_name": "Corner Cafe",
        "transaction_id": "TX-1",
        "transaction_date": "2024-03-15",
        "payment_method": "Card",
        "currency": "USD",
        "items": [{"description": "Latte", "quantity": 1, "unit_price": 4.5, "amount": 4.5}],
        "subtotal": 4.5,
        "tax": None,
        "tip": None,
        "total": 4.5,
    }
    receipt = Receipt(**gt)
    correct, total = score_document(receipt, gt, "items")
    assert correct == total
