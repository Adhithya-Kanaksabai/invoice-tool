"""
Unit tests for extract.py's graceful-failure helper — no API calls needed.
"""

import json

from pydantic import ValidationError

from extract import _describe_last_error
from schema import Invoice


def _make_validation_error() -> ValidationError:
    try:
        Invoice.model_validate({})
    except ValidationError as e:
        return e
    raise AssertionError("expected Invoice.model_validate({}) to raise")


def test_describe_last_error_summarizes_validation_error_fields():
    message = _describe_last_error(_make_validation_error())
    assert "invoice_number" in message
    assert "customer_name" in message
    # must not leak pydantic's raw multi-line dump
    assert "validation errors for Invoice" not in message
    assert "type=string_type" not in message
    assert "\n" not in message


def test_describe_last_error_handles_json_decode_error():
    try:
        json.loads("not json")
    except json.JSONDecodeError as e:
        message = _describe_last_error(e)
    assert "valid JSON" in message


def test_describe_last_error_handles_generic_exception():
    message = _describe_last_error(ValueError("timeout talking to gemini"))
    assert message == "timeout talking to gemini"


def test_describe_last_error_handles_none():
    assert _describe_last_error(None) == "unknown error"


def test_describe_last_error_truncates_long_field_lists():
    # a schema with many missing required fields — message should stay short
    message = _describe_last_error(_make_validation_error())
    assert "more" in message or len(message.split(", ")) <= 6