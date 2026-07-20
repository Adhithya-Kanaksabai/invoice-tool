"""
persistence.py — writes pipeline results to the DB, plus the two read-side
lookups other workers need (content-hash cache check, cross-run natural-id
dedup).

Deliberately NOT a Worker in the workers=[...] list. orchestrator.py's own
execution history (PipelineResult.history) and outcome
(PipelineResult.status / .reason) live OUTSIDE `state` — they're local to
run_pipeline() and only ever handed back on the PipelineResult it returns.
A worker inside the list can never see them without either duplicating that
bookkeeping into state (leaking generic orchestration data into domain
state) or losing full-fidelity history. Called by app.py/eval.py right after
run_pipeline() returns instead — mirrors ingest.py's own "not a Worker
itself" reasoning: there's no retry/validation decision to make here, just
something that needs data that's only cleanly available once the pipeline
finishes.

Write-side (persist_pipeline_result) failures are LOUD — this is the system
of record, not an optional signal like the OCR cross-check this project
tried and removed (see GOD_FILE.md). Silently losing an extracted invoice is
worse than a visible error, so this raises rather than swallowing; callers
decide how to surface that.

Read-side (find_cached_document, check_natural_id_exists) is the opposite:
if the DB is unreachable, both degrade to "no cache hit" / "can't confirm
duplicate" rather than blocking extraction or validation over what's really
just an optimization/extra signal, not the system of record.
"""

from __future__ import annotations

from datetime import date, datetime

from db import get_session
from models import Document, DocumentConfidence, DocumentFlag, PipelineRun
from orchestrator import PipelineResult
from schema_registry import get_schema


def find_cached_document(content_hash: str) -> dict | None:
    """
    Returns the stored `data` JSON (document.model_dump(mode="json")) for
    the most recent document with this content_hash, or None if there's no
    match or the DB isn't reachable. See ingest.py::compute_content_hash for
    what gets hashed.

    Orders by id, not created_at: two inserts microseconds apart can land on
    the same wall-clock timestamp at datetime.utcnow()'s resolution (this
    was caught by a real test failure, not a hypothetical) — the
    autoincrement id is the only field guaranteed to reflect insert order.
    """
    try:
        with get_session() as session:
            doc = (
                session.query(Document)
                .filter_by(content_hash=content_hash)
                .order_by(Document.id.desc())
                .first()
            )
            return doc.data if doc else None
    except Exception:
        return None


def check_natural_id_exists(schema_id: str, natural_id: str | None) -> bool:
    """
    True if a document with this (schema_id, natural_id) pair was already
    persisted in a PRIOR run — the cross-run upgrade over
    business_validate.py's in-memory seen_ids, which only catches duplicates
    within the current batch. Degrades to False on any DB error: a missed
    duplicate flag is safer than crashing validation over it.
    """
    if not natural_id:
        return False
    try:
        with get_session() as session:
            return (
                session.query(Document)
                .filter_by(schema_id=schema_id, natural_id=natural_id)
                .first()
                is not None
            )
    except Exception:
        return False


def _build_correction_history(final_state: dict) -> dict:
    """
    What the agentic Correction Worker actually did, if it fired — richer
    than a boolean (which fields, the model's own rationale, whether the
    deterministic fallback path fired), but deliberately scoped to what
    retry.py's WorkerResult already puts in state. A full turn-by-turn
    transcript (every reexamine call, before/after value per field) would
    need retry.py itself instrumented to retain that instead of discarding
    it once the loop moves on — a real future improvement, not built this
    round.
    """
    retried_fields = final_state.get("retried_fields")
    if not retried_fields:
        return {"fired": False}
    return {
        "fired": True,
        "retried_fields": sorted(retried_fields),
        "note": final_state.get("correction_note", ""),
        "used_fallback": bool(final_state.get("correction_used_fallback")),
    }


def persist_pipeline_result(
    result: PipelineResult,
    original_filename: str,
    content_hash: str,
    started_at: datetime,
) -> None:
    """
    Writes one pipeline run (and, if extraction produced a document, its
    document + flags + confidence scores) to the DB. Raises on failure — see
    module docstring for why this doesn't swallow errors.
    """
    schema_id = result.final_state.get("schema_id", "unknown")
    finished_at = datetime.utcnow()

    with get_session() as session:
        run = PipelineRun(
            schema_id=schema_id,
            original_filename=original_filename,
            content_hash=content_hash,
            status=result.status,
            reason=result.reason,
            worker_history=result.history,
            correction_history=_build_correction_history(result.final_state),
            started_at=started_at,
            finished_at=finished_at,
        )
        session.add(run)
        session.flush()  # assigns run.id within the open transaction, before final commit

        if result.status != "ok" or "document" not in result.final_state:
            return

        doc_schema = get_schema(schema_id)
        document = result.final_state["document"]
        data = document.model_dump(mode="json")

        raw_date = data.get(doc_schema.date_field)
        document_row = Document(
            run_id=run.id,
            schema_id=schema_id,
            data=data,
            original_filename=original_filename,
            content_hash=content_hash,
            natural_id=data.get(doc_schema.natural_id_field),
            party_name=data.get(doc_schema.party_name_field),
            total=data.get("total"),  # "total" is the one field name both schemas already share
            document_date=date.fromisoformat(raw_date) if raw_date else None,
            created_at=finished_at,
        )
        session.add(document_row)
        session.flush()

        for flag in result.final_state.get("flags", []):
            session.add(
                DocumentFlag(
                    document_id=document_row.id,
                    field=flag.field,
                    reason=flag.reason,
                    layer=flag.layer,
                    severity=flag.severity,
                )
            )
        for field_name, score in result.final_state.get("confidence", {}).items():
            session.add(
                DocumentConfidence(document_id=document_row.id, field=field_name, score=score)
            )
