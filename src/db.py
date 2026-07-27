"""
db.py — the one place a DB connection gets made.

Deliberately DB-agnostic: DATABASE_URL from the environment (reusing the
existing python-dotenv pattern already used for GEMINI_API_KEY), defaulting
to a local SQLite file so `git clone && pip install && streamlit run` still
works with zero setup. Postgres becomes the real target via docker-compose
(a later phase) — SQLAlchemy's dialect abstraction is what makes that a
config change, not a rewrite, the same "swap via env var, not code" shape
this project already used once for the Gemini model name.

get_session() is a plain context manager, not a FastAPI-style dependency —
there's no request lifecycle here, callers (persistence.py, extract.py's
content-hash cache check) just need "give me a session, close it when I'm
done."
"""

from __future__ import annotations

import os
from contextlib import contextmanager

from dotenv import load_dotenv
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

load_dotenv()

DEFAULT_SQLITE_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), "invoice_tool.db")
DATABASE_URL = os.environ.get("DATABASE_URL", f"sqlite:///{DEFAULT_SQLITE_PATH}")

# check_same_thread=False: SQLite-only quirk, needed because Streamlit can
# call into this from a different thread than the one that created the
# engine. Postgres's driver has no such restriction, so this connect_arg is
# conditional rather than always-on.
_connect_args = {"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {}

engine = create_engine(DATABASE_URL, connect_args=_connect_args)
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)


@contextmanager
def get_session():
    session: Session = SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def init_db() -> None:
    """
    Dev/test convenience: create tables directly from the models, no
    migration history. Alembic (see alembic/) is the real schema-evolution
    path for anything that already has data in it — this is only for a
    fresh SQLite file or a throwaway test database.

    Then reconcile additive column changes (see _ensure_additive_columns).
    """
    from models import Base

    Base.metadata.create_all(engine)
    _ensure_additive_columns()


def _ensure_additive_columns() -> None:
    """
    Add any model-defined column that's missing from an already-existing table.

    Why this exists: create_all() above only ever creates missing TABLES — it
    never adds a missing COLUMN to a table that already exists. On Streamlit
    Community Cloud there's no pre-start hook to run Alembic, AND the app's
    SQLite file is not tracked in git, so a redeploy that `git pull`s new code
    can leave the OLD database file in place — a file whose `documents` table
    predates, say, the review-loop columns. Without this, the app would import
    fine and then crash at query time with "no such column: review_status".

    Additive and idempotent by construction: it only ever ADDs columns the
    models declare and the table lacks — never drops, never alters, never
    touches data. Alembic remains the real migration path for local/Docker
    (and for anything non-additive); this is the narrow safety net for the one
    environment that can't run it. A column added here uses its model-declared
    server_default so existing rows backfill correctly (that's why
    Document.review_status carries a server_default, not just a Python default).
    """
    from sqlalchemy import inspect, text
    from sqlalchemy.schema import CreateColumn

    from models import Base

    inspector = inspect(engine)
    existing_tables = set(inspector.get_table_names())

    with engine.begin() as conn:
        for table in Base.metadata.sorted_tables:
            if table.name not in existing_tables:
                continue  # create_all just made it — already fully current
            existing_columns = {c["name"] for c in inspector.get_columns(table.name)}
            for column in table.columns:
                if column.name in existing_columns:
                    continue
                column_ddl = CreateColumn(column).compile(dialect=engine.dialect)
                conn.execute(text(f"ALTER TABLE {table.name} ADD COLUMN {column_ddl}"))
