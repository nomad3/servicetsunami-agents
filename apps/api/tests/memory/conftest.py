"""Memory test fixtures.

Integration tests use a real Postgres because pgvector queries don't
work on SQLite/in-memory. The DB pointed at by DATABASE_URL is the
production tenant DB at localhost:8003 — tests MUST rollback at the end
and never commit mutations to production rows.
"""
import os

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker


def pytest_collection_modifyitems(config, items):
    """Mark every test under tests/memory/ as `integration`.

    Memory tests hit pgvector and require a live Postgres; the default unit
    run (`-m "not integration"`) should skip them. The integration job in
    `.github/workflows/tests.yaml` runs them against a real database.
    """
    for item in items:
        if "tests/memory/" in str(item.path).replace("\\", "/"):
            item.add_marker(pytest.mark.integration)


@pytest.fixture
def db_session():
    """Yield a Session bound to the production DB. Rolls back at teardown."""
    url = os.environ.get(
        "DATABASE_URL",
        "postgresql://postgres:postgres@localhost:8003/agentprovision",
    )
    engine = create_engine(url, future=True)
    Session = sessionmaker(bind=engine, expire_on_commit=False)
    db = Session()
    try:
        yield db
    finally:
        # Defensive: even if a test forgot to rollback, this guarantees
        # nothing it INSERTed/UPDATEd persists. The recall_count UPDATE
        # in recall() does call db.commit() — but those are reads-with-
        # side-effects on production rows that the chat path also
        # mutates, so this is acceptable per the plan.
        try:
            db.rollback()
        except Exception:
            pass
        db.close()
        engine.dispose()
