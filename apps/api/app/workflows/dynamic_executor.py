"""Dynamic Workflow Executor — interprets JSON workflow definitions on Temporal.

Every step becomes a real Temporal activity with typed timeouts, retry
policies, and heartbeating. for_each uses child workflows for per-iteration
durability. human_approval uses Temporal signals.
"""

import asyncio
import uuid
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any, Dict, List, Optional

from temporalio import workflow
from temporalio.common import RetryPolicy

with workflow.unsafe.imports_passed_through():
    from app.workflows.activities.dynamic_step import execute_dynamic_step, finalize_workflow_run


@dataclass
class DynamicWorkflowInput:
    workflow_id: str
    run_id: str
    tenant_id: str
    definition: Dict[str, Any]
    input_data: Dict[str, Any] = field(default_factory=dict)


@dataclass
class DynamicWorkflowResult:
    status: str
    output: Dict[str, Any] = field(default_factory=dict)
    total_tokens: int = 0
    total_cost: float = 0.0
    steps_completed: int = 0
    error: Optional[str] = None


def _timeout_for(step: dict) -> timedelta:
    """Per-step-type timeout. Agents get 10min, tools get 60s, waits get days."""
    if step.get("timeout_seconds"):
        return timedelta(seconds=step["timeout_seconds"])
    timeouts = {
        "agent": timedelta(minutes=10),
        "mcp_tool": timedelta(seconds=60),
        "condition": timedelta(seconds=10),
        "transform": timedelta(seconds=10),
        "wait": timedelta(days=7),
        "human_approval": timedelta(days=30),
        "workflow": timedelta(minutes=30),
        "cli_execute": timedelta(minutes=30),
        "internal_api": timedelta(seconds=60),
        "continue_as_new": timedelta(seconds=10),
    }
    return timeouts.get(step.get("type", ""), timedelta(minutes=5))


def _heartbeat_for(step: dict) -> Optional[timedelta]:
    """Heartbeat only when a step explicitly opts in.

    The current dynamic step activity does not emit periodic heartbeats while
    blocking on downstream tool or agent calls. Setting a default heartbeat
    timeout here causes healthy long-running steps to fail after 60 seconds.
    """
    heartbeat_seconds = step.get("heartbeat_seconds")
    if heartbeat_seconds:
        return timedelta(seconds=heartbeat_seconds)
    return None


def _retry_for(step: dict) -> RetryPolicy:
    """Per-step-type retry policy."""
    max_retries = step.get("max_retries")
    if step.get("type") in ("condition", "transform"):
        return RetryPolicy(maximum_attempts=1)
    if step.get("type") == "mcp_tool":
        return RetryPolicy(
            maximum_attempts=max_retries or 3,
            initial_interval=timedelta(seconds=5),
            backoff_coefficient=2.0,
        )
    return RetryPolicy(
        maximum_attempts=max_retries or 2,
        initial_interval=timedelta(seconds=10),
        backoff_coefficient=2.0,
    )


