"""Tests for repeat-attempt detection (PR 7 of platform safety floor).

Locks the §10 Q5 (Luna sign-off):
  - Threshold 5 blocks in 60s → WARNING log + Prometheus counter.
  - Anonymous (user_id=None) calls are skipped (can't attribute).
  - Shadow rows are excluded via the enforcement_mode filter.
  - SQL failure → best-effort log, None return (chat hot path
    already returned the refusal).
  - Detection does NOT auto-ban (operator-driven response only).
"""
from __future__ import annotations

import logging
import uuid
from unittest.mock import MagicMock

import pytest

from app.services.platform_safety_rate_limit import (
    REPEAT_ATTEMPT_THRESHOLD,
    REPEAT_ATTEMPT_WINDOW_SECONDS,
    check_repeat_attempts,
)


def test_threshold_constants_match_design():
    """Locked: 5 blocks in 60s per design §10 Q5. If these change,
    review the runbook + alert thresholds."""
    assert REPEAT_ATTEMPT_THRESHOLD == 5
    assert REPEAT_ATTEMPT_WINDOW_SECONDS == 60


def test_anonymous_user_returns_none():
    """No user_id → can't attribute → skip. Defensive against
    accidentally flagging a whole tenant when a probe is
    unattributable."""
    db = MagicMock()
    res = check_repeat_attempts(
        db, tenant_id=uuid.uuid4(), user_id=None,
    )
    assert res is None
    # The query is never even issued
    assert db.query.call_count == 0


def test_under_threshold_returns_none():
    """4 blocks in 60s → still under, no alert."""
    db = MagicMock()
    db.query.return_value.filter.return_value.scalar.return_value = 4
    res = check_repeat_attempts(
        db, tenant_id=uuid.uuid4(), user_id=uuid.uuid4(),
    )
    assert res is None


def test_at_threshold_returns_count_and_logs(caplog):
    """At threshold → WARNING log + return count."""
    db = MagicMock()
    db.query.return_value.filter.return_value.scalar.return_value = 5
    with caplog.at_level(logging.WARNING):
        res = check_repeat_attempts(
            db, tenant_id=uuid.uuid4(), user_id=uuid.uuid4(),
        )
    assert res == 5
    assert any(
        "PLATFORM_SAFETY_REPEAT_ATTEMPT" in r.message
        for r in caplog.records
    )


def test_over_threshold_returns_count(caplog):
    """Over threshold → log + count."""
    db = MagicMock()
    db.query.return_value.filter.return_value.scalar.return_value = 12
    with caplog.at_level(logging.WARNING):
        res = check_repeat_attempts(
            db, tenant_id=uuid.uuid4(), user_id=uuid.uuid4(),
        )
    assert res == 12


def test_sql_failure_returns_none(caplog):
    """SQL failure → best-effort log + None. Refusal already fired
    upstream; this check is bookkeeping."""
    from sqlalchemy.exc import SQLAlchemyError

    db = MagicMock()
    db.query.return_value.filter.return_value.scalar.side_effect = (
        SQLAlchemyError("simulated DB transient")
    )
    with caplog.at_level(logging.WARNING):
        res = check_repeat_attempts(
            db, tenant_id=uuid.uuid4(), user_id=uuid.uuid4(),
        )
    assert res is None
    assert any(
        "rate_limit: count query failed" in r.message
        for r in caplog.records
    )


def test_filters_shadow_rows():
    """Source-asserts the filter clause excludes shadow rows
    (matches the operator-counter exclusion in PR 3)."""
    import inspect
    from app.services import platform_safety_rate_limit

    src = inspect.getsource(
        platform_safety_rate_limit.check_repeat_attempts,
    )
    assert 'enforcement_mode == "enforced"' in src, (
        "repeat-attempt check must exclude shadow rows — the §12 #7 "
        "shadow-mode promise is they don't count against the user."
    )


def test_filters_by_user_and_tenant():
    """Filter MUST scope to (tenant_id, user_id). Otherwise a probe
    by one user would alert across the whole tenant."""
    import inspect
    from app.services import platform_safety_rate_limit

    src = inspect.getsource(
        platform_safety_rate_limit.check_repeat_attempts,
    )
    assert "tenant_id == tenant_id" in src
    assert "user_id == user_id" in src


def test_window_is_60_seconds():
    """Source-asserts the time window matches the constant. Catches
    a future regression where the window drifts from the constant
    without updating the documentation."""
    import inspect
    from app.services import platform_safety_rate_limit

    src = inspect.getsource(
        platform_safety_rate_limit.check_repeat_attempts,
    )
    assert "REPEAT_ATTEMPT_WINDOW_SECONDS" in src


# ── IO integration ──────────────────────────────────────────────────


def test_io_block_path_invokes_repeat_attempt_check(monkeypatch):
    """When tier 1+2 blocks, the IO wrapper must call
    check_repeat_attempts AFTER writing the audit row. Otherwise
    the new block wouldn't be counted."""
    from app.services import platform_safety_io
    from app.services.platform_safety import PlatformSafetyVerdict

    monkeypatch.setattr(
        platform_safety_io, "consult",
        lambda m: PlatformSafetyVerdict.block(
            category="bulk_malware", detection_tier=1,
            trigger_id="t1-test",
        ),
    )

    called = {"n": 0}

    def _spy(db, **kw):
        called["n"] += 1
        return None

    monkeypatch.setattr(
        "app.services.platform_safety_rate_limit.check_repeat_attempts",
        _spy,
    )

    db = MagicMock()
    verdict = platform_safety_io.consult_with_audit(
        db,
        tenant_id=uuid.uuid4(),
        agent_id=uuid.uuid4(),
        session_id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        message="some malware-ish text",
    )
    assert verdict.decision == "block"
    assert called["n"] == 1, (
        "consult_with_audit must call check_repeat_attempts on block"
    )


def test_io_block_path_swallows_rate_limit_crash(monkeypatch):
    """If check_repeat_attempts raises, the user-facing refusal
    still fires. Bookkeeping must not break the chat hot path."""
    from app.services import platform_safety_io
    from app.services.platform_safety import PlatformSafetyVerdict

    monkeypatch.setattr(
        platform_safety_io, "consult",
        lambda m: PlatformSafetyVerdict.block(
            category="bulk_malware", detection_tier=1,
            trigger_id="t1-test",
        ),
    )

    def _crash(db, **kw):
        raise RuntimeError("simulated rate-limit module crash")

    monkeypatch.setattr(
        "app.services.platform_safety_rate_limit.check_repeat_attempts",
        _crash,
    )

    db = MagicMock()
    verdict = platform_safety_io.consult_with_audit(
        db,
        tenant_id=uuid.uuid4(),
        agent_id=uuid.uuid4(),
        session_id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        message="some malware-ish text",
    )
    assert verdict.decision == "block"
