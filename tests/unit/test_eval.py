"""
Unit tests for eval.py's scoring logic — pure functions, no API calls needed.
Verifies score_document generalizes correctly across both registered
schemas (D17) and that a deliberate mismatch is actually caught.
"""

import json

from eval import (
    MANIFEST_PATH,
    TESTS_DIR,
    _accuracy,
    _percentile,
    _summarize_latency,
    _values_match,
    load_manifest,
    score_document,
)
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


# --- dataset stratification + observability aggregation ---------------------


def test_manifest_covers_every_sample_file_exactly():
    """
    The manifest is only useful if it's complete: a sample file missing from
    it silently lands in the 'uncategorized' bucket and quietly stops being
    measured as part of its real category.
    """
    manifest = load_manifest()
    on_disk = {
        p.name
        for directory in (TESTS_DIR / "sample_invoices", TESTS_DIR / "sample_receipts")
        if directory.exists()
        for p in directory.iterdir()
        if p.suffix.lower() in {".pdf", ".jpg", ".jpeg", ".png", ".webp"}
    }
    assert on_disk - set(manifest) == set(), "sample files missing from tests/manifest.json"
    assert set(manifest) - on_disk == set(), "manifest lists files that no longer exist"


def test_manifest_uses_only_declared_categories():
    declared = set(json.loads(MANIFEST_PATH.read_text())["categories"])
    assert declared == {"clean_synthetic", "degraded_synthetic", "real_photo", "web_template"}
    assert set(load_manifest().values()) <= declared


def test_load_manifest_tolerates_a_missing_file(tmp_path):
    # Stratification is a reporting nicety, not a precondition for measuring.
    assert load_manifest(tmp_path / "nope.json") == {}


def test_percentile_returns_an_actually_observed_value():
    values = [1.0, 2.0, 3.0, 4.0, 100.0]
    assert _percentile(values, 50) == 3.0
    assert _percentile(values, 95) == 100.0
    assert _percentile(values, 100) == 100.0
    assert _percentile([], 50) is None


def test_percentile_handles_a_single_sample():
    assert _percentile([2.5], 95) == 2.5


def test_summarize_latency_reports_n_avg_and_tails():
    summary = _summarize_latency([1.0, 2.0, 3.0])
    assert summary["n"] == 3
    assert summary["avg_s"] == 2.0
    assert summary["max_s"] == 3.0
    assert _summarize_latency([]) is None


def test_accuracy_is_none_rather_than_zero_when_nothing_was_scored():
    # "we scored nothing" and "we scored everything wrong" are different facts.
    assert _accuracy(0, 0) is None
    assert _accuracy(0, 4) == 0.0
    assert _accuracy(3, 4) == 0.75
