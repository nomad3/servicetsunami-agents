"""Step executor activity for dynamic workflows.

Each step type has a handler. Every call is a Temporal activity with
full retry/timeout/heartbeat support.
"""

import logging
import os
import re
import time
import uuid
from datetime import datetime
from typing import Any, Dict

import httpx
from temporalio import activity

logger = logging.getLogger(__name__)

API_BASE_URL = os.environ.get("API_BASE_URL", "http://api:8000")
API_INTERNAL_KEY = os.environ.get("API_INTERNAL_KEY", "dev_mcp_key")
MCP_TOOLS_URL = os.environ.get("MCP_TOOLS_URL", "http://mcp-tools:8000")


@activity.defn
async def execute_dynamic_step(
    step: dict,
    context: dict,
    tenant_id: str,
    run_id: str,
) -> dict:
    """Execute a single workflow step."""
    step_type = step.get("type", "")
    step_id = step.get("id", "unknown")
    start = time.time()

    activity.heartbeat(f"Starting step: {step_id} ({step_type})")
    logger.info("Executing step %s (type=%s) for run %s", step_id, step_type, run_id[:8])

    # Log step start
    _log_step(run_id, step_id, step_type, "running", step.get("tool") or step.get("agent"))

    try:
        if step_type == "mcp_tool":
            result = await _call_mcp_tool(step, context, tenant_id)
        elif step_type == "agent":
            result = await _call_agent(step, context, tenant_id)
        elif step_type == "condition":
            result = _evaluate_condition(step, context)
        elif step_type == "transform":
            result = _transform_data(step, context)
        else:
            result = {"error": f"Unknown step type: {step_type}"}

        duration_ms = int((time.time() - start) * 1000)
        _log_step(run_id, step_id, step_type, "completed", duration_ms=duration_ms,
                  output=result, tokens=result.get("tokens_used", 0) if isinstance(result, dict) else 0)

        return result

    except Exception as e:
        duration_ms = int((time.time() - start) * 1000)
        _log_step(run_id, step_id, step_type, "failed", duration_ms=duration_ms, error=str(e))
        raise


async def _call_mcp_tool(step: dict, context: dict, tenant_id: str) -> dict:
    """Call an MCP tool via the MCP tools server."""
    tool_name = step.get("tool", "")
    params = _resolve_params(step.get("params", {}), context)
    params["tenant_id"] = tenant_id

    activity.heartbeat(f"Calling MCP tool: {tool_name}")

    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.post(
            f"{MCP_TOOLS_URL}/mcp",
            json={
                "jsonrpc": "2.0",
                "method": "tools/call",
                "params": {"name": tool_name, "arguments": params},
                "id": str(uuid.uuid4()),
            },
            headers={
                "X-Internal-Key": API_INTERNAL_KEY,
                "X-Tenant-Id": tenant_id,
            },
        )
        data = resp.json()
        if "result" in data:
            content = data["result"].get("content", [])
            if content and isinstance(content, list):
                text = content[0].get("text", "") if content[0].get("type") == "text" else str(content)
                try:
                    import json
                    return json.loads(text)
                except (json.JSONDecodeError, TypeError):
                    return {"result": text}
            return {"result": content}
        if "error" in data:
            return {"error": data["error"].get("message", str(data["error"]))}
        return data


async def _call_agent(step: dict, context: dict, tenant_id: str) -> dict:
    """Call an agent via the internal chat API."""
    agent_slug = step.get("agent", "luna")
    prompt = _resolve_template(step.get("prompt", ""), context)

    activity.heartbeat(f"Running agent: {agent_slug}")

    from app.db.session import SessionLocal
    from app.services.agent_router import route_and_execute

    db = SessionLocal()
    try:
        response_text, metadata = route_and_execute(
            db,
            tenant_id=uuid.UUID(tenant_id),
            user_id=uuid.UUID(tenant_id),
            message=prompt,
            agent_slug=agent_slug,
            channel="workflow",
        )
        return {
            "response": response_text or "",
            "metadata": metadata or {},
            "tokens_used": (metadata or {}).get("input_tokens", 0) + (metadata or {}).get("output_tokens", 0),
            "cost_usd": (metadata or {}).get("cost_usd", 0.0),
            "platform": (metadata or {}).get("platform", "claude_code"),
        }
    finally:
        db.close()


