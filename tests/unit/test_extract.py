"""
Unit tests for extract.py's graceful-failure helper — no API calls needed.
"""

import json

from pydantic import ValidationError

from extract import _describe_ingest_error, _describe_last_error, extraction_worker
from llm_usage import TOKENS_KEY
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


# --- token accounting wiring ------------------------------------------------


class _FakeUsage:
    def __init__(self, prompt, candidates, total):
        self.prompt_token_count = prompt
        self.candidates_token_count = candidates
        self.total_token_count = total


class _FakeResponse:
    def __init__(self, text, usage):
        self.text = text
        self.usage_metadata = usage


class _FakeModels:
    """Returns `bad_first` unparseable responses before a good one, so the
    internal retry loop runs and its token cost is observable."""

    def __init__(self, payload, bad_first=0):
        self.payload = payload
        self.bad_first = bad_first
        self.calls = 0

    def generate_content(self, **kwargs):
        self.calls += 1
        if self.calls <= self.bad_first:
            return _FakeResponse("not json at all", _FakeUsage(1000, 5, 1005))
        return _FakeResponse(json.dumps(self.payload), _FakeUsage(1000, 200, 1200))


class _FakeClient:
    def __init__(self, models):
        self.models = models


def _valid_invoice_payload() -> dict:
    return {
        "vendor_name": "Bright Software Co",
        "customer_name": "Dana Kim",
        "invoice_number": "INV-1001",
        "invoice_date": "2024-02-01",
        "line_items": [
            {"description": "License", "quantity": 1, "unit_price": 100.0, "amount": 100.0}
        ],
        "subtotal": 100.0,
        "total": 100.0,
    }


def _run_extraction(monkeypatch, models) -> dict:
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    monkeypatch.setattr("extract.genai.Client", lambda **kwargs: _FakeClient(models))
    monkeypatch.setattr("extract.time.sleep", lambda _seconds: None)  # no real backoff waits
    monkeypatch.setattr("extract._image_parts", lambda _pages: [])  # no real page images needed
    state = {
        "file_path": "whatever.pdf",
        "schema_id": "invoice-v1",
        "pages": ["dummy-page"],
        "content_hash": "deadbeef",
        "skip_cache": True,
    }
    return extraction_worker(state)


def test_extraction_worker_threads_token_counts_into_state(monkeypatch):
    result = _run_extraction(monkeypatch, _FakeModels(_valid_invoice_payload()))
    assert result.status == "ok"
    assert result.state[TOKENS_KEY] == {
        "prompt": 1000,
        "candidates": 200,
        "total": 1200,
        "calls": 1,
    }


def test_extraction_worker_sums_tokens_across_internal_retries(monkeypatch):
    # Two unparseable responses then a good one: all three burned quota, so
    # all three must be counted, or the cost-per-document number is a lie.
    models = _FakeModels(_valid_invoice_payload(), bad_first=2)
    result = _run_extraction(monkeypatch, models)

    assert result.status == "ok"
    assert models.calls == 3
    assert result.state[TOKENS_KEY]["calls"] == 3
    assert result.state[TOKENS_KEY]["prompt"] == 3000
    assert result.state[TOKENS_KEY]["total"] == 1005 + 1005 + 1200


def test_extraction_worker_reports_tokens_even_when_extraction_fails(monkeypatch):
    # Every attempt fails -> status "failed", but the quota was still spent.
    models = _FakeModels(_valid_invoice_payload(), bad_first=99)
    result = _run_extraction(monkeypatch, models)

    assert result.status == "failed"
    assert result.state[TOKENS_KEY]["calls"] == 3  # MAX_ATTEMPTS
