"""Temporal workflow for backfilling vector embeddings.

One-shot workflow on `servicetsunami-databricks` queue.
Runs activities in sequence: backfill_entity_embeddings ->
backfill_memory_embeddings -> backfill_observation_embeddings.
"""
from temporalio import workflow
from temporalio.common import RetryPolicy
from datetime import timedelta


@workflow.defn(sandboxed=False)
class EmbeddingBackfillWorkflow:
    """One-shot embedding backfill for a tenant.

    Workflow ID: embedding-backfill-{tenant_id}
    """

    @workflow.run
    async def run(self, tenant_id: str) -> dict:
        retry_policy = RetryPolicy(
            maximum_attempts=3,
            initial_interval=timedelta(seconds=10),
            backoff_coefficient=2.0,
            maximum_interval=timedelta(seconds=120),
        )
        activity_timeout = timedelta(minutes=10)

        workflow.logger.info(f"Starting embedding backfill for tenant {tenant_id[:8]}")

        results = {}

        # Step 1: Backfill entity embeddings
        try:
            results["entities"] = await workflow.execute_activity(
                "backfill_entity_embeddings",
                args=[tenant_id],
                start_to_close_timeout=activity_timeout,
                schedule_to_close_timeout=timedelta(minutes=20),
                retry_policy=retry_policy,
            )
        except Exception as e:
            workflow.logger.error(f"backfill_entity_embeddings failed: {e}")
            results["entities"] = {"error": str(e)}

        # Step 2: Backfill memory embeddings
        try:
            results["memories"] = await workflow.execute_activity(
                "backfill_memory_embeddings",
                args=[tenant_id],
                start_to_close_timeout=activity_timeout,
                schedule_to_close_timeout=timedelta(minutes=20),
                retry_policy=retry_policy,
            )
        except Exception as e:
            workflow.logger.error(f"backfill_memory_embeddings failed: {e}")
            results["memories"] = {"error": str(e)}

        # Step 3: Backfill observation embeddings
        try:
            results["observations"] = await workflow.execute_activity(
                "backfill_observation_embeddings",
                args=[tenant_id],
                start_to_close_timeout=activity_timeout,
                schedule_to_close_timeout=timedelta(minutes=20),
                retry_policy=retry_policy,
            )
        except Exception as e:
            workflow.logger.error(f"backfill_observation_embeddings failed: {e}")
            results["observations"] = {"error": str(e)}

        workflow.logger.info(f"Embedding backfill complete for tenant {tenant_id[:8]}: {results}")
        return results
