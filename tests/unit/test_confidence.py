"""
Unit tests for confidence.py — heuristic (never LLM-self-reported) confidence
scoring. No API calls needed.
"""

from confidence import (
    CONFIDENCE_THRESHOLD,
    FAILED,
    HIGH,
    RETRIED_BUT_PASSED,
    low_confidence_flags,
    score_fields,
)
from schema import Flag

FIELD_NAMES = ["vendor_name", "subtotal", "total"]


def test_score_fields_clean_field_scores_high():
    scores = score_fields(FIELD_NAMES, business_flags=[], retried_fields=set())
    assert scores == {"vendor_name": HIGH, "subtotal": HIGH, "total": HIGH}


def test_score_fields_flagged_field_scores_failed():
    flags = [Flag(field="total", reason="bad math", layer="business", severity="error")]
    scores = score_fields(FIELD_NAMES, business_flags=flags, retried_fields=set())
    assert scores["total"] == FAILED
    assert scores["vendor_name"] == HIGH


def test_score_fields_retried_field_scores_between_high_and_failed():
    scores = score_fields(FIELD_NAMES, business_flags=[], retried_fields={"total"})
    assert scores["total"] == RETRIED_BUT_PASSED
    assert FAILED < RETRIED_BUT_PASSED < HIGH


def test_score_fields_flag_takes_priority_over_retried():
    # A field that was retried but STILL has an unresolved error flag should
    # score as FAILED, not RETRIED_BUT_PASSED — the flag means the retry
    # didn't actually fix it.
    flags = [Flag(field="total", reason="still wrong", layer="business", severity="error")]
    scores = score_fields(FIELD_NAMES, business_flags=flags, retried_fields={"total"})
    assert scores["total"] == FAILED


def test_low_confidence_flags_below_threshold():
    scores = {"total": FAILED, "vendor_name": HIGH}
    flags = low_confidence_flags(scores)
    assert len(flags) == 1
    assert flags[0].field == "total"
    assert flags[0].severity == "warning"


def test_low_confidence_flags_none_below_threshold():
    scores = {"vendor_name": HIGH, "subtotal": HIGH}
    assert low_confidence_flags(scores) == []


def test_confidence_threshold_is_between_failed_and_high():
    assert FAILED < CONFIDENCE_THRESHOLD < HIGH
