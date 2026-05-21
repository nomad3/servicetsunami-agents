"""Tests for Phase 3a bodies of skill_eval_activities."""
from __future__ import annotations

import uuid
from unittest.mock import MagicMock, patch

import pytest

from app.workflows.activities import skill_eval_activities as sea


def _row(**kwargs):
    """Build a SQLAlchemy-Row-like object for the test mocks."""
    m = MagicMock()
    for k, v in kwargs.items():
        setattr(m, k, v)
    return m


def test_persist_returns_missing_row_when_no_db_row(monkeypatch):
    """No row matching the (iteration_run_id, eval_id, with_skill)
    tuple → return status='missing_row' without raising. The parent
    workflow counts these as failures but the iteration continues."""
    fake_db = MagicMock()
    fake_db.execute.return_value.first.return_value = None
    monkeypatch.setattr(sea, "SessionLocal", lambda: fake_db, raising=False)
    import sys
    fake_module = MagicMock()
    fake_module.SessionLocal = lambda: fake_db
    monkeypatch.setitem(sys.modules, "app.db.session", fake_module)

    out = sea.persist_run_artifacts(
        iteration_run_id=str(uuid.uuid4()),
        eval_id=str(uuid.uuid4()),
        with_skill=True,
    )
    assert out["status"] == "missing_row"
    assert out["run_id"] is None


def test_persist_skips_re_dispatch_when_already_terminal(monkeypatch):
    """A concurrent retry might find the row already in 'ok'/'error'/
    'timeout'. We MUST NOT re-dispatch — that would create a duplicate
    Temporal child workflow and burn quota."""
    run_id = uuid.uuid4()
    fake_row = _row(
        run_id=run_id, iteration=1, status="ok",
        skill_id=uuid.uuid4(), prompt="hi",
        tenant_id=uuid.uuid4(), skill_name="grep",
    )
    fake_db = MagicMock()
    fake_db.execute.return_value.first.return_value = fake_row
    monkeypatch.setattr(sea, "SessionLocal", lambda: fake_db, raising=False)
    import sys
    fake_module = MagicMock()
    fake_module.SessionLocal = lambda: fake_db
    monkeypatch.setitem(sys.modules, "app.db.session", fake_module)

    # eval_runner should NOT be touched
    fake_eval_runner = MagicMock()
    fake_eval_runner.TERMINAL_STATUSES = ("ok", "error", "timeout")
    monkeypatch.setitem(
        sys.modules,
        "app.services.skill_creator.eval_runner",
        fake_eval_runner,
    )
    monkeypatch.setitem(
        sys.modules, "app.services.skill_creator", MagicMock(eval_runner=fake_eval_runner),
    )

    out = sea.persist_run_artifacts(
        iteration_run_id=str(uuid.uuid4()),
        eval_id=str(uuid.uuid4()),
        with_skill=True,
    )
    assert out["status"] == "ok"
    fake_eval_runner._run_one.assert_not_called()


def test_aggregate_iteration_empty_returns_zeros(monkeypatch):
    fake_db = MagicMock()
    fake_db.execute.return_value.all.return_value = []
    monkeypatch.setattr(sea, "SessionLocal", lambda: fake_db, raising=False)
    import sys
    fake_module = MagicMock()
    fake_module.SessionLocal = lambda: fake_db
    monkeypatch.setitem(sys.modules, "app.db.session", fake_module)

    out = sea.aggregate_iteration(
        iteration_run_id=str(uuid.uuid4()),
        skill_id=str(uuid.uuid4()),
        iteration=1,
    )
    assert out["total_runs"] == 0
    assert out["with_skill_ok"] == 0
    assert out["baseline_ok"] == 0
    assert out["with_skill_mean_timing_ms"] is None
    assert out["baseline_mean_timing_ms"] is None


def test_aggregate_iteration_counts_ok_vs_failed_per_leg(monkeypatch):
    rows = [
        _row(with_skill=True, status="ok", timing_ms=1000),
        _row(with_skill=True, status="ok", timing_ms=2000),
        _row(with_skill=True, status="error", timing_ms=None),
        _row(with_skill=False, status="ok", timing_ms=1500),
        _row(with_skill=False, status="timeout", timing_ms=None),
    ]
    fake_db = MagicMock()
    fake_db.execute.return_value.all.return_value = rows
    monkeypatch.setattr(sea, "SessionLocal", lambda: fake_db, raising=False)
    import sys
    fake_module = MagicMock()
    fake_module.SessionLocal = lambda: fake_db
    monkeypatch.setitem(sys.modules, "app.db.session", fake_module)

    out = sea.aggregate_iteration(
        iteration_run_id=str(uuid.uuid4()),
        skill_id=str(uuid.uuid4()),
        iteration=1,
    )
    assert out["total_runs"] == 5
    assert out["with_skill_ok"] == 2
    assert out["with_skill_failed"] == 1
    assert out["baseline_ok"] == 1
    assert out["baseline_failed"] == 1
    # mean of [1000, 2000] = 1500
    assert out["with_skill_mean_timing_ms"] == 1500.0
    # only one baseline success with timing → mean = 1500
    assert out["baseline_mean_timing_ms"] == 1500.0


def test_aggregate_iteration_skips_timing_when_null(monkeypatch):
    """A run with status=ok but timing_ms=None (legacy data) is counted
    as a success but doesn't contaminate the mean."""
    rows = [
        _row(with_skill=True, status="ok", timing_ms=None),
        _row(with_skill=True, status="ok", timing_ms=2000),
    ]
    fake_db = MagicMock()
    fake_db.execute.return_value.all.return_value = rows
    monkeypatch.setattr(sea, "SessionLocal", lambda: fake_db, raising=False)
    import sys
    fake_module = MagicMock()
    fake_module.SessionLocal = lambda: fake_db
    monkeypatch.setitem(sys.modules, "app.db.session", fake_module)

    out = sea.aggregate_iteration(
        iteration_run_id=str(uuid.uuid4()),
        skill_id=str(uuid.uuid4()),
        iteration=1,
    )
    assert out["with_skill_ok"] == 2
    # mean of [2000] only — the None row doesn't drag it down
    assert out["with_skill_mean_timing_ms"] == 2000.0
