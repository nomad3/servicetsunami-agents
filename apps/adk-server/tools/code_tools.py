"""Tools for the code_agent — starts code tasks via Temporal workflows."""

import asyncio
import logging
import os
import re
import uuid
from contextvars import ContextVar

from google.adk.tools import FunctionTool

logger = logging.getLogger(__name__)

TEMPORAL_ADDRESS = os.environ.get("TEMPORAL_ADDRESS", "temporal:7233")
TASK_QUEUE = "servicetsunami-code"

_UUID_PATTERN = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.I)
_current_tenant_id: ContextVar[str | None] = ContextVar('_code_tenant_id', default=None)


def set_current_tenant_id(tenant_id: str) -> None:
    """Set the current tenant_id from session state (called by middleware)."""
    if _UUID_PATTERN.match(tenant_id):
        _current_tenant_id.set(tenant_id)


def _resolve_tenant_id(tenant_id: str) -> str:
    """Resolve tenant_id — use per-request value from session state if LLM passes garbage."""
    if _UUID_PATTERN.match(tenant_id):
        return tenant_id
    cached = _current_tenant_id.get()
    if cached:
        return cached
    raise ValueError(f"No valid tenant_id available (got: {tenant_id!r})")


async def _start_code_workflow(task_description: str, tenant_id: str, context: str = "") -> dict:
    """Start a CodeTaskWorkflow on Temporal and wait for the result."""
    from temporalio.client import Client

    # tenant_id is already resolved by start_code_task before entering thread pool

    client = await Client.connect(TEMPORAL_ADDRESS)

    workflow_id = f"code-task-{uuid.uuid4().hex[:8]}"

    handle = await client.start_workflow(
        "CodeTaskWorkflow",
        arg={
            "task_description": task_description,
            "tenant_id": tenant_id,
            "context": context,
        },
        id=workflow_id,
        task_queue=TASK_QUEUE,
    )

    logger.info("Started CodeTaskWorkflow %s for tenant %s", workflow_id, tenant_id)

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


def start_code_task(task_description: str, tenant_id: str = "", context: str = "") -> dict:
    """Start an autonomous code task. Claude Code will implement the task, create a branch, and open a PR.

    Args:
        task_description: What to build or fix. Be specific.
        tenant_id: The tenant ID. Usually auto-resolved from session state — pass empty string to use default.
        context: Optional additional context about the codebase or requirements.

    Returns:
        dict with pr_url, summary, branch, files_changed, success, error.
    """
    # Resolve tenant_id HERE (in the calling context where ContextVar is set)
    # before entering the ThreadPoolExecutor — ContextVars don't propagate to new threads
    tenant_id = _resolve_tenant_id(tenant_id)

    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            import concurrent.futures
            with concurrent.futures.ThreadPoolExecutor() as pool:
                result = pool.submit(asyncio.run, _start_code_workflow(task_description, tenant_id, context)).result()
        else:
            result = loop.run_until_complete(_start_code_workflow(task_description, tenant_id, context))
    except RuntimeError:
        result = asyncio.run(_start_code_workflow(task_description, tenant_id, context))

    return result


start_code_task_tool = FunctionTool(start_code_task)
