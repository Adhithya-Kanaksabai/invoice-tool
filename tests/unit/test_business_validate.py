"""
Unit tests for business_validate.py — pure functions, no API calls needed.
Covers the D16 generalization (discount/shipping/tax all optional) and the
D6 retry-group expansion.
"""

from datetime import date

import pytest

from business_validate import (
    check_date_order,
    check_duplicate_invoice_number,
    check_line_items_sum,
    check_total_arithmetic,
    retry_field_groups,
)
from schema import Flag, Invoice, LineItem


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


def test_check_line_items_sum_passes_when_consistent():
    invoice = make_invoice(subtotal=10.0)
    assert check_line_items_sum(invoice) == []


def test_check_line_items_sum_flags_mismatch():
    invoice = make_invoice(subtotal=999.0)
    flags = check_line_items_sum(invoice)
    assert len(flags) == 1
    assert flags[0].field == "subtotal"
    assert flags[0].severity == "error"


@pytest.mark.parametrize(
    "discount,shipping,tax,total,should_flag",
    [
        (None, None, None, 10.0, False),  # bare subtotal = total
        (None, 2.0, None, 12.0, False),  # shipping only (no tax field on invoice, per D16)
        (2.0, 1.0, None, 9.0, False),  # discount + shipping, no tax
        (None, None, 1.0, 11.0, False),  # classic tax-only invoice
        (2.0, 1.0, 1.0, 10.0, False),  # all four present
        (None, None, None, 999.0, True),  # wrong total
    ],
)
def test_check_total_arithmetic_generalized_formula(discount, shipping, tax, total, should_flag):
    invoice = make_invoice(discount=discount, shipping=shipping, tax=tax, total=total)
    flags = check_total_arithmetic(invoice)
    assert (len(flags) == 1) == should_flag


def test_check_date_order_flags_due_before_invoice_date():
    invoice = make_invoice(invoice_date=date(2012, 6, 1), due_date=date(2012, 5, 1))
    flags = check_date_order(invoice)
    assert len(flags) == 1
    assert flags[0].severity == "warning"


def test_check_date_order_passes_when_no_due_date():
    invoice = make_invoice(due_date=None)
    assert check_date_order(invoice) == []


def test_check_duplicate_invoice_number_flags_seen_number():
    invoice = make_invoice(invoice_number="37425")
    flags = check_duplicate_invoice_number(invoice, seen_ids={"37425"})
    assert len(flags) == 1
    assert flags[0].severity == "warning"


def test_check_duplicate_invoice_number_passes_for_new_number():
    invoice = make_invoice(invoice_number="37425")
    assert check_duplicate_invoice_number(invoice, seen_ids={"other"}) == []


def test_retry_field_groups_expands_total_mismatch_to_full_dependency_group():
    flags = [Flag(field="total", reason="mismatch", layer="business", severity="error")]
    assert retry_field_groups(flags) == {"subtotal", "discount", "shipping", "tax", "total"}


def test_retry_field_groups_expands_subtotal_mismatch():
    flags = [Flag(field="subtotal", reason="mismatch", layer="business", severity="error")]
    assert retry_field_groups(flags) == {"line_items", "subtotal"}


def test_retry_field_groups_single_field_fallback_for_unmapped_field():
    flags = [Flag(field="due_date", reason="bad order", layer="business", severity="warning")]
    # warning severity is excluded entirely — only error flags drive retries
    assert retry_field_groups(flags) == set()


def test_retry_field_groups_ignores_warnings():
    flags = [
        Flag(field="invoice_number", reason="dup", layer="business", severity="warning"),
    ]
    assert retry_field_groups(flags) == set()
