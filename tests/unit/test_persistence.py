"""
Unit tests for persistence.py — no API calls, no real Postgres needed. Uses
the db_session fixture (tests/conftest.py) to point db.py's session factory
at a fresh in-memory SQLite database per test.
"""

from datetime import date, datetime

from orchestrator import PipelineResult
from persistence import check_natural_id_exists, find_cached_document, persist_pipeline_result
from schema import Flag, Invoice, LineItem


def make_invoice(**overrides) -> Invoice:
    defaults = dict(
        vendor_name="SuperStore",
        customer_name="Test Customer",
        invoice_number="INV-9001",
        invoice_date=date(2024, 1, 1),
        line_items=[LineItem(description="Item", quantity=1, unit_price=10.0, amount=10.0)],
        subtotal=10.0,
        total=10.0,
    )
    defaults.update(overrides)
    return Invoice(**defaults)


def make_result(document, flags=None, confidence=None, history=None) -> PipelineResult:
    return PipelineResult(
        final_state={
            "schema_id": "invoice-v1",
            "document": document,
            "flags": flags or [],
            "confidence": confidence or {},
        },
        status="ok",
        history=history or ["extraction_worker", "validation_worker", "report_worker"],
    )


# --- basic write + read round trip -----------------------------------------


def test_persist_pipeline_result_writes_run_and_document(db_session):
    result = make_result(make_invoice())
    persist_pipeline_result(
        result,
        original_filename="invoice_1.pdf",
        content_hash="hash-abc",
        started_at=datetime.utcnow(),
    )

    cached = find_cached_document("hash-abc")
    assert cached is not None
    assert cached["invoice_number"] == "INV-9001"
    assert cached["vendor_name"] == "SuperStore"


def test_persist_failed_run_has_no_document(db_session):
    result = PipelineResult(
        final_state={"schema_id": "invoice-v1", "extraction_failed": True},
        status="failed",
        history=["extraction_worker"],
        reason="extraction failed after 3 attempts: timeout",
    )
    # Must not raise even though there's no document to persist.
    persist_pipeline_result(
        result,
        original_filename="broken.pdf",
        content_hash="hash-broken",
        started_at=datetime.utcnow(),
    )
    assert find_cached_document("hash-broken") is None


def test_persist_stores_flags_and_confidence(db_session):
    flags = [Flag(field="total", reason="bad math", layer="business", severity="error")]
    confidence = {"total": 0.3, "vendor_name": 0.95}
    result = make_result(make_invoice(), flags=flags, confidence=confidence)

    persist_pipeline_result(
        result, original_filename="x.pdf", content_hash="hash-flags", started_at=datetime.utcnow()
    )

    import db
    from models import Document, DocumentConfidence, DocumentFlag

    with db.get_session() as session:
        doc = session.query(Document).filter_by(content_hash="hash-flags").first()
        stored_flags = session.query(DocumentFlag).filter_by(document_id=doc.id).all()
        stored_scores = session.query(DocumentConfidence).filter_by(document_id=doc.id).all()

    assert len(stored_flags) == 1
    assert stored_flags[0].field == "total"
    assert stored_flags[0].severity == "error"
    assert {s.field: s.score for s in stored_scores} == confidence


# --- content_hash cache lookup ----------------------------------------------


def test_find_cached_document_returns_none_when_absent(db_session):
    assert find_cached_document("never-seen-hash") is None


def test_find_cached_document_returns_most_recent(db_session):
    persist_pipeline_result(
        make_result(make_invoice(total=100.0)),
        original_filename="a.pdf",
        content_hash="hash-dup",
        started_at=datetime.utcnow(),
    )
    persist_pipeline_result(
        make_result(make_invoice(total=200.0)),
        original_filename="a.pdf",
        content_hash="hash-dup",
        started_at=datetime.utcnow(),
    )

    cached = find_cached_document("hash-dup")
    assert cached["total"] == 200.0  # the later of the two, not the first


# --- cross-run natural_id dedup --------------------------------------------


def test_check_natural_id_exists_false_when_nothing_persisted(db_session):
    assert check_natural_id_exists("invoice-v1", "INV-9001") is False


