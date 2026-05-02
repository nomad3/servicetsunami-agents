"""Temporal activities for the Teams channel monitor.

The TeamsMonitorWorkflow runs a single activity per tick that delegates
to ``teams_service.monitor_tick`` — keeping all the inbound-DM gating,
allowlist enforcement, idempotent dedup, and Graph token refresh in
one place (the service module). The activity is just the Temporal
boundary so heartbeats / retries / continue_as_new are handled at the
workflow layer.
"""
import asyncio
import logging
from typing import Any, Dict

from temporalio import activity

logger = logging.getLogger(__name__)


@activity.defn(name="teams_monitor_tick")
async def teams_monitor_tick(tenant_id: str, account_id: str = "default") -> Dict[str, Any]:
    """Run one Teams Monitor poll for a tenant.

    Wraps ``app.services.teams_service.teams_service.monitor_tick``.
    The service handles credential lookup, Graph fan-out, allowlist
    enforcement, dedup against ``acct.config.processed_ids``, and
    safely returns even when the Teams account is no longer enabled
    (so workflow restarts after a disable don't error out).

    Returns the tick result dict; the workflow uses it for logging only.
    """
    # Lazy import keeps temporal worker startup fast — the service
    # pulls in httpx, sqlalchemy, etc.
    from app.services.teams_service import teams_service

    try:
        result = await teams_service.monitor_tick(tenant_id, account_id)
        return result or {"ok": False, "reason": "monitor_tick returned None"}
    except asyncio.CancelledError:
        # Honored by Temporal so the worker can shut down cleanly.
        raise
    except Exception as e:
        # Don't let a single bad tick poison the workflow. Log + return
        # a structured error so the workflow keeps continuing-as-new.
        logger.exception(
            "Teams monitor tick failed for tenant=%s account=%s",
            str(tenant_id)[:8], account_id,
        )
        return {"ok": False, "reason": f"exception: {e!r}"}
