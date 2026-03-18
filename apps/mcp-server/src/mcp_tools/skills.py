"""Skill and memory MCP tools.

Skill execution and semantic memory recall tools.
Provides tools for listing/executing file-based skills and semantic
memory recall across the platform.
"""
import json
import logging

import httpx
from mcp.server.fastmcp import Context

from src.mcp_app import mcp
from src.mcp_auth import resolve_tenant_id

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _get_api_base_url() -> str:
    from src.config import settings
    return settings.API_BASE_URL.rstrip("/")


def _get_internal_key() -> str:
    from src.config import settings
    return settings.API_INTERNAL_KEY


# ---------------------------------------------------------------------------
# MCP Tools
# ---------------------------------------------------------------------------


@mcp.tool()
async def list_skills(
    tenant_id: str = "",
    ctx: Context = None,
) -> dict:
    """List all available file-based skills from the platform.

    Args:
        tenant_id: Tenant UUID (resolved from session if omitted).
        ctx: MCP request context (injected automatically).

    Returns:
        Dict with a list of available skills, each with name, description,
        and required inputs.
    """
    tid = resolve_tenant_id(ctx) or tenant_id
    api_base_url = _get_api_base_url()
    internal_key = _get_internal_key()

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(
                f"{api_base_url}/api/v1/skills/library/internal",
                headers={"X-Internal-Key": internal_key},
            )
            if resp.status_code != 200:
                return {
                    "error": f"Failed to list skills: HTTP {resp.status_code}",
                    "detail": resp.text[:500],
                }
            skills = resp.json()
            return {
                "status": "success",
                "skills": [
                    {
                        "name": s.get("name"),
                        "description": s.get("description"),
                        "inputs": s.get("inputs", []),
                    }
                    for s in skills
                ],
                "count": len(skills),
            }
    except Exception as e:
        logger.exception("list_skills failed")
        return {"error": f"Failed to list skills: {str(e)}"}


@mcp.tool()
async def run_skill(
    skill_name: str,
    inputs: str,
    tenant_id: str = "",
    ctx: Context = None,
) -> dict:
    """Execute a file-based skill by name with the given JSON inputs.

    Args:
        skill_name: The exact name of the skill to run
            (e.g. "Scrape Competitor SEO"). Required.
        inputs: JSON string of input parameters
            (e.g. '{"url": "https://example.com"}'). Required.
        tenant_id: Tenant UUID (resolved from session if omitted).
        ctx: MCP request context (injected automatically).

    Returns:
        Dict with the skill execution result or error.
    """
    tid = resolve_tenant_id(ctx) or tenant_id
    if not skill_name:
        return {"error": "skill_name is required."}
    if not inputs:
        return {"error": "inputs is required (JSON string)."}

    try:
        input_data = json.loads(inputs)
    except json.JSONDecodeError:
        return {"error": f"Invalid JSON inputs: {inputs}"}

    api_base_url = _get_api_base_url()
    internal_key = _get_internal_key()

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(
                f"{api_base_url}/api/v1/skills/library/internal/execute",
                headers={"X-Internal-Key": internal_key},
                json={"skill_name": skill_name, "inputs": input_data},
            )
            if resp.status_code != 200:
                return {
                    "error": f"Skill execution failed: HTTP {resp.status_code}",
                    "detail": resp.text[:500],
                }
            return {"status": "success", **resp.json()}
    except Exception as e:
        logger.exception("run_skill failed")
        return {"error": f"Failed to run skill: {str(e)}"}


@mcp.tool()
async def match_skills_to_context(
    user_message: str,
    tenant_id: str = "",
    ctx: Context = None,
) -> dict:
    """Find skills that semantically match a user's message.

    Use this to check if there's a relevant skill before responding.
    Returns matched skills with similarity scores.

    Args:
        user_message: The user's message to match against skill descriptions. Required.
        tenant_id: Tenant UUID (resolved from session if omitted).
        ctx: MCP request context (injected automatically).

    Returns:
        Dict with matched skills and their similarity scores.
    """
    tid = resolve_tenant_id(ctx) or tenant_id
    if not user_message:
        return {"error": "user_message is required."}

    api_base_url = _get_api_base_url()
    internal_key = _get_internal_key()

    try:
        params: dict = {"q": user_message, "limit": 3}
        if tid:
            params["tenant_id"] = tid

        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(
                f"{api_base_url}/api/v1/skills/library/match",
                params=params,
                headers={"X-Internal-Key": internal_key},
            )
            if resp.status_code == 200:
                return {"status": "success", **resp.json()}
            return {"matches": [], "error": f"HTTP {resp.status_code}"}
    except Exception as e:
        logger.warning("match_skills_to_context failed: %s", e)
        return {"matches": []}


@mcp.tool()
async def recall_memory(
    query: str,
    tenant_id: str = "",
    types: str = "",
    limit: int = 10,
    ctx: Context = None,
) -> dict:
    """Semantic search across all memory — entities, activities, past conversations.

    Use this to recall relevant context about the user, their business,
    or past interactions.

    Args:
        query: What to search for in memory. Required.
        tenant_id: Tenant UUID (resolved from session if omitted).
        types: Comma-separated content types to filter
            (entity, memory_activity, skill, chat_message). Empty = all.
        limit: Max results to return (default 10).
        ctx: MCP request context (injected automatically).

    Returns:
        Dict with matching memory results and similarity scores.
    """
    tid = resolve_tenant_id(ctx) or tenant_id
    if not query:
        return {"error": "query is required."}

    api_base_url = _get_api_base_url()
    internal_key = _get_internal_key()

    try:
        params: dict = {"q": query, "limit": limit}
        if types:
            params["types"] = types
        if tid:
            params["tenant_id"] = tid

        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(
                f"{api_base_url}/api/v1/memories/search/internal",
                params=params,
                headers={"X-Internal-Key": internal_key},
            )
            if resp.status_code == 200:
                return {"status": "success", **resp.json()}
            return {"results": [], "error": f"HTTP {resp.status_code}"}
    except Exception as e:
        logger.warning("recall_memory failed: %s", e)
        return {"results": []}
