"""Regression tests for DynamicWorkflowInDB serialization.

A direct-SQL insert left run_count/installs/rating as NULL on one row, and
because the response schema required non-null ints, /dynamic-workflows/templates/browse
returned HTTP 500 — the empty Templates tab. These tests pin the Optional
defaults so a future refactor can't re-introduce the regression.
"""
import os
os.environ["TESTING"] = "True"

from types import SimpleNamespace
from datetime import datetime
import uuid

from app.schemas.dynamic_workflow import DynamicWorkflowInDB


def _row(**overrides):
    """Minimal row shape that DynamicWorkflowInDB can pull from via from_attributes."""
    base = dict(
        id=uuid.uuid4(),
        tenant_id=uuid.uuid4(),
        name="Cardiac Report Generator",
        description=None,
        definition={"steps": []},
        version=1,
        status="active",
        trigger_config={"type": "manual"},
        tags=[],
        tier="native",
        public=False,
        run_count=None,
        last_run_at=None,
        avg_duration_ms=None,
        success_rate=None,
        created_at=datetime.utcnow(),
        updated_at=None,
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def test_serializes_with_null_run_count():
    # Regression: pre-fix this raised ResponseValidationError on run_count = None
    obj = DynamicWorkflowInDB.model_validate(_row(run_count=None))
    assert obj.run_count == 0


def test_serializes_with_null_optional_stats():
    # All four stats columns default to None and must not crash
    obj = DynamicWorkflowInDB.model_validate(_row(
        last_run_at=None,
        avg_duration_ms=None,
        success_rate=None,
        updated_at=None,
    ))
    assert obj.last_run_at is None
    assert obj.avg_duration_ms is None
    assert obj.success_rate is None
    assert obj.updated_at is None


def test_serializes_real_values_unchanged():
    now = datetime.utcnow()
    obj = DynamicWorkflowInDB.model_validate(_row(
        run_count=42,
        last_run_at=now,
        avg_duration_ms=1500,
        success_rate=0.95,
    ))
    assert obj.run_count == 42
    assert obj.last_run_at == now
    assert obj.avg_duration_ms == 1500
    assert obj.success_rate == 0.95


def test_browse_response_tolerates_one_bad_row():
    # Simulates /templates/browse: serialize a list where one row has NULL
    # counters alongside normal rows. Before the fix, the whole list 500'd.
    rows = [
        _row(name="Daily Briefing", run_count=10),
        _row(name="Cardiac Report Generator", run_count=None),  # the problem row
        _row(name="Lead Pipeline", run_count=5),
    ]
    serialized = [DynamicWorkflowInDB.model_validate(r) for r in rows]
    assert len(serialized) == 3
    assert [s.run_count for s in serialized] == [10, 0, 5]
