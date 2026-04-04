"""Dynamic workflow MCP tools — Luna can create, run, and manage workflows via chat."""

import logging
import os
from typing import Optional

import httpx
from mcp.server.fastmcp import Context

from src.mcp_app import mcp
from src.mcp_auth import resolve_tenant_id

logger = logging.getLogger(__name__)

API_BASE_URL = os.environ.get("API_BASE_URL", "http://api:8000")
API_INTERNAL_KEY = os.environ.get("MCP_API_KEY", "dev_mcp_key")


async def _api_call(method: str, path: str, tenant_id: str, json_data: dict = None) -> dict:
    """Call the internal API."""
    async with httpx.AsyncClient(timeout=15.0) as client:
        kwargs = {"headers": {"X-Internal-Key": API_INTERNAL_KEY, "X-Tenant-Id": tenant_id}}
        if json_data is not None and method.lower() != "get":
            kwargs["json"] = json_data
        resp = await getattr(client, method)(
            f"{API_BASE_URL}/api/v1/dynamic-workflows{path}",
            **kwargs,
        )
        if resp.status_code in (200, 201):
            return resp.json()
        if resp.status_code == 204:
            return {"status": "success"}
        return {"error": f"API returned {resp.status_code}: {resp.text[:200]}"}


@mcp.tool()
async def create_dynamic_workflow(
    name: str,
    description: str = "",
    steps: list = None,
    trigger_type: str = "manual",
    trigger_schedule: str = "",
    tags: list = None,
    tenant_id: str = "",
    ctx: Context = None,
) -> dict:
    """Create a new dynamic workflow.

    Args:
        name: Workflow name (e.g. "Daily Inbox Scanner")
        description: What the workflow does
        steps: List of step definitions. Each step is a dict with:
            - id: unique step identifier
            - type: "mcp_tool", "agent", "condition", "for_each", "wait", "transform"
            - tool: MCP tool name (for mcp_tool type)
            - agent: Agent slug like "luna" (for agent type)
            - prompt: Prompt template with {{variables}} (for agent type)
            - params: Parameters dict (for mcp_tool type)
            - output: Variable name to store result
            - if: Condition expression (for condition type)
        trigger_type: "manual", "cron", "interval", "webhook", "event"
        trigger_schedule: Cron expression for cron triggers (e.g. "0 8 * * *")
        tags: List of tags
        tenant_id: Tenant UUID
    """
    tid = resolve_tenant_id(ctx) or tenant_id
    if not steps:
        steps = []

    definition = {"steps": steps}
    trigger_config = {"type": trigger_type}
    if trigger_schedule:
        trigger_config["schedule"] = trigger_schedule

    result = await _api_call("post", "/internal/create", tid, {
        "name": name,
        "description": description,
        "definition": definition,
        "trigger_config": trigger_config,
        "tags": tags or [],
    })

    if "error" not in result:
        return {
            "status": "created",
            "id": result.get("id"),
            "name": result.get("name"),
            "steps": len(steps),
            "trigger": trigger_type,
        }
    return result


@mcp.tool()
async def list_dynamic_workflows(
    status: str = "",
    tenant_id: str = "",
    ctx: Context = None,
) -> dict:
    """List all dynamic workflows for the tenant.

    Args:
        status: Filter by status (draft, active, paused, archived). Empty for all.
        tenant_id: Tenant UUID
    """
    tid = resolve_tenant_id(ctx) or tenant_id
    path = "/internal/list"
    if status:
        path += f"?status={status}"
    result = await _api_call("get", path, tid)

    if isinstance(result, list):
        workflows = []
        for wf in result:
            workflows.append({
                "id": wf["id"],
                "name": wf["name"],
                "status": wf["status"],
                "steps": len(wf.get("definition", {}).get("steps", [])),
                "trigger": (wf.get("trigger_config") or {}).get("type", "manual"),
                "runs": wf.get("run_count", 0),
                "success_rate": wf.get("success_rate"),
                "tags": wf.get("tags", []),
            })
        return {"workflows": workflows, "count": len(workflows)}
    return result


@mcp.tool()
async def run_dynamic_workflow(
    workflow_id: str,
    input_data: dict = None,
    tenant_id: str = "",
    ctx: Context = None,
) -> dict:
    """Run a dynamic workflow manually.

    Args:
        workflow_id: UUID of the workflow to run
        input_data: Optional input data passed to the workflow
        tenant_id: Tenant UUID
    """
    tid = resolve_tenant_id(ctx) or tenant_id
    result = await _api_call("post", f"/internal/{workflow_id}/run", tid, {
        "input_data": input_data or {},
    })

    if "error" not in result:
        return {
            "status": "started",
            "run_id": result.get("id"),
            "workflow_id": workflow_id,
        }
    return result