def _evaluate_condition(step: dict, context: dict) -> dict:
    """Evaluate a condition expression."""
    expr = step.get("if", step.get("condition", "false"))
    resolved = _resolve_template(str(expr), context)

    import operator
    ops = {
        ">=": operator.ge, "<=": operator.le,
        "!=": operator.ne, "==": operator.eq,
        ">": operator.gt, "<": operator.lt,
    }

    for op_str, op_fn in sorted(ops.items(), key=lambda x: -len(x[0])):
        if op_str in resolved:
            left, right = resolved.split(op_str, 1)
            left = left.strip()
            right = right.strip().strip("'\"")
            try:
                passed = op_fn(float(left), float(right))
            except ValueError:
                passed = op_fn(left, right)
            return {"passed": passed, "expression": expr, "resolved": resolved}

    return {"passed": bool(resolved and resolved.lower() not in ("false", "0", "none", "null", ""))}


def _transform_data(step: dict, context: dict) -> dict:
    """Simple data transformations."""
    operation = step.get("operation", "identity")
    input_ref = step.get("input", "")
    data = _resolve_value(input_ref, context) if input_ref else context

    if operation == "count":
        return {"count": len(data) if isinstance(data, (list, dict)) else 0}
    elif operation == "keys":
        return {"keys": list(data.keys()) if isinstance(data, dict) else []}
    elif operation == "flatten":
        if isinstance(data, list):
            flat = []
            for item in data:
                if isinstance(item, list):
                    flat.extend(item)
                else:
                    flat.append(item)
            return {"result": flat}
    elif operation == "filter":
        field = step.get("field", "")
        value = step.get("value", "")
        if isinstance(data, list):
            return {"result": [d for d in data if isinstance(d, dict) and str(d.get(field)) == str(value)]}
    elif operation == "map":
        field = step.get("field", "")
        if isinstance(data, list):
            return {"result": [d.get(field) if isinstance(d, dict) else d for d in data]}

    return {"result": data}


def _resolve_template(template: str, context: dict) -> str:
    """Replace {{var.path}} placeholders with context values."""
    def replacer(match):
        path = match.group(1).strip()
        value = _resolve_path(path, context)
        return str(value) if value is not None else match.group(0)
    return re.sub(r"\{\{(.+?)\}\}", replacer, template)


def _resolve_params(params: dict, context: dict) -> dict:
    """Resolve all template references in a params dict."""
    resolved = {}
    for k, v in params.items():
        if isinstance(v, str):
            resolved[k] = _resolve_template(v, context)
        elif isinstance(v, dict):
            resolved[k] = _resolve_params(v, context)
        elif isinstance(v, list):
            resolved[k] = [_resolve_template(str(i), context) if isinstance(i, str) else i for i in v]
        else:
            resolved[k] = v
    return resolved


def _resolve_value(ref: str, context: dict) -> Any:
    """Resolve a {{variable}} reference."""
    if isinstance(ref, str) and ref.strip().startswith("{{"):
        path = ref.strip()[2:-2].strip()
        return _resolve_path(path, context)
    return ref


def _resolve_path(path: str, context: dict) -> Any:
    """Resolve a dot-separated path in context."""
    value = context
    for key in path.split("."):
        if isinstance(value, dict):
            value = value.get(key)
        elif isinstance(value, list) and key.isdigit():
            idx = int(key)
            value = value[idx] if idx < len(value) else None
        elif hasattr(value, key):
            value = getattr(value, key)
        else:
            return None
    return value


def _log_step(run_id: str, step_id: str, step_type: str, status: str,
              step_name: str = None, duration_ms: int = None,
              output: dict = None, error: str = None, tokens: int = 0):
    """Log step execution to the database."""
    try:
        from app.db.session import SessionLocal
        from sqlalchemy import text

        db = SessionLocal()
        try:
            if status == "running":
                db.execute(text("""
                    INSERT INTO workflow_step_logs (id, run_id, step_id, step_type, step_name, status, started_at)
                    VALUES (gen_random_uuid(), CAST(:run_id AS uuid), :step_id, :step_type, :step_name, 'running', NOW())
                    ON CONFLICT DO NOTHING
                """), {"run_id": run_id, "step_id": step_id, "step_type": step_type, "step_name": step_name})
            else:
                db.execute(text("""
                    UPDATE workflow_step_logs
                    SET status = :status, completed_at = NOW(), duration_ms = :duration_ms,
                        output_data = CAST(:output AS jsonb), error = :error, tokens_used = :tokens
                    WHERE run_id = CAST(:run_id AS uuid) AND step_id = :step_id AND status = 'running'
                """), {
                    "run_id": run_id, "step_id": step_id, "status": status,
                    "duration_ms": duration_ms,
                    "output": __import__("json").dumps(output)[:5000] if output else None,
                    "error": error, "tokens": tokens,
                })
            db.commit()
        finally:
            db.close()
    except Exception as e:
        logger.debug("Step log failed: %s", e)
