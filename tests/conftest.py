"""
conftest.py — shared pytest fixtures.

`db_session` gives persistence tests a fresh, isolated in-memory SQLite
database per test (create_all straight from models.py, no Alembic needed for
tests — see db.py::init_db's docstring for why). Tests that need
persistence.py's functions to hit THIS session rather than the real
DATABASE_URL-configured one monkeypatch db.SessionLocal to point at it.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from models import Base  # noqa: E402


@pytest.fixture
def db_session(monkeypatch):
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine, expire_on_commit=False)

    import db as db_module

    monkeypatch.setattr(db_module, "SessionLocal", session_factory)

    yield session_factory

    engine.dispose()
