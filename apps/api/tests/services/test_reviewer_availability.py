"""Unit tests for the reviewer-availability gate.

Tests the pure availability logic with mocked Session/Agent rows.
Same constraint as test_review_circularity: SQLite can't compile
our JSONB Agent columns, so we use SimpleNamespace fakes.

Design: docs/plans/2026-05-24-review-gate-medium-followups-design.md
Motivation: gap #3 of the 2026-05-24 blameless RL experiment.
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from app.services.reviewer_availability import (
    ReviewerUnavailableError,
    UnavailabilityReason,
    check_required_reviewers,
)


TENANT = uuid.UUID("752626d9-8b2c-4aa2-87ef-c458d48bd38a")


def _agent(
    *,
    name: str,
    status: str = "production",
    review_required: bool = False,
) -> SimpleNamespace:
    return SimpleNamespace(
        id=uuid.uuid4(),
        name=name,
        status=status,
        tool_groups_review_required=review_required,
    )


def _db_returning(*agents) -> MagicMock:
    """Stub a Session whose Agent query returns the provided rows in order.

    Each subsequent ``.one_or_none()`` call returns the next agent.
    Pass ``None`` to simulate a missing row.
    """
    db = MagicMock()
    db.query.return_value.filter.return_value.one_or_none = MagicMock(
        side_effect=list(agents)
    )
    return db


# ── Happy path ───────────────────────────────────────────────────────


def test_all_healthy_returns_empty_list() -> None:
    db = _db_returning(
        _agent(name="Code Reviewer"),
        _agent(name="Substrate Sentinel"),
    )
    assert check_required_reviewers(
        db, TENANT, ["code-reviewer", "substrate-sentinel"]
    ) == []


def test_cli_platform_slugs_are_skipped() -> None:
    """claude/codex/gemini have no bundled-name mapping → no DB hit, no reason."""
    db = MagicMock()
    # No .one_or_none calls should happen — assert the query isn't invoked.
    db.query.assert_not_called()
    assert check_required_reviewers(
        db, TENANT, ["claude", "codex", "gemini"]
    ) == []


# ── Failure modes ─────────────────────────────────────────────────────


def test_missing_agent_returns_agent_missing() -> None:
    db = _db_returning(None)
    reasons = check_required_reviewers(db, TENANT, ["code-reviewer"])
    assert len(reasons) == 1
    assert reasons[0] == UnavailabilityReason(
        agent_slug="code-reviewer",
        code="agent_missing",
        detail="no Agent row found for slug 'code-reviewer' in this tenant",
    )


@pytest.mark.parametrize("status", ["draft", "deprecated"])
def test_disabled_status_returns_agent_disabled(status: str) -> None:
    db = _db_returning(_agent(name="Code Reviewer", status=status))
    reasons = check_required_reviewers(db, TENANT, ["code-reviewer"])
    assert len(reasons) == 1
    assert reasons[0].code == "agent_disabled"
    assert status in reasons[0].detail


def test_review_required_returns_review_required_unresolved() -> None:
    """This is the chicken-and-egg case from PR #705 — Code Reviewer
    + Substrate Sentinel both shipped with review_required=TRUE in
    migration 153 and cannot act as gates until cleared."""
    db = _db_returning(_agent(name="Code Reviewer", review_required=True))
    reasons = check_required_reviewers(db, TENANT, ["code-reviewer"])
    assert len(reasons) == 1
    assert reasons[0].code == "review_required_unresolved"
    assert "tool_groups_review_required=TRUE" in reasons[0].detail


def test_multiple_failures_all_returned() -> None:
    """All required reviewers are evaluated — not short-circuited."""
    db = _db_returning(
        None,  # code-reviewer missing
        _agent(name="Substrate Sentinel", review_required=True),  # in review queue
    )
    reasons = check_required_reviewers(
        db, TENANT, ["code-reviewer", "substrate-sentinel"]
    )
    assert [r.agent_slug for r in reasons] == [
        "code-reviewer", "substrate-sentinel",
    ]
    assert [r.code for r in reasons] == [
        "agent_missing", "review_required_unresolved",
    ]


# ── ReviewerUnavailableError ──────────────────────────────────────────


def test_error_str_lists_each_slug_and_code() -> None:
    err = ReviewerUnavailableError(
        [
            UnavailabilityReason("code-reviewer", "agent_missing", "..."),
            UnavailabilityReason(
                "substrate-sentinel", "review_required_unresolved", "..."
            ),
        ]
    )
    msg = str(err)
    assert "code-reviewer=agent_missing" in msg
    assert "substrate-sentinel=review_required_unresolved" in msg
