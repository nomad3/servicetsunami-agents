"""Tests for SkillEvalIterationWorkflow Phase 3 scaffold.

Uses Temporal's WorkflowEnvironment in time-skipping mode (same
harness pattern as the NightlyReflectionWorkflow tests). Activities
are mocked so we can verify the workflow shape without touching DB
or disk.
"""
from __future__ import annotations

import uuid as _uuid
from unittest.mock import AsyncMock

import pytest
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Worker

from app.workflows.skill_eval_iteration_workflow import (
    SkillEvalIterationWorkflow,
)

pytestmark = pytest.mark.asyncio


TASK_QUEUE = "test-skill-eval-iteration"


async def _run_with_activities(env: WorkflowEnvironment, *, persist_mock, aggregate_mock, legs):
    from temporalio import activity

    @activity.defn(name="skill_eval.persist_run_artifacts")
    async def _persist(iteration_run_id: str, eval_id: str, with_skill: bool) -> dict:
        return await persist_mock(iteration_run_id, eval_id, with_skill)

    @activity.defn(name="skill_eval.aggregate_iteration")
    async def _agg(iteration_run_id: str, skill_id: str, iteration: int) -> dict:
        return await aggregate_mock(iteration_run_id, skill_id, iteration)

    async with Worker(
        env.client,
        task_queue=TASK_QUEUE,
        workflows=[SkillEvalIterationWorkflow],
        activities=[_persist, _agg],
    ):
        return await env.client.execute_workflow(
            SkillEvalIterationWorkflow.run,
            args=["run-1", "skill-1", 1, legs],
            id=f"test-skill-eval-{_uuid.uuid4().hex[:12]}",
            task_queue=TASK_QUEUE,
        )


async def test_workflow_runs_persist_per_leg_then_aggregate():
    persist_mock = AsyncMock(return_value={"status": "noop_stub"})
    aggregate_mock = AsyncMock(return_value={"status": "noop_stub"})
    legs = [("eval-A", True), ("eval-A", False), ("eval-B", True)]
    async with await WorkflowEnvironment.start_time_skipping() as env:
        result = await _run_with_activities(
            env,
            persist_mock=persist_mock,
            aggregate_mock=aggregate_mock,
            legs=legs,
        )

    assert result["iteration_run_id"] == "run-1"
    assert result["legs_total"] == 3
    assert result["legs_succeeded"] == 3
    assert result["legs_failed"] == 0
    assert result["aggregated"] is True
    assert persist_mock.await_count == 3
    aggregate_mock.assert_awaited_once_with("run-1", "skill-1", 1)


async def test_workflow_counts_failed_legs_and_continues():
    """A single failing leg MUST NOT abort the iteration — other
    legs and the aggregate step still run. Phase 3 dispatch must
    survive a flaky ChatCliWorkflow child."""
    call_count = {"n": 0}

    async def _persist(run_id, eval_id, with_skill):
        call_count["n"] += 1
        if eval_id == "eval-B" and with_skill is False:
            raise RuntimeError("simulated child failure")
        return {"status": "ok"}

    persist_mock = AsyncMock(side_effect=_persist)
    aggregate_mock = AsyncMock(return_value={"status": "ok"})
    legs = [
        ("eval-A", True), ("eval-A", False),
        ("eval-B", True), ("eval-B", False),  # this one fails
    ]
    async with await WorkflowEnvironment.start_time_skipping() as env:
        result = await _run_with_activities(
            env,
            persist_mock=persist_mock,
            aggregate_mock=aggregate_mock,
            legs=legs,
        )

    assert result["legs_total"] == 4
    assert result["legs_succeeded"] == 3
    assert result["legs_failed"] == 1
    assert result["aggregated"] is True


async def test_workflow_records_aggregate_failure_without_killing_run():
    """If aggregate_iteration raises, the workflow still returns
    success-counts for the per-leg work and surfaces aggregated=False
    so the operator can re-trigger just the rollup."""
    persist_mock = AsyncMock(return_value={"status": "ok"})
    aggregate_mock = AsyncMock(side_effect=RuntimeError("rollup table missing"))
    legs = [("eval-A", True)]
    async with await WorkflowEnvironment.start_time_skipping() as env:
        result = await _run_with_activities(
            env,
            persist_mock=persist_mock,
            aggregate_mock=aggregate_mock,
            legs=legs,
        )
    assert result["legs_succeeded"] == 1
    assert result["aggregated"] is False
