import os
import pytest
from sqlalchemy import create_engine, inspect, text


@pytest.fixture
def engine():
    return create_engine(os.environ["DATABASE_URL"])


SCOPED_TABLES = [
    "knowledge_entities",
    "commitment_records",
    "goal_records",
    "agent_memories",
    "behavioral_signals",
]


@pytest.mark.parametrize("table", SCOPED_TABLES)
def test_table_has_visibility_columns(engine, table):
    cols = {c["name"]: c for c in inspect(engine).get_columns(table)}
    assert "visibility" in cols, f"{table} missing visibility column"
    assert "visible_to" in cols, f"{table} missing visible_to column"

    visibility_col = cols["visibility"]
    # NOT NULL with default 'tenant_wide' preserves existing rows.
    assert visibility_col["nullable"] is False, (
        f"{table}.visibility must be NOT NULL"
    )
    default = visibility_col.get("default") or ""
    assert "tenant_wide" in str(default), (
        f"{table}.visibility default must be 'tenant_wide', got {default!r}"
    )


@pytest.mark.parametrize(
    "table",
    ["knowledge_entities", "agent_memories"],
)
def test_table_has_owner_agent_slug(engine, table):
    cols = {c["name"] for c in inspect(engine).get_columns(table)}
    assert "owner_agent_slug" in cols, (
        f"{table} missing owner_agent_slug column"
    )


@pytest.mark.parametrize(
    "index_name",
    [
        "idx_knowledge_entities_tenant_visibility_owner",
        "idx_knowledge_entities_visible_to_gin",
        "idx_commitments_tenant_visibility_owner",
        "idx_agent_memories_tenant_visibility_owner",
    ],
)
def test_visibility_index_exists(engine, index_name):
    with engine.connect() as c:
        result = c.execute(
            text(
                "SELECT indexname FROM pg_indexes WHERE indexname = :name"
            ),
            {"name": index_name},
        ).first()
        assert result is not None, f"index {index_name} not found"
