"""Temporal workflow for periodic memory consolidation.

Long-running workflow (one per tenant) that deduplicates entities, applies
memory decay, promotes entity lifecycle stages, syncs memories with entities,
and archives stale data.

Uses continue_as_new every 24h (same pattern as InboxMonitorWorkflow).
"""
from temporalio import workflow
from temporalio.common import RetryPolicy
from datetime import timedelta
import json


@workflow.defn(sandboxed=False)
class MemoryConsolidationWorkflow:
    """Periodic memory consolidation for a tenant.

    Runs every 24h:
    find_duplicate_entities -> auto_merge_duplicates -> apply_memory_decay ->
    promote_entities -> archive_stale_entities (via promote) ->
    sync_memories_and_entities -> log_consolidation_results -> continue_as_new

    Workflow ID: memory-consolidation-{tenant_id}
    """

    @workflow.run
    async def run(
        self,
        tenant_id: str,
        consolidation_interval_seconds: int = 86400,
    ) -> dict:
        retry_policy = RetryPolicy(
            maximum_attempts=3,
            initial_interval=timedelta(seconds=15),
            backoff_coefficient=2.0,
            maximum_interval=timedelta(seconds=60),
        )
        activity_timeout = timedelta(minutes=5)

        workflow.logger.info(f"Memory consolidation cycle for tenant {tenant_id[:8]}")

        step_errors = []
        results = {}

        # Step 1: Find duplicate entities
        clusters_json = "[]"
        try:
            dup_result = await workflow.execute_activity(
                "find_duplicate_entities",
                args=[tenant_id],
                start_to_close_timeout=activity_timeout,
                schedule_to_close_timeout=timedelta(minutes=10),
                retry_policy=retry_policy,
            )
            results["duplicates"] = dup_result
            clusters = dup_result.get("clusters", [])
            if clusters:
                clusters_json = json.dumps(clusters)
        except Exception as e:
            workflow.logger.error(f"Step 1 (find_duplicate_entities) failed: {e}")
            step_errors.append(f"find_duplicate_entities: {e}")

        # Step 2: Auto-merge duplicates
        try:
            if clusters_json != "[]":
                merge_result = await workflow.execute_activity(
                    "auto_merge_duplicates",
                    args=[tenant_id, clusters_json],
                    start_to_close_timeout=activity_timeout,
                    schedule_to_close_timeout=timedelta(minutes=10),
                    retry_policy=retry_policy,
                )
                results["merge"] = merge_result
        except Exception as e:
            workflow.logger.error(f"Step 2 (auto_merge_duplicates) failed: {e}")
            step_errors.append(f"auto_merge_duplicates: {e}")

        # Step 3: Apply memory decay
        try:
            decay_result = await workflow.execute_activity(
                "apply_memory_decay",
                args=[tenant_id],
                start_to_close_timeout=activity_timeout,
                schedule_to_close_timeout=timedelta(minutes=10),
                retry_policy=retry_policy,
            )
            results["decay"] = decay_result
        except Exception as e:
            workflow.logger.error(f"Step 3 (apply_memory_decay) failed: {e}")
            step_errors.append(f"apply_memory_decay: {e}")

        # Step 4: Promote entities through lifecycle stages
        try:
            promote_result = await workflow.execute_activity(
                "promote_entities",
                args=[tenant_id],
                start_to_close_timeout=activity_timeout,
                schedule_to_close_timeout=timedelta(minutes=10),
                retry_policy=retry_policy,
            )
            results["promotions"] = promote_result
        except Exception as e:
            workflow.logger.error(f"Step 4 (promote_entities) failed: {e}")
            step_errors.append(f"promote_entities: {e}")

        # Step 5: Sync memories and entities
        try:
            sync_result = await workflow.execute_activity(
                "sync_memories_and_entities",
                args=[tenant_id],
                start_to_close_timeout=activity_timeout,
                schedule_to_close_timeout=timedelta(minutes=10),
                retry_policy=retry_policy,
            )
            results["sync"] = sync_result
        except Exception as e:
            workflow.logger.error(f"Step 5 (sync_memories_and_entities) failed: {e}")
            step_errors.append(f"sync_memories_and_entities: {e}")

        # Step 6: Log consolidation results
        try:
            results_json = json.dumps(results, default=str)
            await workflow.execute_activity(
                "log_consolidation_results",
                args=[tenant_id, results_json],
                start_to_close_timeout=activity_timeout,
                schedule_to_close_timeout=timedelta(minutes=10),
                retry_policy=retry_policy,
            )
        except Exception as e:
            workflow.logger.error(f"Step 6 (log_consolidation_results) failed: {e}")
            step_errors.append(f"log_consolidation_results: {e}")

        if step_errors:
            workflow.logger.warning(
                f"Consolidation completed with {len(step_errors)} error(s): {step_errors}"
            )

        # Sleep then continue as new
        await workflow.sleep(timedelta(seconds=consolidation_interval_seconds))

        workflow.continue_as_new(args=[
            tenant_id,
            consolidation_interval_seconds,
        ])