@mcp.tool()
async def get_workflow_run_status(
    run_id: str,
    tenant_id: str = "",
    ctx: Context = None,
) -> dict:
    """Check the status of a workflow run, including step-by-step results.

    Args:
        run_id: UUID of the workflow run
        tenant_id: Tenant UUID
    """
    tid = resolve_tenant_id(ctx) or tenant_id
    result = await _api_call("get", f"/internal/runs/{run_id}", tid)

    if "error" not in result and "run" in result:
        run = result["run"]
        steps = result.get("steps", [])
        return {
            "status": run["status"],
            "started_at": run.get("started_at"),
            "duration_ms": run.get("duration_ms"),
            "total_tokens": run.get("total_tokens", 0),
            "total_cost_usd": run.get("total_cost_usd", 0),
            "steps": [{
                "id": s["step_id"],
                "type": s["step_type"],
                "status": s["status"],
                "duration_ms": s.get("duration_ms"),
                "tokens": s.get("tokens_used", 0),
                "error": s.get("error"),
            } for s in steps],
        }
    return result


@mcp.tool()
async def activate_dynamic_workflow(
    workflow_id: str,
    tenant_id: str = "",
    ctx: Context = None,
) -> dict:
    """Activate a dynamic workflow (enable its triggers).

    Args:
        workflow_id: UUID of the workflow
        tenant_id: Tenant UUID
    """
    tid = resolve_tenant_id(ctx) or tenant_id
    return await _api_call("post", f"/internal/{workflow_id}/activate", tid)


@mcp.tool()
async def update_dynamic_workflow(
    workflow_id: str,
    tenant_id: str = "",
    name: str = "",
    description: str = "",
    trigger_type: str = "",
    trigger_schedule: str = "",
    definition: dict = None,
    ctx: Context = None,
) -> dict:
    """Update an existing dynamic workflow. Only provided fields are changed; omitted fields keep their current values.

    Args:
        workflow_id: UUID of the workflow to update
        tenant_id: Tenant UUID
        name: New workflow name (leave empty to keep current)
        description: New description (leave empty to keep current)
        trigger_type: New trigger type — "manual", "cron", "interval", "webhook", "event" (leave empty to keep current)
        trigger_schedule: New cron expression for cron triggers (leave empty to keep current)
        definition: Full workflow definition replacement (dict with "steps" list). Omit to keep current.
    """
    tid = resolve_tenant_id(ctx) or tenant_id

    # Fetch current workflow
    current = await _api_call("get", f"/internal/{workflow_id}", tid)
    if "error" in current:
        return current

    # Build update payload merging only provided fields
    update_data = {}
    update_data["name"] = name if name else current.get("name", "")
    update_data["description"] = description if description else current.get("description", "")

    if definition is not None:
        update_data["definition"] = definition
    else:
        update_data["definition"] = current.get("definition", {"steps": []})

    # Merge trigger config
    current_trigger = current.get("trigger_config") or {}
    trigger_config = {}
    trigger_config["type"] = trigger_type if trigger_type else current_trigger.get("type", "manual")
    if trigger_schedule:
        trigger_config["schedule"] = trigger_schedule
    elif "schedule" in current_trigger:
        trigger_config["schedule"] = current_trigger["schedule"]
    update_data["trigger_config"] = trigger_config

    update_data["tags"] = current.get("tags", [])

    result = await _api_call("put", f"/internal/{workflow_id}", tid, json_data=update_data)

    if "error" not in result:
        return {
            "status": "updated",
            "id": result.get("id"),
            "name": result.get("name"),
            "steps": len(result.get("definition", {}).get("steps", [])),
            "trigger": (result.get("trigger_config") or {}).get("type", "manual"),
        }
    return result


@mcp.tool()
async def delete_dynamic_workflow(
    workflow_id: str,
    tenant_id: str = "",
    ctx: Context = None,
) -> dict:
    """Delete a dynamic workflow permanently.

    Args:
        workflow_id: UUID of the workflow to delete
        tenant_id: Tenant UUID
    """
    tid = resolve_tenant_id(ctx) or tenant_id
    result = await _api_call("delete", f"/internal/{workflow_id}", tid)

    if "error" not in result:
        return {
            "status": "deleted",
            "workflow_id": workflow_id,
            "message": f"Workflow {workflow_id} has been permanently deleted.",
        }
    return result


@mcp.tool()
async def install_workflow_template(
    template_id: str,
    tenant_id: str = "",
    ctx: Context = None,
) -> dict:
    """Install a workflow template from the marketplace.

    Args:
        template_id: UUID of the template to install
        tenant_id: Tenant UUID
    """
    tid = resolve_tenant_id(ctx) or tenant_id
    result = await _api_call("post", f"/internal/templates/{template_id}/install", tid)

    if "error" not in result:
        return {
            "status": "installed",
            "id": result.get("id"),
            "name": result.get("name"),
            "steps": len(result.get("definition", {}).get("steps", [])),
        }
    return result
