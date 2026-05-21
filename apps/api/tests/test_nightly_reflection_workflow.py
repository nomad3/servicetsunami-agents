"""Tests for the NightlyReflectionWorkflow scaffold (O2 Phase 1).

We use Temporal's WorkflowEnvironment in time-skipping mode so we can
exercise the workflow's leg sequence without a running cluster. The
activities are stubbed with mocks so we can:

  - confirm kill-switch OFF short-circuits BEFORE any other activity runs,
  - confirm kill-switch ON drives the full leg chain in order,
  - confirm the return shape carries the operator-visible reason field.

These mirror the existing CoalitionWorkflow tests' WorkflowEnvironment
pattern.
"""
from __future__ import annotations

import uuid as _uuid
from unittest.mock import AsyncMock

import pytest
from temporalio.client import Client
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Worker

from app.workflows.nightly_reflection_workflow import NightlyReflectionWorkflow

pytestmark = pytest.mark.asyncio


TASK_QUEUE = "test-nightly-reflection"
TENANT = "11111111-1111-1111-1111-111111111111"
DAY = "2026-05-19"


def _make_activity_set(*, enabled: bool):
    """Return a 5-tuple of AsyncMock activities preconfigured for a
    kill-switch state. Each mock returns the smallest valid payload
    the next leg expects."""
    check_killswitch = AsyncMock(return_value=enabled)
    gather_episodes = AsyncMock(return_value=[])
    cluster_episodes = AsyncMock(return_value=[])
    synthesize_reflections = AsyncMock(return_value=[])
    write_reflections = AsyncMock(return_value=0)
    return (
        check_killswitch,
        gather_episodes,
        cluster_episodes,
        synthesize_reflections,
        write_reflections,
    )


async def _run_with_activities(env: WorkflowEnvironment, mocks):
    """Spin up a worker with the mocked activities registered under
    the canonical activity NAMES (not function refs — the workflow
    references them by name via @activity.defn(name=...)). Run the
    workflow and return the result dict."""
    from temporalio import activity

    @activity.defn(name="reflection.check_killswitch")
    async def _killswitch(tenant_id: str) -> bool:
        return await mocks[0](tenant_id)

    @activity.defn(name="reflection.gather_episodes")
    async def _gather(tenant_id: str, day: str) -> list:
        return await mocks[1](tenant_id, day)

    @activity.defn(name="reflection.cluster_episodes")
    async def _cluster(episodes: list) -> list:
        return await mocks[2](episodes)

    @activity.defn(name="reflection.synthesize_reflections")
    async def _synth(tenant_id: str, day: str, episodes: list, clusters: list) -> list:
        return await mocks[3](tenant_id, day, episodes, clusters)

    @activity.defn(name="reflection.write_reflections")
    async def _write(tenant_id: str, reflections: list) -> int:
        return await mocks[4](tenant_id, reflections)

    async with Worker(
        env.client,
        task_queue=TASK_QUEUE,
        workflows=[NightlyReflectionWorkflow],
        activities=[_killswitch, _gather, _cluster, _synth, _write],
    ):
        return await env.client.execute_workflow(
            NightlyReflectionWorkflow.run,
            args=[TENANT, DAY],
            # UUID-based ID avoids the pytest-xdist parallel-run
            # collision the prior global-counter approach risked.
            # (#631 retroactive review I3.)
            id=f"test-nightly-{_uuid.uuid4().hex[:12]}",
            task_queue=TASK_QUEUE,
        )


async def test_killswitch_off_short_circuits_without_running_other_legs():
    """When the per-tenant flag is OFF, the workflow must return the
    operator-visible reason and NOT touch any of the synthesis legs.
    This is the load-bearing safety property of the scaffold."""
    mocks = _make_activity_set(enabled=False)
    async with await WorkflowEnvironment.start_time_skipping() as env:
        result = await _run_with_activities(env, mocks)

    assert result == {"reason": "kill_switch_off", "reflections_written": 0}
    mocks[0].assert_awaited_once_with(TENANT)
    # The other 4 legs must NOT have been called — locked decision #4.
    for m in mocks[1:]:
        m.assert_not_awaited()


async def test_killswitch_on_runs_all_legs_in_order():
    """When the flag is ON, every leg executes once, in order, with
    its arguments threaded through from the prior leg's return."""
    mocks = _make_activity_set(enabled=True)
    async with await WorkflowEnvironment.start_time_skipping() as env:
        result = await _run_with_activities(env, mocks)

    assert result == {"reason": "ok", "reflections_written": 0}
    mocks[0].assert_awaited_once_with(TENANT)
    mocks[1].assert_awaited_once_with(TENANT, DAY)
    mocks[2].assert_awaited_once_with([])
    mocks[3].assert_awaited_once_with(TENANT, DAY, [], [])
    mocks[4].assert_awaited_once_with(TENANT, [])


async def test_write_count_propagates_to_result():
    """When the synthesize leg produces N reflections and the write
    leg returns N, the workflow surfaces that count in the result so
    the morning-report UI can display 'synthesised N reflections'."""
    mocks = _make_activity_set(enabled=True)
    mocks[3].return_value = [{"placeholder": True}]  # synthesize
    mocks[4].return_value = 1  # write

    async with await WorkflowEnvironment.start_time_skipping() as env:
        result = await _run_with_activities(env, mocks)

    assert result == {"reason": "ok", "reflections_written": 1}
