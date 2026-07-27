"""
Unit tests for db.py::init_db's additive-column reconciler — the Streamlit
Cloud safety net that adds model columns missing from an already-existing
table (since create_all only ever creates missing tables, never columns).
"""

import sqlite3

from sqlalchemy import create_engine, text


def _make_old_documents_table(path: str) -> None:
    """A `documents` table as it was BEFORE the review-loop columns existed,
    with one row already in it — the exact shape a stale Streamlit Cloud
    SQLite file would have after a redeploy."""
    con = sqlite3.connect(path)
    con.execute(
        "CREATE TABLE documents ("
        "id INTEGER PRIMARY KEY, run_id INTEGER, schema_id VARCHAR, data JSON, "
        "original_filename VARCHAR, content_hash VARCHAR, natural_id VARCHAR, "
        "party_name VARCHAR, total FLOAT, document_date DATE, created_at DATETIME)"
    )
    con.execute(
        "INSERT INTO documents (schema_id, data, original_filename, content_hash, created_at) "
        "VALUES ('invoice-v1', '{}', 'old.pdf', 'h1', '2026-07-01 00:00:00')"
    )
    con.commit()
    con.close()


def test_init_db_adds_missing_review_columns_to_an_existing_table(tmp_path, monkeypatch):
    db_path = tmp_path / "old.db"
    _make_old_documents_table(str(db_path))

    engine = create_engine(f"sqlite:///{db_path}")
    import db as db_module

    monkeypatch.setattr(db_module, "engine", engine)

    # The failure this prevents: without the reconciler, the review-loop
    # columns would be absent and a review query would crash at runtime.
    db_module.init_db()

    con = sqlite3.connect(str(db_path))
    cols = {r[1] for r in con.execute("PRAGMA table_info(documents)")}
    row = con.execute("SELECT review_status, corrected_data, reviewed_at FROM documents").fetchone()
    con.close()

    assert {"review_status", "corrected_data", "reviewed_at"} <= cols
    # The pre-existing row must backfill via the column's server_default, not
    # end up NULL (review_status is NOT NULL).
    assert row == ("pending", None, None)


def test_init_db_is_idempotent_when_columns_already_exist(tmp_path, monkeypatch):
    # Running it twice must be a clean no-op the second time — the reconciler
    # only ever adds what's missing.
    db_path = tmp_path / "fresh.db"
    engine = create_engine(f"sqlite:///{db_path}")
    import db as db_module

    monkeypatch.setattr(db_module, "engine", engine)

    db_module.init_db()  # creates everything fresh (all columns present)
    db_module.init_db()  # must not raise trying to re-add existing columns

    with engine.connect() as conn:
        count = conn.execute(text("SELECT count(*) FROM documents")).scalar()
    assert count == 0
