"""Temporal workflow for proactive Gmail + Calendar monitoring.

Long-running workflow (one per tenant) that periodically checks for new
emails and upcoming events, triages them with an LLM enriched by memory
context, creates notifications, and extracts entities/memories from
important emails through the standard knowledge extraction pipeline.

Uses continue_as_new to prevent history growth (same as ChannelHealthMonitorWorkflow).
"""
from temporalio import workflow
from temporalio.common import RetryPolicy
from datetime import timedelta
from typing import Optional


@workflow.defn(sandboxed=False)
class InboxMonitorWorkflow:
    """Periodic inbox monitor for Gmail and Calendar.

    Runs every N seconds (default 15 min):
    fetch emails → fetch events → triage (with memory enrichment) →
    create notifications → extract entities from important emails → log → continue_as_new

    One workflow instance per tenant. Workflow ID: inbox-monitor-{tenant_id}
    """

    @workflow.run
    async def run(
        self,
        tenant_id: str,
        check_interval_seconds: int = 900,
        last_gmail_history_id: Optional[str] = None,
        calendar_hours_ahead: int = 24,
    ) -> dict:
        retry_policy = RetryPolicy(
            maximum_attempts=3,
            initial_interval=timedelta(seconds=15),
            backoff_coefficient=2.0,
            maximum_interval=timedelta(seconds=60),
        )
        activity_timeout = timedelta(minutes=2)

        workflow.logger.info(f"Inbox monitor cycle for tenant {tenant_id[:8]}")

        # Track state across steps so failures don't lose data from earlier steps
        emails = []
        new_history_id = last_gmail_history_id
        events = []
        triaged_items = []
        notifications_created = 0
        extraction_result = {"entities": 0}
        step_errors = []

        # Step 1: Fetch new emails
        try:
            email_result = await workflow.execute_activity(
                "fetch_new_emails",
                args=[tenant_id, last_gmail_history_id],
                start_to_close_timeout=activity_timeout,
                schedule_to_close_timeout=timedelta(minutes=4),
                retry_policy=retry_policy,
            )
            emails = email_result.get("emails", [])
            new_history_id = email_result.get("new_history_id", last_gmail_history_id)
        except Exception as e:
            workflow.logger.error(f"Step 1 (fetch_new_emails) failed: {e}")
            step_errors.append(f"fetch_new_emails: {e}")

        # Step 2: Fetch upcoming calendar events
        try:
            event_result = await workflow.execute_activity(
                "fetch_upcoming_events",
                args=[tenant_id, calendar_hours_ahead],
                start_to_close_timeout=activity_timeout,
                schedule_to_close_timeout=timedelta(minutes=4),
                retry_policy=retry_policy,
            )
            events = event_result.get("events", [])
        except Exception as e:
            workflow.logger.error(f"Step 2 (fetch_upcoming_events) failed: {e}")
            step_errors.append(f"fetch_upcoming_events: {e}")

        # Step 3: Triage items with LLM + memory context enrichment
        try:
            if emails or events:
                triaged_items = await workflow.execute_activity(
                    "triage_items",
                    args=[tenant_id, emails, events],
                    start_to_close_timeout=timedelta(minutes=3),
                    schedule_to_close_timeout=timedelta(minutes=6),
                    retry_policy=retry_policy,
                )
        except Exception as e:
            workflow.logger.error(f"Step 3 (triage_items) failed: {e}")
            step_errors.append(f"triage_items: {e}")

        # Step 4: Create notifications (deduplicates by reference_id)
        try:
            notif_result = await workflow.execute_activity(
                "create_notifications",
                args=[tenant_id, triaged_items],
                start_to_close_timeout=activity_timeout,
                schedule_to_close_timeout=timedelta(minutes=4),
                retry_policy=retry_policy,
            )
            notifications_created = notif_result.get("created", 0)
        except Exception as e:
            workflow.logger.error(f"Step 4 (create_notifications) failed: {e}")
            step_errors.append(f"create_notifications: {e}")

        # Step 5: Extract entities/relations/memories from important emails
        try:
            if emails and triaged_items:
                extraction_result = await workflow.execute_activity(
                    "extract_from_emails",
                    args=[tenant_id, emails, triaged_items],
                    start_to_close_timeout=timedelta(minutes=5),
                    schedule_to_close_timeout=timedelta(minutes=10),
                    retry_policy=retry_policy,
                )
        except Exception as e:
            workflow.logger.error(f"Step 5 (extract_from_emails) failed: {e}")
            step_errors.append(f"extract_from_emails: {e}")

        # Step 5b: Proactive memory surfacing (meeting context + stale leads)
        try:
            if events:
                await workflow.execute_activity(
                    "check_proactive_triggers",
                    args=[tenant_id, events],
                    start_to_close_timeout=activity_timeout,
                    schedule_to_close_timeout=timedelta(minutes=4),
                    retry_policy=retry_policy,
                )
        except Exception as e:
            workflow.logger.error(f"Step 5b (check_proactive_triggers) failed: {e}")
            step_errors.append(f"check_proactive_triggers: {e}")

        # Step 6: Log the scan cycle
        try:
            wf_info = workflow.info()
            await workflow.execute_activity(
                "log_monitor_cycle",
                args=[
                    tenant_id,
                    wf_info.run_id,
                    len(emails),
                    len(events),
                    notifications_created,
                    extraction_result.get("entities", 0),
                ],
                start_to_close_timeout=activity_timeout,
                schedule_to_close_timeout=timedelta(minutes=4),
                retry_policy=retry_policy,
            )
        except Exception as e:
            workflow.logger.error(f"Step 6 (log_monitor_cycle) failed: {e}")
            step_errors.append(f"log_monitor_cycle: {e}")

        if step_errors:
            workflow.logger.warning(
                f"Cycle completed with {len(step_errors)} error(s): {step_errors}"
            )

        # Sleep then continue as new (plain args, matching ChannelHealthMonitor pattern)
        await workflow.sleep(timedelta(seconds=check_interval_seconds))

        workflow.continue_as_new(args=[
            tenant_id,
            check_interval_seconds,
            new_history_id,
            calendar_hours_ahead,
        ])
