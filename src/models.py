"""
models.py — SQLAlchemy tables for persisted pipeline runs and documents.

JSON payload + promoted columns, deliberately NOT per-document-type tables:
this project's whole thesis is schema-agnostic (see schema_registry.py), and
a separate `invoices` / `receipts` table would silently reintroduce the
hardcoded-per-type coupling the rest of the codebase specifically avoids. The
full extracted document is stored as-is in `Document.data` (whatever
`document.model_dump(mode="json")` produced, for whichever schema); only the
handful of fields actually queried get promoted to real indexed columns —
which fields those are per schema is schema_registry.py's
natural_id_field/party_name_field/date_field, not hardcoded here either.

No UNIQUE constraint on content_hash or (schema_id, natural_id): duplicates
must be STORED and FLAGGED for human review, not rejected at the DB layer —
a constraint violation would crash the pipeline on exactly the case it's
supposed to handle gracefully. Indexed for fast lookup, not constrained.
"""

from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import Date, DateTime, Float, ForeignKey, Index, String
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship
from sqlalchemy.types import JSON


class Base(DeclarativeBase):
    pass


class PipelineRun(Base):
    """
    One row per pipeline invocation (one uploaded/sampled file), regardless
    of outcome — a failed extraction still gets a row, so "this file was
    attempted and failed" is itself a queryable fact, not silently dropped.
    """

    __tablename__ = "pipeline_runs"

    id: Mapped[int] = mapped_column(primary_key=True)
    schema_id: Mapped[str] = mapped_column(String, index=True)
    original_filename: Mapped[str] = mapped_column(String)
    content_hash: Mapped[str] = mapped_column(String, index=True)
    status: Mapped[str] = mapped_column(String)  # "ok" | "failed", mirrors PipelineResult.status
    reason: Mapped[str | None] = mapped_column(String, nullable=True)
    worker_history: Mapped[list] = mapped_column(JSON, default=list)  # PipelineResult.history, verbatim
    # See src/persistence.py's _build_correction_history docstring for the
    # exact shape and what it deliberately does NOT capture (a full
    # turn-by-turn transcript would need retry.py instrumentation this round
    # didn't add).
    correction_history: Mapped[dict] = mapped_column(JSON, default=dict)
    started_at: Mapped[datetime] = mapped_column(DateTime, index=True)
    finished_at: Mapped[datetime] = mapped_column(DateTime)

    documents: Mapped[list[Document]] = relationship(
        back_populates="run", cascade="all, delete-orphan"
    )


class Document(Base):
    """The extracted document itself — one row per successfully-extracted document."""

    __tablename__ = "documents"

    id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("pipeline_runs.id"), index=True)
    schema_id: Mapped[str] = mapped_column(String, index=True)
    data: Mapped[dict] = mapped_column(JSON)  # document.model_dump(mode="json"), full fidelity
    original_filename: Mapped[str] = mapped_column(String)
    content_hash: Mapped[str] = mapped_column(String, index=True)

    # Promoted for querying — see schema_registry.DocumentSchema's
    # natural_id_field / party_name_field / date_field for which document
    # field each of these came from, per schema_id.
    natural_id: Mapped[str | None] = mapped_column(String, nullable=True, index=True)
    party_name: Mapped[str | None] = mapped_column(String, nullable=True, index=True)
    total: Mapped[float | None] = mapped_column(Float, nullable=True)
    document_date: Mapped[date | None] = mapped_column(Date, nullable=True, index=True)

    created_at: Mapped[datetime] = mapped_column(DateTime)

    run: Mapped[PipelineRun] = relationship(back_populates="documents")
    flags: Mapped[list[DocumentFlag]] = relationship(
        back_populates="document", cascade="all, delete-orphan"
    )
    confidence_scores: Mapped[list[DocumentConfidence]] = relationship(
        back_populates="document", cascade="all, delete-orphan"
    )

    __table_args__ = (
        # The literal dedup lookup (business_validate.py's cross-run
        # duplicate rule queries by exactly this pair) — indexed as a pair,
        # not just as two separate single-column indexes.
        Index("ix_documents_schema_natural_id", "schema_id", "natural_id"),
    )


class DocumentFlag(Base):
    __tablename__ = "flags"

    id: Mapped[int] = mapped_column(primary_key=True)
    document_id: Mapped[int] = mapped_column(ForeignKey("documents.id"), index=True)
    field: Mapped[str] = mapped_column(String)
    reason: Mapped[str] = mapped_column(String)
    layer: Mapped[str] = mapped_column(String)  # "schema" | "business", same as schema.Flag
    severity: Mapped[str] = mapped_column(String)  # "error" | "warning"

    document: Mapped[Document] = relationship(back_populates="flags")


class DocumentConfidence(Base):
    __tablename__ = "confidence_scores"

    id: Mapped[int] = mapped_column(primary_key=True)
    document_id: Mapped[int] = mapped_column(ForeignKey("documents.id"), index=True)
    field: Mapped[str] = mapped_column(String)
    score: Mapped[float] = mapped_column(Float)

    document: Mapped[Document] = relationship(back_populates="confidence_scores")
