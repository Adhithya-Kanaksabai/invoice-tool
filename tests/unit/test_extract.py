"""
Unit tests for extract.py's graceful-failure helper — no API calls needed.
"""

import json

from pydantic import ValidationError

from extract import _describe_ingest_error, _describe_last_error, extraction_worker
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


class _FakePDFInfoNotInstalledError(Exception):
    """Stands in for pdf2image.exceptions.PDFInfoNotInstalledError without
    needing Poppler installed to run this test — only the class name matters
    to _describe_ingest_error's type-name check."""


def test_describe_ingest_error_flags_missing_poppler_without_leaking_internals():
    message = _describe_ingest_error(_FakePDFInfoNotInstalledError("Unable to get page count."))
    assert "Poppler" not in message  # user shouldn't need to know the dependency name
    assert "Unable to get page count" not in message  # raw exception text must not leak
    assert "try again" in message


def test_describe_ingest_error_passes_through_unsupported_type_message():
    message = _describe_ingest_error(ValueError("Unsupported file type: .docx"))
    assert message == "Unsupported file type: .docx"


def test_describe_ingest_error_handles_generic_corruption():
    message = _describe_ingest_error(OSError("cannot identify image file"))
    assert "corrupt" in message


def test_extraction_worker_returns_graceful_failure_on_ingest_error(monkeypatch):
    def _raise_missing_poppler(_path):
        raise _FakePDFInfoNotInstalledError("Unable to get page count.")

    monkeypatch.setattr("extract.load_page_images", _raise_missing_poppler)

    result = extraction_worker({"file_path": "whatever.pdf", "schema_id": "invoice-v1"})

    assert result.status == "failed"
    assert "Unable to get page count" not in (result.reason or "")
    assert "Traceback" not in (result.reason or "")
    assert "try again" in (result.reason or "")


def test_extraction_worker_returns_graceful_failure_without_api_key(monkeypatch):
    # Reproduces a real crash seen on the live deployment: os.environ["GEMINI_API_KEY"]
    # was accessed unguarded, so a missing secret raised a bare KeyError straight to the UI.
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)

    result = extraction_worker(
        {
            "file_path": "whatever.pdf",
            "schema_id": "invoice-v1",
            "pages": ["dummy-page"],  # truthy -> skips load_page_images entirely
            "content_hash": "deadbeef",  # skips compute_content_hash reading a real file
            "skip_cache": True,  # skips the DB-backed cache lookup
        }
    )

    assert result.status == "failed"
    assert "GEMINI_API_KEY" in (result.reason or "")
    assert "Traceback" not in (result.reason or "")