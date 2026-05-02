"""TeamsMonitorWorkflow — long-running per-tenant Teams DM poll.

Mirrors the IdleEpisodeScanWorkflow / InboxMonitorWorkflow pattern:
  - Long-running, per-tenant, kicked off when a tenant enables Teams.
  - Each iteration calls a single activity that delegates to
    ``teams_service.monitor_tick`` (allowlist + dedup + Graph fetch +
    auto-reply through the chat path are all there).
  - Sleeps for ``poll_interval_minutes`` (default 5) between ticks.
  - Continues as new each cycle so workflow history stays bounded.

Workflow ID convention: ``teams-monitor-{tenant_id}-{account_id}``.
The /teams/enable endpoint starts this workflow with
``WorkflowIDReusePolicy.ALLOW_DUPLICATE_FAILED_ONLY`` so re-enabling
doesn't double-spawn but a previously-failed run can be replaced.

The workflow is intentionally robust against churn:
  - If the channel_account is disabled mid-tick, ``monitor_tick``
    returns ``{ok: False, reason: "channel not enabled"}`` and we
    sleep + continue as normal. This means a "soft pause" via
    ``POST /teams/disable`` doesn't kill the workflow — it just goes
    quiet until re-enabled.
  - If the tick raises, the activity returns an error dict and the
    workflow proceeds to the next tick. We do NOT cancel the workflow
    on a single bad tick.
"""
from datetime import timedelta

from temporalio import workflow

with workflow.unsafe.imports_passed_through():
    from app.workflows.activities.teams_monitor import teams_monitor_tick


@workflow.defn
class TeamsMonitorWorkflow:
    """Runs ``teams_service.monitor_tick`` on a poll cadence.

    Long-running per-tenant; uses ``continue_as_new`` after each cycle
    so workflow history never grows unbounded.
    """

    @workflow.run
    async def run(
        self,
        tenant_id: str,
        account_id: str = "default",
        poll_interval_minutes: int = 5,
    ) -> None:
        # Single tick. Activity timeout is generous because Graph
        # pagination across many chats can take time on busy tenants;
        # the underlying httpx clients have their own per-request
        # timeouts (15-20s).
        result = await workflow.execute_activity(
            teams_monitor_tick,
            args=[tenant_id, account_id],
            start_to_close_timeout=timedelta(seconds=180),
            heartbeat_timeout=timedelta(seconds=60),
        )

        # Best-effort observability — workflow logger surfaces in the
        # Temporal UI for ops triage. Errors / "channel not enabled"
        # results don't terminate the workflow.
        workflow.logger.info(
            "Teams monitor tick result tenant=%s account=%s ok=%s fetched=%s replied=%s",
            tenant_id[:8], account_id,
            result.get("ok") if isinstance(result, dict) else None,
            (result or {}).get("fetched"),
            (result or {}).get("replied"),
        )

        # Sleep for the poll interval before the next tick.
        await workflow.sleep(timedelta(minutes=poll_interval_minutes))

        # Continue as new — keeps workflow history bounded and resets
        # any in-memory state. Pass-through args so the cadence and
        # account binding survive across continuations.
        workflow.continue_as_new(
            args=[tenant_id, account_id, poll_interval_minutes],
        )
