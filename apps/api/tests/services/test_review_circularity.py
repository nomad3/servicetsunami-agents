"""Unit tests for the introduction-PR circularity detector.

Tests the pure detection logic with a mocked Session — we don't need a
real DB because the only DB-touching code paths are the escalation
resolver, which we exercise via fakes here. The integration test in
test_reviews_check_circularity_endpoint.py covers the wired-in
behavior.

Design: docs/plans/2026-05-24-review-gate-medium-followups-design.md
Motivation: PR #705 (gap #4 of the 2026-05-24 blameless RL experiment).
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from app.services.bundled_agents import BUNDLED_AGENTS_ROOT
from app.services.review_circularity import (
    CircularityFinding,
    _bundled_paths_for_slug,
    _strip_repo_prefix,
    detect_self_modification,
)


TENANT = uuid.UUID("752626d9-8b2c-4aa2-87ef-c458d48bd38a")


# ── Pure-function tests ──────────────────────────────────────────────


def test_bundled_paths_for_slug_returns_skill_and_dir() -> None:
    paths = _bundled_paths_for_slug("code-reviewer")
    assert paths[0] == f"{BUNDLED_AGENTS_ROOT}/code-reviewer/skill.md"
    assert paths[1] == f"{BUNDLED_AGENTS_ROOT}/code-reviewer/"


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("apps/api/app/agents/_bundled/code-reviewer/skill.md",
         "apps/api/app/agents/_bundled/code-reviewer/skill.md"),
        ("./apps/api/app/agents/_bundled/code-reviewer/skill.md",
         "apps/api/app/agents/_bundled/code-reviewer/skill.md"),
        ("/abs/path/apps/api/app/agents/_bundled/code-reviewer/skill.md",
         "apps/api/app/agents/_bundled/code-reviewer/skill.md"),
        ("docs/plans/something.md", "docs/plans/something.md"),
        # Path with `..` segment must NOT have its leading `.` characters
        # stripped — the old `lstrip("./")` form was a character-class op
        # that silently collapsed parent-dir segments.
        ("../scratch/something.md", "../scratch/something.md"),
        (".hidden/file.md", ".hidden/file.md"),
    ],
)
def test_strip_repo_prefix(raw: str, expected: str) -> None:
    assert _strip_repo_prefix(raw) == expected


# ── detect_self_modification ─────────────────────────────────────────


def _stub_db_no_escalation() -> MagicMock:
    """Agent lookup returns None — exercise the no-escalation branch."""
    db = MagicMock()
    db.query.return_value.filter.return_value.one_or_none.return_value = None
    return db


def test_skill_md_change_drops_reviewer() -> None:
    db = _stub_db_no_escalation()
    filtered, findings = detect_self_modification(
        db,
        TENANT,
        changed_files=[
            "apps/api/app/agents/_bundled/code-reviewer/skill.md",
        ],
        candidate_reviewer_slugs=["code-reviewer", "claude", "codex"],
    )

    assert "code-reviewer" not in filtered
    assert filtered == ["claude", "codex"]
    assert len(findings) == 1
    f = findings[0]
    assert f.agent_slug == "code-reviewer"
    assert f.bundled_path.endswith("/code-reviewer/skill.md")
    # No escalation since stub returns None.
    assert f.escalation_slug is None


def test_subdirectory_change_drops_reviewer() -> None:
    """A file under _bundled/<slug>/ that isn't skill.md still counts."""
    db = _stub_db_no_escalation()
    filtered, findings = detect_self_modification(
        db,
        TENANT,
        changed_files=[
            "apps/api/app/agents/_bundled/substrate-sentinel/prompts/extra.md",
        ],
        candidate_reviewer_slugs=["substrate-sentinel"],
    )
    assert filtered == []
    assert len(findings) == 1
    assert findings[0].agent_slug == "substrate-sentinel"


def test_unrelated_change_passes_through() -> None:
    db = _stub_db_no_escalation()
    filtered, findings = detect_self_modification(
        db,
        TENANT,
        changed_files=[
            "apps/api/app/services/users.py",
            "docs/plans/example.md",
        ],
        candidate_reviewer_slugs=["code-reviewer", "substrate-sentinel"],
    )
    assert filtered == ["code-reviewer", "substrate-sentinel"]
    assert findings == []


