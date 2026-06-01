"""Tests for migration 156 — Luna Supervisor gains the `learning` tool_group.

Two layers of assertion:
  1. The bundled `apps/api/app/agents/_bundled/luna/skill.md` frontmatter
     lists `learning` in `tool_groups` (file-only, fast, runs in unit
     mode).
  2. After migration 156 is applied, the DB-side `agents` row for Luna
     Supervisor on Simon's tenant has `learning` in its `tool_groups`
     jsonb array (integration-marked, requires DATABASE_URL).

The two-layer split mirrors plan §0b: Luna's effective tool_groups are
the union of frontmatter (loaded by the bundled-skill importer) AND the
DB row (authoritative for runtime gating). Drift between the two would
break Luna's runtime access to the `learning` group.
"""
from __future__ import annotations

import os
import re
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[3]
LUNA_SKILL_PATH = REPO_ROOT / "apps/api/app/agents/_bundled/luna/skill.md"
SIMON_TENANT_ID = "752626d9-8b2c-4aa2-87ef-c458d48bd38a"
EXPECTED_FRONTMATTER_TOOL_GROUPS = {
    "calendar", "email", "drive", "data", "reports", "bookings",
    "monitor", "jira", "github", "workflows", "skills", "ecommerce",
    "competitor", "knowledge", "meta", "sales", "web_research",
    "higgsfield",
    # `learning` (autonomous-learning subsystem — pre-existing) was
    # left in for back-compat after PR #728 review IMPORTANT4 split the
    # group; `luna_learn` is the new home for the 7 video→skill
    # primitives.
    "learning",
    "luna_learn",
}


def _load_luna_frontmatter() -> dict:
    content = LUNA_SKILL_PATH.read_text(encoding="utf-8")
    match = re.match(r"^---\n(.*?)\n---", content, re.DOTALL)
    assert match, f"Luna skill.md missing YAML frontmatter: {LUNA_SKILL_PATH}"
    return yaml.safe_load(match.group(1))


def test_luna_skill_md_frontmatter_includes_learning_tool_group():
    """The bundled luna/skill.md must declare `learning` in `tool_groups`.

    This is the file-loaded half of Luna's effective tool_groups. If it
    drifts away from the DB-side seed (migration 156), the bundled
    skill-importer reads a stale set and Luna can lose access to the
    learning primitives at next bundled-skill refresh.
    """
    fm = _load_luna_frontmatter()
    assert "tool_groups" in fm, (
        "luna/skill.md frontmatter missing tool_groups (added by T5.2 "
        "alongside migration 156)"
    )
    declared = set(fm["tool_groups"])
    assert "learning" in declared, (
        f"luna/skill.md tool_groups missing `learning`: {sorted(declared)}"
    )
    assert declared == EXPECTED_FRONTMATTER_TOOL_GROUPS, (
        "luna/skill.md tool_groups drifted from the migration-156 seed; "
        f"declared={sorted(declared)} expected={sorted(EXPECTED_FRONTMATTER_TOOL_GROUPS)}"
    )


@pytest.mark.integration
def test_luna_supervisor_db_row_includes_learning_tool_group():
    """After migration 156 is applied, Luna Supervisor's DB row has
    `learning` in its tool_groups jsonb array on Simon's tenant.

    Mirrors the conftest pattern in tests/migrations/: direct engine +
    raw SQL, no ORM dependency. Skipped in unit-mode runs (no
    DATABASE_URL); CI integration job runs it.
    """
    db_url = os.environ.get("DATABASE_URL", "")
    if not db_url or not db_url.startswith(("postgresql", "postgres")):
        pytest.skip("DATABASE_URL not pointing at Postgres — integration only")

    from sqlalchemy import create_engine, text

    engine = create_engine(db_url)
    with engine.connect() as c:
        # Confirm migration 156 was applied to this database.
        applied = c.execute(text(
            "SELECT 1 FROM _migrations WHERE filename = "
            "'156_luna_add_learning_tool_group.sql'"
        )).scalar()
        assert applied, (
            "migration 156_luna_add_learning_tool_group.sql not in "
            "_migrations — apply it before running this test"
        )

        row = c.execute(text(
            "SELECT tool_groups FROM agents "
            "WHERE name = 'Luna Supervisor' AND tenant_id = :tid"
        ), {"tid": SIMON_TENANT_ID}).fetchone()

    assert row is not None, (
        f"Luna Supervisor row missing on tenant {SIMON_TENANT_ID} — "
        "seed (migrations 154/155) prerequisites not applied?"
    )
    tool_groups = row[0]
    # `tool_groups` is jsonb → sqlalchemy returns a list[str].
    assert isinstance(tool_groups, list), (
        f"tool_groups column not a list: {type(tool_groups).__name__}"
    )
    assert "learning" in tool_groups, (
        f"Luna Supervisor.tool_groups missing `learning` after migration "
        f"156: {tool_groups}"
    )
    # Migration 157 splits Luna Learn primitives into the `luna_learn`
    # group (PR #728 IMPORTANT4 fix); both must be present.
    assert "luna_learn" in tool_groups, (
        f"Luna Supervisor.tool_groups missing `luna_learn` after migration "
        f"157: {tool_groups}"
    )
