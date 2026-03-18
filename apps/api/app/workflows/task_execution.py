"""
Temporal workflow for executing agent tasks through the orchestration engine.

Steps:
1. Dispatch task to best agent
2. Recall relevant agent memories
3. Execute task via CLI orchestrator
4. Persist entities from output to knowledge graph
5. Evaluate results and store learnings
"""

from temporalio import workflow
from datetime import timedelta
from typing import Dict, Any


@workflow.defn(sandboxed=False)
class TaskExecutionWorkflow:
    """
    Durable workflow for executing agent tasks.

    Steps:
    1. dispatch_task - Find best agent for the task
    2. recall_memory - Load relevant agent memories
    3. execute_task - Run task via CLI orchestrator
    4. persist_entities - Extract and persist entities to knowledge graph
    5. evaluate_task - Score results, store memory, update skills
    """

    @workflow.run
    async def run(self, task_id: str, tenant_id: str, task_data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Execute a task through the full orchestration pipeline.

        Args:
            task_id: UUID of the agent task
            tenant_id: UUID of the tenant
            task_data: Task context including objective, task_type, capabilities, group_id, agent_id

        Returns:
            Dict with status, agent_id, output, confidence, tokens_used, cost
        """
        retry_policy = workflow.RetryPolicy(
            maximum_attempts=3,
            initial_interval=timedelta(seconds=30),
            maximum_interval=timedelta(seconds=60),
            backoff_coefficient=2.0,
        )

        workflow.logger.info(f"Starting task execution for {task_id}")

        try:
            # Step 1: Dispatch task to best agent
            dispatch_result = await workflow.execute_activity(
                "dispatch_task",
                args=[task_id, tenant_id, task_data],
                start_to_close_timeout=timedelta(minutes=2),
                schedule_to_close_timeout=timedelta(minutes=15),
                retry_policy=retry_policy,
            )

            agent_id = dispatch_result["agent_id"]
            workflow.logger.info(f"Task dispatched to agent {agent_id}")

            # Step 2: Recall relevant memories
            memory_result = await workflow.execute_activity(
                "recall_memory",
                args=[task_id, tenant_id, agent_id, task_data],
                start_to_close_timeout=timedelta(minutes=1),
                schedule_to_close_timeout=timedelta(minutes=15),
                retry_policy=retry_policy,
            )

            workflow.logger.info(f"Recalled {len(memory_result.get('memories', []))} memories")

            # Step 3: Execute task
            context = {
                **task_data,
                "memories": memory_result.get("memories", []),
            }

            execute_result = await workflow.execute_activity(
                "execute_task",
                args=[task_id, tenant_id, agent_id, context],
                start_to_close_timeout=timedelta(minutes=10),
                schedule_to_close_timeout=timedelta(minutes=15),
                retry_policy=retry_policy,
            )

            workflow.logger.info(f"Task executed with status: {execute_result['status']}")

            # Step 4: Persist entities from output (if applicable)
            persist_result = await workflow.execute_activity(
                "persist_entities",
                args=[task_id, tenant_id, agent_id, execute_result],
                start_to_close_timeout=timedelta(minutes=5),
                schedule_to_close_timeout=timedelta(minutes=15),
                retry_policy=retry_policy,
            )

            workflow.logger.info(
                f"Entities persisted: {persist_result.get('entities_created', 0)} created"
            )

            # Step 5: Evaluate results
            evaluate_result = await workflow.execute_activity(
                "evaluate_task",
                args=[task_id, tenant_id, agent_id, execute_result],
                start_to_close_timeout=timedelta(minutes=2),
                schedule_to_close_timeout=timedelta(minutes=15),
                retry_policy=retry_policy,
            )

            workflow.logger.info(f"Task evaluation complete: confidence={evaluate_result.get('confidence')}")

            return {
                "status": evaluate_result["status"],
                "agent_id": agent_id,
                "output": execute_result.get("output"),
                "confidence": evaluate_result.get("confidence"),
                "tokens_used": evaluate_result.get("tokens_used"),
                "cost": evaluate_result.get("cost"),
            }

        except Exception as e:
            step_name = "unknown"
            try:
                dispatch_result  # noqa: B018
                try:
                    memory_result  # noqa: B018
                    try:
                        execute_result  # noqa: B018
                        try:
                            persist_result  # noqa: B018
                            step_name = "evaluate_task"
                        except NameError:
                            step_name = "persist_entities"
                    except NameError:
                        step_name = "execute_task"
                except NameError:
                    step_name = "recall_memory"
            except NameError:
                step_name = "dispatch_task"

            workflow.logger.error(
                f"Task execution failed at step '{step_name}' for task {task_id}: {e}"
            )
            return {
                "status": "failed",
                "step": step_name,
                "error": str(e),
            }
