"""Memory test fixtures.

Integration tests use a real Postgres because pgvector queries don't
work on SQLite/in-memory.

DB selection: callers MUST set `DATABASE_URL` (or `MEMORY_TEST_DATABASE_URL`)
to a disposable test database with the pgvector extension installed. The
fallback default is `agentprovision_test` on a local Postgres — explicitly
NOT the production tenant DB. The fixture rolls back at teardown as a
belt-and-braces safety net, but this is not a license to point the suite
at production.
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


# Disposable test DB default. Override via MEMORY_TEST_DATABASE_URL or
# DATABASE_URL when running locally against your own Postgres+pgvector.
# This is intentionally NOT pointed at the production tenant DB.
_DEFAULT_TEST_DB_URL = (
    "postgresql://postgres:postgres@localhost:5432/agentprovision_test"
)


@pytest.fixture
def db_session():
    """Yield a Session bound to a disposable test DB. Rolls back at teardown."""
    url = (
        os.environ.get("MEMORY_TEST_DATABASE_URL")
        or os.environ.get("DATABASE_URL")
        or _DEFAULT_TEST_DB_URL
    )
    engine = create_engine(url, future=True)
    Session = sessionmaker(bind=engine, expire_on_commit=False)
    db = Session()
    try:
        yield db
    finally:
        # Defensive: even if a test forgot to rollback, this guarantees
        # nothing it INSERTed/UPDATEd persists. The recall_count UPDATE
        # in recall() does call db.commit() — those side-effects only
        # land in the disposable test DB.
        try:
            db.rollback()
        except Exception:
            pass
        db.close()
        engine.dispose()
