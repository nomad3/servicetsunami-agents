"""Tools for the dev_agent — starts dev tasks via Temporal workflows."""

import asyncio
import logging
import os
import uuid

from google.adk.tools import FunctionTool

logger = logging.getLogger(__name__)

TEMPORAL_ADDRESS = os.environ.get("TEMPORAL_ADDRESS", "temporal:7233")
TASK_QUEUE = "servicetsunami-dev"


async def _start_dev_workflow(task_description: str, tenant_id: str, context: str = "") -> dict:
    """Start a DevTaskWorkflow on Temporal and wait for the result."""
    from temporalio.client import Client

    client = await Client.connect(TEMPORAL_ADDRESS)

    workflow_id = f"dev-task-{uuid.uuid4().hex[:8]}"

    handle = await client.start_workflow(
        "DevTaskWorkflow",
        arg={
            "task_description": task_description,
            "tenant_id": tenant_id,
            "context": context,
        },
        id=workflow_id,
        task_queue=TASK_QUEUE,
    )

    logger.info("Started DevTaskWorkflow %s for tenant %s", workflow_id, tenant_id)

    # Wait for completion (up to 15 min)
    result = await handle.result()

    return {
        "workflow_id": workflow_id,
        "pr_url": result.get("pr_url", ""),
        "summary": result.get("summary", ""),
        "branch": result.get("branch", ""),
        "files_changed": result.get("files_changed", []),
        "success": result.get("success", False),
        "error": result.get("error"),
    }


def start_dev_task(task_description: str, tenant_id: str, context: str = "") -> dict:
    """Start an autonomous dev task. Claude Code will implement the task, create a branch, and open a PR.

    Args:
        task_description: What to build or fix. Be specific.
        tenant_id: The tenant ID (from session state).
        context: Optional additional context about the codebase or requirements.

    Returns:
        dict with pr_url, summary, branch, files_changed, success, error.
    """
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            import concurrent.futures
            with concurrent.futures.ThreadPoolExecutor() as pool:
                result = pool.submit(asyncio.run, _start_dev_workflow(task_description, tenant_id, context)).result()
        else:
            result = loop.run_until_complete(_start_dev_workflow(task_description, tenant_id, context))
    except RuntimeError:
        result = asyncio.run(_start_dev_workflow(task_description, tenant_id, context))

    return result


start_dev_task_tool = FunctionTool(start_dev_task)
