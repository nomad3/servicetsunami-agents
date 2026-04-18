from datetime import timedelta
from temporalio import workflow
from temporalio.common import RetryPolicy

with workflow.unsafe.imports_passed_through():
    from app.workflows.activities.agent_performance import compute_agent_performance_snapshot


@workflow.defn(name="AgentPerformanceRollupWorkflow")
class AgentPerformanceRollupWorkflow:
    @workflow.run
    async def run(self) -> None:
        retry_policy = RetryPolicy(
            initial_interval=timedelta(seconds=5),
            maximum_interval=timedelta(seconds=60),
            backoff_coefficient=2,
            maximum_attempts=3,
        )

        try:
            await workflow.execute_activity(
                compute_agent_performance_snapshot,
                retry_policy=retry_policy,
                start_to_close_timeout=timedelta(minutes=10),
            )
        except Exception as e:
            workflow.logger.warning("compute_agent_performance_snapshot failed: %s", e)

        await workflow.sleep(timedelta(seconds=3600))
        await workflow.continue_as_new()
