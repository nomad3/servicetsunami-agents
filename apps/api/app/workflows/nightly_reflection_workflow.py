"""NightlyReflectionWorkflow — O2 scaffold of the offline-synthesis track.

Canonical design: ``docs/plans/2026-05-20-luna-metacognition-and-dreams-canonical.md``
§5 / O2.

Phase 1 (this PR): kill-switch + scaffolded leg sequence. All four
activities are stubs that return empty results — the workflow shape
is the load-bearing piece. Phase 2 fills in the synthesis bodies once
this is deployed and per-tenant enablement is reviewed.

Run shape
---------
Triggered per-tenant by an operator-scheduled Temporal cron (NOT
wired in this PR — schedule activation is a separate operator action,
per locked decision #4). The argument is ``(tenant_id, day)`` where
``day`` is the YYYY-MM-DD UTC the synthesis is *about* (i.e. yesterday).

Leg order:

  1. check_killswitch        — per-tenant opt-in gate. Short-circuits
                               with reason='kill_switch_off' when OFF.
  2. gather_episodes         — pull the day's conversation_episodes
  3. cluster_episodes        — group hard cases for counterfactual replay
  4. synthesize_reflections  — run the 4 dream mechanisms
  5. write_reflections       — persist via reflection_io

Each leg is a Temporal activity so failures retry independently and
the LLM-heavy synthesis step doesn't burn through retry budget on
network-flaky DB queries.

Return shape
------------
``{"reason": str, "reflections_written": int}`` — the morning report
surface can read this to display "synthesised on YYYY-MM-DD" with
counts, or surface the skip reason when no synthesis ran.
"""
from datetime import timedelta

from temporalio import workflow

with workflow.unsafe.imports_passed_through():
    from app.workflows.activities.reflection_activities import (
        check_killswitch,
        cluster_episodes,
        gather_episodes,
        synthesize_reflections,
        write_reflections,
    )


# Activity timeouts mirror the canonical doc's cost mitigation (§7
# "Compute cost"): cap each leg so a runaway LLM call doesn't drag
# the nightly window. Generous because synthesis is sequential, not
# realtime — but bounded.
_KILLSWITCH_TIMEOUT = timedelta(seconds=15)
_GATHER_TIMEOUT = timedelta(minutes=2)
_CLUSTER_TIMEOUT = timedelta(minutes=2)
_SYNTHESIZE_TIMEOUT = timedelta(minutes=10)
_WRITE_TIMEOUT = timedelta(minutes=2)


@workflow.defn
class NightlyReflectionWorkflow:
    @workflow.run
    async def run(self, tenant_id: str, day: str) -> dict:
        """Run a per-tenant overnight synthesis pass.

        Args:
            tenant_id: UUID of the tenant whose history to synthesize.
            day:       YYYY-MM-DD UTC — the day the synthesis is about
                       (typically yesterday when run at 03:00 local).
        """
        enabled = await workflow.execute_activity(
            check_killswitch,
            args=[tenant_id],
            start_to_close_timeout=_KILLSWITCH_TIMEOUT,
        )
        if not enabled:
            return {
                "reason": "kill_switch_off",
                "reflections_written": 0,
            }

        episodes = await workflow.execute_activity(
            gather_episodes,
            args=[tenant_id, day],
            start_to_close_timeout=_GATHER_TIMEOUT,
        )

        clusters = await workflow.execute_activity(
            cluster_episodes,
            args=[episodes],
            start_to_close_timeout=_CLUSTER_TIMEOUT,
        )

        reflections = await workflow.execute_activity(
            synthesize_reflections,
            args=[tenant_id, day, episodes, clusters],
            start_to_close_timeout=_SYNTHESIZE_TIMEOUT,
        )

        written = await workflow.execute_activity(
            write_reflections,
            args=[tenant_id, reflections],
            start_to_close_timeout=_WRITE_TIMEOUT,
        )

        return {
            "reason": "ok",
            "reflections_written": int(written),
        }