def test_cli_platform_slugs_are_never_filtered() -> None:
    """claude/codex/gemini have no bundled paths so the gate is a no-op."""
    db = _stub_db_no_escalation()
    filtered, findings = detect_self_modification(
        db,
        TENANT,
        # Even if a CLI-platform slug appears in the diff (it can't
        # have a `_bundled/claude/` dir today), the function should
        # only match if the path actually exists in the diff.
        changed_files=[
            "apps/api/app/agents/_bundled/code-reviewer/skill.md",
        ],
        candidate_reviewer_slugs=["claude", "codex", "gemini"],
    )
    assert filtered == ["claude", "codex", "gemini"]
    assert findings == []


def test_escalation_slug_resolves_via_supervisor_chain() -> None:
    """Agent → escalation_agent_id → bundled-slug lookup happy path."""
    luna_id = uuid.uuid4()
    code_reviewer_agent = SimpleNamespace(
        id=uuid.uuid4(),
        escalation_agent_id=luna_id,
        name="Code Reviewer",
    )
    luna_agent = SimpleNamespace(
        id=luna_id,
        escalation_agent_id=None,
        name="Luna",
    )

    # First query: Code Reviewer (matched by name). Second: Luna by id.
    db = MagicMock()
    one_or_none = MagicMock(
        side_effect=[code_reviewer_agent, luna_agent]
    )
    db.query.return_value.filter.return_value.one_or_none = one_or_none

    filtered, findings = detect_self_modification(
        db,
        TENANT,
        changed_files=[
            "apps/api/app/agents/_bundled/code-reviewer/skill.md",
        ],
        candidate_reviewer_slugs=["code-reviewer"],
    )
    assert filtered == []
    assert findings == [
        CircularityFinding(
            agent_slug="code-reviewer",
            bundled_path="apps/api/app/agents/_bundled/code-reviewer/skill.md",
            escalation_slug="luna",
        ),
    ]


def test_sibling_slug_does_not_match() -> None:
    """A `_bundled/code-reviewer-v2/skill.md` change must NOT match
    `code-reviewer` (trailing-slash discriminator in the prefix)."""
    db = _stub_db_no_escalation()
    filtered, findings = detect_self_modification(
        db,
        TENANT,
        changed_files=[
            "apps/api/app/agents/_bundled/code-reviewer-v2/skill.md",
        ],
        candidate_reviewer_slugs=["code-reviewer"],
    )
    assert filtered == ["code-reviewer"]
    assert findings == []


def test_pr_705_walkthrough_with_escalation() -> None:
    """Concrete: PR #705 modified BOTH Code Reviewer and Substrate
    Sentinel skill.md, plus migrations + tests. Both reviewers must
    drop out, surface Luna as the escalation target, and Luna
    remains as the only valid reviewer.

    Each circularity finding requires two queries: the agent row
    (by name) and its escalation target (by id). We sequence the
    side_effect to feed each pair in turn."""
    luna_id = uuid.uuid4()
    code_reviewer = SimpleNamespace(
        id=uuid.uuid4(), escalation_agent_id=luna_id, name="Code Reviewer",
    )
    substrate_sentinel = SimpleNamespace(
        id=uuid.uuid4(), escalation_agent_id=luna_id, name="Substrate Sentinel",
    )
    luna = SimpleNamespace(id=luna_id, escalation_agent_id=None, name="Luna")

    db = MagicMock()
    db.query.return_value.filter.return_value.one_or_none = MagicMock(
        side_effect=[
            code_reviewer, luna,            # finding 1: Code Reviewer → Luna
            substrate_sentinel, luna,       # finding 2: Substrate Sentinel → Luna
        ]
    )

    filtered, findings = detect_self_modification(
        db,
        TENANT,
        changed_files=[
            "apps/api/app/agents/_bundled/code-reviewer/skill.md",
            "apps/api/app/agents/_bundled/substrate-sentinel/skill.md",
            "apps/api/app/services/users.py",
            "apps/api/app/services/tool_groups.py",
            "apps/api/app/models/agent.py",
            "apps/api/migrations/153_review_default_true_and_readonly_split.sql",
            "apps/api/migrations/153_review_default_true_and_readonly_split.down.sql",
            "apps/api/tests/services/test_tool_groups_knowledge_readonly.py",
            "apps/api/tests/services/test_bundled_readonly_skills.py",
        ],
        candidate_reviewer_slugs=["code-reviewer", "substrate-sentinel", "luna"],
    )
    assert filtered == ["luna"]
    assert sorted(f.agent_slug for f in findings) == ["code-reviewer", "substrate-sentinel"]
    # Both findings must surface Luna as the escalation target — that's
    # the operator's routing hint and the whole point of the dataclass.
    assert all(f.escalation_slug == "luna" for f in findings)
