"""
Unit test for retry.py's guard against a missing GEMINI_API_KEY — reproduces
a real crash seen on the live deployment (raw KeyError reaching the UI)
before this fix, without needing an actual API key or model call.
"""

import os

from retry import correction_worker


def test_correction_worker_gives_up_gracefully_without_api_key(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.setattr(
        "retry._expand_retry_fields", lambda flags, retry_groups: {"total"}
    )

    state = {
        "schema_id": "invoice-v1",
        "document": object(),
        "pages": [],
        "flags": [{"field": "total", "severity": "error", "reason": "doesn't add up"}],
    }

    result = correction_worker(state)

    assert result.status == "ok"  # orchestrator ignores this worker's status entirely
    assert result.state["correction_attempted_but_failed"] is True
    assert "GEMINI_API_KEY" in result.state["correction_failure_reason"]
    assert os.environ.get("GEMINI_API_KEY") is None
