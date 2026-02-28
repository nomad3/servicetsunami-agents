"""
Temporal workflow for monthly veterinary billing settlement.

Runs on the 1st of each month: aggregates completed visits per clinic,
generates invoices, sends them, and schedules follow-ups for unpaid ones.
"""
from temporalio import workflow
from datetime import timedelta
from dataclasses import dataclass
from typing import List


@dataclass
class MonthlyBillingInput:
    tenant_id: str
    month: str  # "YYYY-MM" format
    clinic_ids: List[str] = None  # None = all clinics


@workflow.defn(sandboxed=False)
class MonthlyBillingWorkflow:
    """Monthly billing settlement for veterinary cardiologist visits."""

    @workflow.run
    async def run(self, input: MonthlyBillingInput) -> dict:
        retry_policy = workflow.RetryPolicy(
            maximum_attempts=3,
            initial_interval=timedelta(seconds=30),
            backoff_coefficient=2.0,
        )

        workflow.logger.info(
            f"Starting monthly billing for tenant {input.tenant_id[:8]}, month {input.month}"
        )

        # Step 1: Aggregate visits for the billing period
        visits = await workflow.execute_activity(
            "aggregate_billing_visits",
            args=[input.tenant_id, input.month, input.clinic_ids],
            start_to_close_timeout=timedelta(minutes=5),
            retry_policy=retry_policy,
        )

        if not visits.get("clinics"):
            return {"status": "no_visits", "month": input.month}

        # Step 2: Generate invoices per clinic
        invoices = await workflow.execute_activity(
            "generate_billing_invoices",
            args=[input.tenant_id, input.month, visits["clinics"]],
            start_to_close_timeout=timedelta(minutes=10),
            retry_policy=retry_policy,
        )

        # Step 3: Send invoices via email + WhatsApp
        delivery = await workflow.execute_activity(
            "send_billing_invoices",
            args=[input.tenant_id, invoices.get("invoice_ids", [])],
            start_to_close_timeout=timedelta(minutes=5),
            retry_policy=retry_policy,
        )

        # Step 4: Schedule follow-up for unpaid invoices (7-day reminder)
        followup = await workflow.execute_activity(
            "schedule_billing_followups",
            args=[input.tenant_id, invoices.get("invoice_ids", [])],
            start_to_close_timeout=timedelta(minutes=1),
            retry_policy=retry_policy,
        )

        return {
            "status": "completed",
            "month": input.month,
            "clinics_billed": len(visits.get("clinics", [])),
            "invoices_generated": len(invoices.get("invoice_ids", [])),
            "invoices_sent": delivery.get("sent_count", 0),
            "followups_scheduled": followup.get("count", 0),
        }
