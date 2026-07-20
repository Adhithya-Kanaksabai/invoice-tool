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
    """
    from models import Base

    Base.metadata.create_all(engine)