def test_check_natural_id_exists_true_after_a_separate_prior_run(db_session):
    # This is the actual behavior that didn't exist before this round:
    # business_validate.py's old check_duplicate_invoice_number only saw
    # duplicates within ONE run's in-memory seen_ids set. Two fully separate
    # persist_pipeline_result calls simulate two separate pipeline runs.
    persist_pipeline_result(
        make_result(make_invoice(invoice_number="INV-9001")),
        original_filename="run1.pdf",
        content_hash="hash-run1",
        started_at=datetime.utcnow(),
    )

    assert check_natural_id_exists("invoice-v1", "INV-9001") is True
    # Different schema_id with the SAME natural_id must not collide.
    assert check_natural_id_exists("receipt-v1", "INV-9001") is False
    # A genuinely different id must not false-positive.
    assert check_natural_id_exists("invoice-v1", "INV-9002") is False


def test_content_hash_and_natural_id_dedup_are_independent_signals(db_session):
    # The same file (content_hash) submitted twice, where the SECOND
    # extraction produced a different (misread) natural_id, must still be
    # caught by content_hash even though natural_id differs — proving the
    # two signals are independent, not aliases of each other.
    persist_pipeline_result(
        make_result(make_invoice(invoice_number="INV-9001")),
        original_filename="same_file.pdf",
        content_hash="hash-same-file",
        started_at=datetime.utcnow(),
    )
    # Simulate a second, mis-extracted read of the identical bytes.
    persist_pipeline_result(
        make_result(make_invoice(invoice_number="INV-DIFFERENT")),
        original_filename="same_file.pdf",
        content_hash="hash-same-file",
        started_at=datetime.utcnow(),
    )

    cached = find_cached_document("hash-same-file")
    assert cached["invoice_number"] == "INV-DIFFERENT"  # most recent read
    # natural_id dedup alone would NOT have caught this pair (different ids) —
    # content_hash is what catches it.
    assert check_natural_id_exists("invoice-v1", "INV-9001") is True
    assert check_natural_id_exists("invoice-v1", "INV-DIFFERENT") is True


# --- correction_history -----------------------------------------------------


def test_correction_history_empty_when_correction_never_fired(db_session):
    result = make_result(make_invoice())
    persist_pipeline_result(
        result, original_filename="clean.pdf", content_hash="hash-clean", started_at=datetime.utcnow()
    )

    import db
    from models import PipelineRun

    with db.get_session() as session:
        run = session.query(PipelineRun).filter_by(content_hash="hash-clean").first()
        assert run.correction_history == {"fired": False}


def test_correction_history_records_retried_fields_and_note(db_session):
    result = make_result(make_invoice())
    result.final_state["retried_fields"] = {"subtotal", "total"}
    result.final_state["correction_note"] = "line items were misread, corrected total"
    result.final_state["correction_used_fallback"] = False

    persist_pipeline_result(
        result,
        original_filename="corrected.pdf",
        content_hash="hash-corrected",
        started_at=datetime.utcnow(),
    )

    import db
    from models import PipelineRun

    with db.get_session() as session:
        run = session.query(PipelineRun).filter_by(content_hash="hash-corrected").first()
        assert run.correction_history["fired"] is True
        assert run.correction_history["retried_fields"] == ["subtotal", "total"]
        assert run.correction_history["note"] == "line items were misread, corrected total"
        assert run.correction_history["used_fallback"] is False


# --- graceful degradation on read-side --------------------------------------


def test_find_cached_document_degrades_to_none_on_db_error(monkeypatch):
    # persistence.py does `from db import get_session`, so the patch target
    # must be persistence's own bound name, not db.get_session — patching
    # the latter wouldn't affect a name already imported by value.
    import persistence

    def _broken_session():
        raise RuntimeError("DB unreachable")

    monkeypatch.setattr(persistence, "get_session", _broken_session)
    assert find_cached_document("any-hash") is None


def test_check_natural_id_exists_degrades_to_false_on_db_error(monkeypatch):
    import persistence

    def _broken_session():
        raise RuntimeError("DB unreachable")

    monkeypatch.setattr(persistence, "get_session", _broken_session)
    assert check_natural_id_exists("invoice-v1", "INV-9001") is False
