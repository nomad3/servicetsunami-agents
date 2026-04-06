"""Memory continuity MCP tools — Gap 1 (Session Journal) and Gap 2 (Behavioral Signals).

These tools are called by Temporal dynamic workflows on a schedule:
  - synthesize_daily_journal: nightly at 23:55, turns today's episodes into a journal entry
  - synthesize_weekly_journal: Sundays at 23:00, weekly narrative summary
  - expire_behavioral_signals: nightly at 00:30, marks stale pending signals as ignored
  - get_learning_stats: returns suggestion performance stats for a tenant
"""
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


async def _internal(method: str, path: str, tenant_id: str, json_data: dict = None) -> dict:
    """Call an internal API endpoint."""
    async with httpx.AsyncClient(timeout=30.0) as client:
        kwargs = {
            "headers": {
                "X-Internal-Key": API_INTERNAL_KEY,
                "X-Tenant-Id": tenant_id,
            }
        }
        if json_data is not None and method.lower() != "get":
            kwargs["json"] = json_data
        resp = await getattr(client, method.lower())(
            f"{API_BASE_URL}{path}", **kwargs
        )
    if resp.status_code in (200, 201):
        return resp.json()
    if resp.status_code == 204:
        return {"status": "success"}
    return {"error": f"API {resp.status_code}: {resp.text[:300]}"}


# ---------------------------------------------------------------------------
# Gap 1: Session Journal tools
# ---------------------------------------------------------------------------

@mcp.tool()
async def synthesize_daily_journal(
    tenant_id: str = "",
    ctx: Context = None,
) -> dict:
    """
    Synthesize today's conversation episodes into a session journal entry.

    Reads ConversationEpisode records from today, extracts themes/accomplishments/
    challenges, generates a narrative summary via Ollama, and stores it as a
    SessionJournal entry with embedding.

    Designed to run nightly (cron: '55 23 * * *') so tomorrow's morning briefing
    has fresh narrative context.

    Args:
        tenant_id: Tenant UUID
    """
    tid = resolve_tenant_id(tenant_id, ctx)
    result = await _internal("post", "/api/v1/internal/session-journals/synthesize-daily", tid)
    return result


@mcp.tool()
async def synthesize_weekly_journal(
    tenant_id: str = "",
    ctx: Context = None,
) -> dict:
    """
    Synthesize this week's activity into a weekly session journal entry.

    Aggregates daily journal entries (or episodes) from the past 7 days into a
    cohesive weekly narrative. Designed to run Sundays at 23:00.

    Args:
        tenant_id: Tenant UUID
    """
    tid = resolve_tenant_id(tenant_id, ctx)
    result = await _internal("post", "/api/v1/internal/session-journals/synthesize-weekly", tid)
    return result


@mcp.tool()
async def get_morning_briefing(
    days_lookback: int = 7,
    tenant_id: str = "",
    ctx: Context = None,
) -> dict:
    """
    Get the synthesized morning briefing from recent session journals.

    Returns a warm narrative of the user's last N days of activity, suitable
    for inclusion in Luna's context at session start.

    Args:
        days_lookback: How many days of journals to include (default 7)
        tenant_id: Tenant UUID
    """
    tid = resolve_tenant_id(tenant_id, ctx)
    result = await _internal("get", f"/api/v1/session-journals/morning-briefing?days_lookback={days_lookback}", tid)
    return result


# ---------------------------------------------------------------------------
# Gap 2: Behavioral Signal tools
# ---------------------------------------------------------------------------

@mcp.tool()
async def expire_behavioral_signals(
    tenant_id: str = "",
    ctx: Context = None,
) -> dict:
    """
    Expire stale pending behavioral signals (mark as ignored).

    Any suggestion Luna made that the user hasn't acted on within the signal's
    expires_after_hours window gets marked acted_on=False. This prevents the
    pending queue from growing unbounded and keeps learning stats accurate.

    Designed to run nightly (cron: '30 0 * * *').

    Args:
        tenant_id: Tenant UUID
    """
    tid = resolve_tenant_id(tenant_id, ctx)
    result = await _internal("post", "/api/v1/internal/behavioral-signals/expire", tid)
    return result


@mcp.tool()
async def get_learning_stats(
    days: int = 14,
    tenant_id: str = "",
    ctx: Context = None,
) -> dict:
    """
    Get suggestion performance stats for a tenant (Gap 2: Learning).

    Returns acted_on rates per suggestion_type over the last N days.
    Useful for understanding which of Luna's suggestion types the user
    actually follows through on.

    Args:
        days: Lookback window in days (default 14)
        tenant_id: Tenant UUID
    """
    tid = resolve_tenant_id(tenant_id, ctx)
    result = await _internal("get", f"/api/v1/internal/behavioral-signals/stats?days={days}", tid)
    return result
