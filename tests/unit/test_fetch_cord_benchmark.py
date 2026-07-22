"""
Unit tests for fetch_cord_benchmark.py's normalization logic — pure
functions, no network access, no live download. The download/write path
(main()) is exercised by actually running the script once, not by these
tests.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fetch_cord_benchmark import _build_ground_truth, _menu_items, _parse_cord_amount


def test_parse_cord_amount_strips_dot_as_thousands_separator():
    # CORD receipts use "." and "," interchangeably as thousands separators,
    # never as a decimal point -- "60.000" means 60000, not 60.0.
    assert _parse_cord_amount("60.000") == 60000.0


def test_parse_cord_amount_strips_comma_as_thousands_separator():
    assert _parse_cord_amount("28,000") == 28000.0


def test_parse_cord_amount_strips_at_sign_prefix():
    assert _parse_cord_amount("@11000") == 11000.0


def test_parse_cord_amount_takes_first_element_of_a_list():
    # A real CORD annotation quirk: subtotal_price sometimes comes back as a
    # list of duplicate strings instead of a scalar.
    assert _parse_cord_amount(["46.636", "46.636"]) == 46636.0


def test_parse_cord_amount_returns_none_for_missing_value():
    assert _parse_cord_amount(None) is None


def test_parse_cord_amount_returns_none_for_unparseable_string():
    assert _parse_cord_amount("N/A") is None


def test_menu_items_normalizes_a_single_dict_to_a_one_item_list():
    gt_parse = {"menu": {"nm": "TICKET CP", "price": "60.000"}}
    assert _menu_items(gt_parse) == [{"nm": "TICKET CP", "price": "60.000"}]


def test_menu_items_passes_through_an_existing_list():
    gt_parse = {"menu": [{"nm": "A"}, {"nm": "B"}]}
    assert _menu_items(gt_parse) == [{"nm": "A"}, {"nm": "B"}]


def test_menu_items_returns_empty_list_when_menu_absent():
    assert _menu_items({}) == []


def test_build_ground_truth_extracts_total_and_item_descriptions():
    gt_parse = {
        "total": {"total_price": "60.000"},
        "menu": {"nm": "-TICKET CP", "price": "60.000"},
    }
    gt = _build_ground_truth(gt_parse)
    assert gt == {"total": 60000.0, "items": [{"description": "-TICKET CP"}]}


def test_build_ground_truth_returns_none_when_total_unparseable():
    # A document with no scoreable total isn't worth including at all --
    # every other CORD field is even less reliable than this one.
    assert _build_ground_truth({"menu": {"nm": "X"}}) is None


def test_build_ground_truth_skips_menu_items_with_no_name():
    gt_parse = {
        "total": {"total_price": "10000"},
        "menu": [{"price": "10000"}, {"nm": "Real Item", "price": "5000"}],
    }
    gt = _build_ground_truth(gt_parse)
    assert gt["items"] == [{"description": "Real Item"}]
