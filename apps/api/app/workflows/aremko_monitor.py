"""Temporal workflow for recurring Aremko Spa availability monitoring.

Periodically checks availability across all Aremko services (tinajas, masajes,
cabañas) and creates notifications when meaningful changes are detected —
e.g. a popular tub becomes fully booked or new slots open up.

Uses continue_as_new to keep history bounded (same as InboxMonitorWorkflow).

Workflow ID: aremko-monitor-{tenant_id}
Queue: servicetsunami-orchestration
"""
from temporalio import workflow
from temporalio.common import RetryPolicy
from datetime import timedelta
from typing import Optional


@workflow.defn(sandboxed=False)
class AremkoMonitorWorkflow:
    """Recurring availability monitor for Aremko Spa.

    Cycle (default every 60 min):
      fetch availability for next N days → compare with previous snapshot →
      detect changes → create notifications → sleep → continue_as_new

    One workflow instance per tenant. Trigger manually or via MCP tool.
    """

    @workflow.run
    async def run(
        self,
        tenant_id: str,
        check_interval_seconds: int = 3600,
        days_ahead: int = 3,
        previous_snapshot: Optional[dict] = None,
    ) -> dict:
        retry_policy = RetryPolicy(
            maximum_attempts=3,
            initial_interval=timedelta(seconds=10),
            backoff_coefficient=2.0,
            maximum_interval=timedelta(seconds=60),
        )
        activity_timeout = timedelta(minutes=3)

        workflow.logger.info(
            "AremkoMonitor cycle: tenant=%s days_ahead=%d", tenant_id[:8], days_ahead
        )

        snapshot = previous_snapshot or {}
        changes = []
        notifications_created = 0

        # Step 1: Fetch current availability snapshot
        try:
            snapshot_result = await workflow.execute_activity(
                "fetch_aremko_snapshot",
                args=[tenant_id, days_ahead],
                start_to_close_timeout=activity_timeout,
                schedule_to_close_timeout=timedelta(minutes=5),
                retry_policy=retry_policy,
            )
            new_snapshot = snapshot_result.get("snapshot", {})
        except Exception as e:
            workflow.logger.error("Step 1 (fetch_aremko_snapshot) failed: %s", e)
            new_snapshot = snapshot  # keep old snapshot on failure

        # Step 2: Detect meaningful changes vs previous snapshot
        try:
            if snapshot:
                changes_result = await workflow.execute_activity(
                    "detect_aremko_changes",
                    args=[tenant_id, snapshot, new_snapshot],
                    start_to_close_timeout=activity_timeout,
                    schedule_to_close_timeout=timedelta(minutes=4),
                    retry_policy=retry_policy,
                )
                changes = changes_result.get("changes", [])
        except Exception as e:
            workflow.logger.error("Step 2 (detect_aremko_changes) failed: %s", e)

        # Step 3: Create notifications for significant changes
        try:
            if changes:
                notif_result = await workflow.execute_activity(
                    "create_aremko_notifications",
                    args=[tenant_id, changes],
                    start_to_close_timeout=activity_timeout,
                    schedule_to_close_timeout=timedelta(minutes=4),
                    retry_policy=retry_policy,
                )
                notifications_created = notif_result.get("created", 0)
                workflow.logger.info(
                    "AremkoMonitor: %d changes detected, %d notifications created",
                    len(changes), notifications_created,
                )
        except Exception as e:
            workflow.logger.error("Step 3 (create_aremko_notifications) failed: %s", e)

        # Sleep then continue as new with updated snapshot
        await workflow.sleep(timedelta(seconds=check_interval_seconds))

        workflow.continue_as_new(args=[
            tenant_id,
            check_interval_seconds,
            days_ahead,
            new_snapshot,
        ])