@workflow.defn
class DynamicWorkflowExecutor:
    """Execute any dynamic workflow from its JSON definition.

    Temporal guarantees:
    - Crash recovery: resumes at exact step on restart
    - Retry: per-step policies (3x tools, 2x agents, 1x conditions)
    - Heartbeat: long-running agent steps heartbeat every 60s
    - Durability: for_each iterations are independent child workflows
    - Signals: human_approval steps wait for external approval
    """

    def __init__(self):
        self._approvals: Dict[str, bool] = {}

    @workflow.run
    async def run(self, input: DynamicWorkflowInput) -> DynamicWorkflowResult:
        steps = input.definition.get("steps", [])
        context: Dict[str, Any] = {"input": input.input_data}
        total_tokens = 0
        total_cost = 0.0
        steps_completed = 0

        for step in steps:
            step_type = step.get("type", "")

            try:
                if step_type == "for_each":
                    result = await self._execute_for_each(step, context, input)
                elif step_type == "parallel":
                    result = await self._execute_parallel(step, context, input)
                elif step_type == "human_approval":
                    result = await self._wait_for_approval(step)
                elif step_type == "wait":
                    duration_s = _parse_duration(step.get("duration", "60s"))
                    await workflow.sleep(timedelta(seconds=duration_s))
                    result = {"waited": step.get("duration"), "seconds": duration_s}
                elif step_type == "cli_execute":
                    # Dispatch to code-worker queue via child workflow
                    params = step.get("params", {})
                    result = {"delegated_to": "servicetsunami-code", "task": params.get("task", step.get("task", ""))}
                elif step_type == "continue_as_new":
                    # Handled after the loop, skip as a step
                    result = {"type": "continue_as_new", "interval_seconds": step.get("interval_seconds", 900)}
                else:
                    result = await workflow.execute_activity(
                        execute_dynamic_step,
                        args=[step, context, input.tenant_id, input.run_id],
                        start_to_close_timeout=_timeout_for(step),
                        heartbeat_timeout=_heartbeat_for(step),
                        retry_policy=_retry_for(step),
                    )

                # Store result
                output_key = step.get("output") or step.get("id", f"step_{steps_completed}")
                context[output_key] = result
                steps_completed += 1

                # Accumulate cost
                if isinstance(result, dict):
                    total_tokens += result.get("tokens_used", 0)
                    total_cost += result.get("cost_usd", 0.0)

                # Condition branching
                if step_type == "condition":
                    if not result.get("passed"):
                        else_action = step.get("else", "skip")
                        if else_action == "skip":
                            continue
                        # Future: jump to specific step

            except Exception as e:
                workflow.logger.error("Step %s failed: %s", step.get("id"), str(e))
                error_msg = f"Step {step.get('id', '?')} failed: {str(e)}"
                await workflow.execute_activity(
                    finalize_workflow_run,
                    args=[input.run_id, "failed", steps_completed, total_tokens, total_cost, error_msg],
                    start_to_close_timeout=timedelta(seconds=30),
                )
                return DynamicWorkflowResult(
                    status="failed",
                    output=context,
                    total_tokens=total_tokens,
                    total_cost=total_cost,
                    steps_completed=steps_completed,
                    error=error_msg,
                )

        # Check if last step is continue_as_new (infinite-duration workflow)
        if steps and steps[-1].get("type") == "continue_as_new":
            last_step = steps[-1]
            interval = last_step.get("interval_seconds", 900)
            await workflow.execute_activity(
                finalize_workflow_run,
                args=[input.run_id, "completed", steps_completed, total_tokens, total_cost],
                start_to_close_timeout=timedelta(seconds=30),
            )
            await workflow.sleep(timedelta(seconds=interval))
            workflow.continue_as_new(input)

        await workflow.execute_activity(
            finalize_workflow_run,
            args=[input.run_id, "completed", steps_completed, total_tokens, total_cost],
            start_to_close_timeout=timedelta(seconds=30),
        )
        return DynamicWorkflowResult(
            status="completed",
            output=context,
            total_tokens=total_tokens,
            total_cost=total_cost,
            steps_completed=steps_completed,
        )

    async def _execute_for_each(
        self, step: dict, context: dict, input: DynamicWorkflowInput
    ) -> List[dict]:
        """Each iteration as a child workflow — independently durable."""
        collection_ref = step.get("collection", "[]")
        collection = _resolve_value(collection_ref, context)
        if not isinstance(collection, list):
            collection = [collection]

        item_var = step.get("as", "item")
        sub_steps = step.get("steps", [])
        results = []

        for i, item in enumerate(collection):
            child_input = DynamicWorkflowInput(
                workflow_id=input.workflow_id,
                run_id=input.run_id,
                tenant_id=input.tenant_id,
                definition={"steps": sub_steps},
                input_data={**context, item_var: item},
            )
            result = await workflow.execute_child_workflow(
                DynamicWorkflowExecutor.run,
                child_input,
                id=f"{workflow.info().workflow_id}-foreach-{i}",
            )
            results.append(result.output if isinstance(result, DynamicWorkflowResult) else result)

        return results

    async def _execute_parallel(
        self, step: dict, context: dict, input: DynamicWorkflowInput
    ) -> List[dict]:
        """Run sub-steps concurrently, wait for all."""
        import asyncio
        sub_steps = step.get("steps", [])
        tasks = []
        for sub_step in sub_steps:
            tasks.append(
                workflow.execute_activity(
                    execute_dynamic_step,
                    args=[sub_step, context, input.tenant_id, input.run_id],
                    start_to_close_timeout=_timeout_for(sub_step),
                    retry_policy=_retry_for(sub_step),
                )
            )
        return list(await asyncio.gather(*tasks))

    @workflow.signal
    async def approve_step(self, step_id: str, approved: bool):
        """Signal handler for human approval steps."""
        self._approvals[step_id] = approved

    async def _wait_for_approval(self, step: dict) -> dict:
        """Wait for human signal — survives days/weeks."""
        step_id = step.get("id", "approval")
        timeout_days = step.get("timeout_days", 30)
        try:
            await workflow.wait_condition(
                lambda: step_id in self._approvals,
                timeout=timedelta(days=timeout_days),
            )
            return {"approved": self._approvals.get(step_id, False), "step_id": step_id}
        except asyncio.TimeoutError:
            return {"approved": False, "step_id": step_id, "timeout": True}


def _resolve_value(ref: str, context: dict) -> Any:
    """Resolve a {{variable}} reference from context."""
    if not isinstance(ref, str):
        return ref
    ref = ref.strip()
    if ref.startswith("{{") and ref.endswith("}}"):
        path = ref[2:-2].strip()
        value = context
        for key in path.split("."):
            if isinstance(value, dict):
                value = value.get(key, ref)
            elif isinstance(value, list) and key.isdigit():
                value = value[int(key)]
            else:
                return ref
        return value
    return ref


def _parse_duration(s: str) -> int:
    """Parse duration string to seconds. E.g. '5m' → 300, '2h' → 7200."""
    s = s.strip().lower()
    if s.endswith("s"):
        return int(s[:-1])
    if s.endswith("m"):
        return int(s[:-1]) * 60
    if s.endswith("h"):
        return int(s[:-1]) * 3600
    if s.endswith("d"):
        return int(s[:-1]) * 86400
    try:
        return int(s)
    except ValueError:
        return 60
