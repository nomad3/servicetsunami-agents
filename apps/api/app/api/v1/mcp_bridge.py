"""MCP JSON-RPC bridge for external agents.

Exposes a lightweight MCP 2024-11-05 endpoint at ``POST /api/v1/mcp`` so
external agents — Claude Code, Gemini CLI, VS Code Copilot — can discover
and call the current tenant's skills through the standard MCP protocol.

Protocol:
    - Request:  {"jsonrpc": "2.0", "id": ..., "method": "...", "params": {...}}
    - Response: {"jsonrpc": "2.0", "id": ..., "result": {...}}          (success)
                {"jsonrpc": "2.0", "id": ..., "error": {"code": N, "message": ...}}

Supported methods:
    - initialize              — protocol handshake
    - tools/list              — return tenant's skills as MCP tools
    - tools/call              — execute a skill and return its result

Auth: standard ``Authorization: Bearer <jwt>`` via ``get_current_user``.
"""
from typing import Any, Dict, Optional

from fastapi import APIRouter, Body, Depends, HTTPException
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, get_db
from app.models.user import User
from app.services.skill_manager import skill_manager
from app.services.memory_activity import log_activity

# Re-use centralized helpers (single source of truth for tool shape + filter)
from app.api.v1.skills_new import (
    _is_auto_generated_skill,
    _skill_to_mcp_tool,
    _sanitize_tool_name,
)

router = APIRouter()


PROTOCOL_VERSION = "2024-11-05"
SERVER_NAME = "agentprovision-skills"
SERVER_VERSION = "1.0.0"


def _rpc_error(request_id: Any, code: int, message: str) -> Dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}}


def _rpc_result(request_id: Any, result: Any) -> Dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "result": result}


@router.post("")
def mcp_rpc(
    payload: Dict[str, Any] = Body(...),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """JSON-RPC entrypoint. Routes on ``method`` field."""
    request_id = payload.get("id")
    method = payload.get("method", "")
    params = payload.get("params") or {}

    tenant_id = str(current_user.tenant_id)

    if method == "initialize":
        return _rpc_result(request_id, {
            "protocolVersion": PROTOCOL_VERSION,
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
        })

    if method == "tools/list":
        skills = skill_manager.list_skills(tenant_id)
        skills = [s for s in skills if not _is_auto_generated_skill(s)]
        return _rpc_result(request_id, {
            "tools": [_skill_to_mcp_tool(s) for s in skills],
        })

    if method == "tools/call":
        tool_name = params.get("name", "")
        arguments = params.get("arguments") or {}

        # tools/list advertises "skill_<slug>" — strip prefix + match
        if not tool_name.startswith("skill_"):
            return _rpc_error(request_id, -32602, f"Unknown tool: {tool_name}")

        want = tool_name[len("skill_"):]

        # Resolve by sanitized-slug → full skill (same sanitizer used in manifest)
        target = None
        for s in skill_manager.list_skills(tenant_id):
            if _is_auto_generated_skill(s):
                continue
            if _sanitize_tool_name(s.slug or s.name) == want:
                target = s
                break

        if target is None:
            return _rpc_error(request_id, -32602, f"Skill not found: {tool_name}")

        result = skill_manager.execute_skill(target.name, arguments, tenant_id=tenant_id)

        # Audit: MCP-invoked skills should leave the same trail as UI-invoked ones
        try:
            log_activity(
                db,
                tenant_id=current_user.tenant_id,
                event_type="action_completed" if "error" not in result else "action_failed",
                description=f"MCP skill executed: {target.name}",
                source="mcp",
                event_metadata={"skill_name": target.name, "arguments": arguments, "action": "skill_executed_mcp"},
            )
        except Exception:
            pass  # never let audit failure break tool calls

        if "error" in result:
            # MCP protocol says tool errors go in `result.isError` not `error`
            return _rpc_result(request_id, {
                "content": [{"type": "text", "text": str(result["error"])}],
                "isError": True,
            })

        # Wrap the skill's return dict in MCP content blocks
        import json as _json
        return _rpc_result(request_id, {
            "content": [{"type": "text", "text": _json.dumps(result, default=str)}],
            "isError": False,
        })

    return _rpc_error(request_id, -32601, f"Method not found: {method}")
