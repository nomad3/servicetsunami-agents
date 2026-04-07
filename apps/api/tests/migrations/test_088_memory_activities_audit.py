import os
import pytest
from sqlalchemy import create_engine, inspect


@pytest.fixture
def engine():
    return create_engine(os.environ["DATABASE_URL"])


def test_memory_activities_has_workflow_id_column(engine):
    cols = {c["name"] for c in inspect(engine).get_columns("memory_activities")}
    assert "workflow_id" in cols
    # Existing columns we rely on:
    assert "event_type" in cols
    assert "description" in cols
    assert "source" in cols
    assert "metadata" in cols  # mapped to event_metadata in ORM
    assert "workflow_run_id" in cols
