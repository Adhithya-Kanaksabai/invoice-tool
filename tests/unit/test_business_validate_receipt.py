"""
Unit tests for business_validate_receipt.py — the second schema's own,
independent business rules (per D15/D17). No API calls needed.
"""

from datetime import date

import pytest

from business_validate_receipt import (
    check_receipt_duplicate_transaction_id,
    check_receipt_items_sum,
    check_receipt_total_arithmetic,
    retry_field_groups,
)
from schema import Flag, Receipt, ReceiptItem


def make_receipt(**overrides) -> Receipt:
    defaults = dict(
        merchant_name="Corner Cafe",
        transaction_date=date(2024, 3, 15),
        items=[ReceiptItem(description="Latte", quantity=1, unit_price=4.5, amount=4.5)],
        subtotal=4.5,
        total=4.5,
    )
    defaults.update(overrides)
    return Receipt(**defaults)


def test_check_receipt_items_sum_passes_when_consistent():
    receipt = make_receipt(subtotal=4.5)
    assert check_receipt_items_sum(receipt) == []


def test_check_receipt_items_sum_flags_mismatch():
    receipt = make_receipt(subtotal=999.0)
    flags = check_receipt_items_sum(receipt)
    assert len(flags) == 1
    assert flags[0].field == "subtotal"
    assert flags[0].severity == "error"


@pytest.mark.parametrize(
    "tax,tip,total,should_flag",
    [
        (None, None, 4.5, False),  # no tax/tip at all
        (0.5, None, 5.0, False),  # tax only
        (None, 1.0, 5.5, False),  # tip only
        (0.5, 1.0, 6.0, False),  # both
        (None, None, 999.0, True),  # wrong total
    ],
)
def test_check_receipt_total_arithmetic(tax, tip, total, should_flag):
    receipt = make_receipt(tax=tax, tip=tip, total=total)
    flags = check_receipt_total_arithmetic(receipt)
    assert (len(flags) == 1) == should_flag


def test_check_receipt_duplicate_transaction_id_flags_seen_id():
    receipt = make_receipt(transaction_id="TX-1")
    flags = check_receipt_duplicate_transaction_id(receipt, seen_ids={"TX-1"})
    assert len(flags) == 1
    assert flags[0].severity == "warning"


def test_check_receipt_duplicate_transaction_id_passes_without_id():
    # A receipt with no transaction_id at all shouldn't be flagged — there's
    # nothing to compare against a "seen" set.
    receipt = make_receipt(transaction_id=None)
    assert check_receipt_duplicate_transaction_id(receipt, seen_ids={"TX-1"}) == []


def test_retry_field_groups_expands_total_mismatch():
    flags = [Flag(field="total", reason="mismatch", layer="business", severity="error")]
    assert retry_field_groups(flags) == {"subtotal", "tax", "tip", "total"}


def test_retry_field_groups_expands_subtotal_mismatch():
    flags = [Flag(field="subtotal", reason="mismatch", layer="business", severity="error")]
    assert retry_field_groups(flags) == {"items", "subtotal"}
