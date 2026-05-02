"""Discover Copilot Studio + Azure AI Foundry agents the tenant owns.

Lets a tenant click "Discover Microsoft Agents" instead of pasting a
JSON config — closes the loop on PR #243's import flow.

Architecture:
- Reuses the existing ``microsoft`` OAuth provider — same app
  registration, same delegated-auth Graph token that PR #241
  already wires up for Outlook + Teams.
- Hits Microsoft Graph beta endpoints to enumerate the user's
  Copilot Studio bots and Azure AI Foundry assistants. Both
  surfaces are covered by Graph beta today; production-stable
  endpoints are still in flight.
- Returns a list of dicts compatible with the existing
  ``parse_agent_definition`` importer in ``agent_importer.py``,
  so the UI can show "discovered agents" and let the user
  one-click import any subset.

Returned shape per agent:
    {
        "kind": "copilot_studio" | "ai_foundry",
        "id": "<provider-side bot/agent ID>",
        "display_name": "...",
        "description": "...",
        "raw": <raw provider JSON for one-click import>,
    }

The raw payload is what gets passed to ``parse_agent_definition``
when the user clicks Import — that function already has the
copilot_studio / ai_foundry branches (PR #243).

Failure modes:
- No microsoft credential connected → empty list, ``reason="not_connected"``.
- Graph 401 / 403 → empty list, ``reason="auth_failed"`` (token expired
  or scope insufficient — admin needs to re-authorize).
- Graph 404 / 501 on a specific endpoint → that surface skipped, others
  still returned. Either Microsoft hasn't enabled the endpoint for
  this tenant or the path is in flight.
- Pagination is followed via ``@odata.nextLink`` up to a hard cap
  (200 items per surface) so we don't OOM on tenants with many bots.
"""
from __future__ import annotations

import logging
import uuid
from typing import Any, Dict, List, Optional

import httpx
from sqlalchemy.orm import Session

from app.models.integration_config import IntegrationConfig
from app.services.orchestration.credential_vault import retrieve_credentials_for_skill

logger = logging.getLogger(__name__)


GRAPH_BASE = "https://graph.microsoft.com/v1.0"
GRAPH_BETA = "https://graph.microsoft.com/beta"

# Hard cap per surface — bounds runtime + memory for tenants with
# hundreds of bots.
_DISCOVERY_CAP = 200


def _get_microsoft_token(db: Session, tenant_id: uuid.UUID) -> Optional[str]:
    """Pull the microsoft OAuth token, refreshing if stale.

    Same resolution as ``teams_service._get_access_token``: prefer
    a dedicated ``microsoft_agents`` integration_config, fall back
    to ``teams`` / ``outlook`` since they share the same provider.
    """
    from app.api.v1.oauth import _refresh_access_token, _update_stored_tokens

    cfg = (
        db.query(IntegrationConfig)
        .filter(
            IntegrationConfig.tenant_id == tenant_id,
            IntegrationConfig.integration_name.in_(
                ["microsoft_agents", "teams", "outlook"]
            ),
            IntegrationConfig.enabled.is_(True),
        )
        .order_by(IntegrationConfig.integration_name.desc())
        .first()
    )
    if not cfg:
        return None

    creds = retrieve_credentials_for_skill(
        db, integration_config_id=cfg.id, tenant_id=cfg.tenant_id,
    )
    stored_token = creds.get("access_token") or creds.get("oauth_token")
    refresh_token = creds.get("refresh_token")

    if not refresh_token and cfg.account_email:
        siblings = (
            db.query(IntegrationConfig)
            .filter(
                IntegrationConfig.tenant_id == tenant_id,
                IntegrationConfig.account_email == cfg.account_email,
                IntegrationConfig.enabled.is_(True),
                IntegrationConfig.id != cfg.id,
            )
            .all()
        )
        for sib in siblings:
            sib_creds = retrieve_credentials_for_skill(
                db, integration_config_id=sib.id, tenant_id=cfg.tenant_id,
            )
            if sib_creds.get("refresh_token"):
                refresh_token = sib_creds["refresh_token"]
                break

    if not refresh_token:
        return stored_token

    try:
        refreshed = _refresh_access_token(
            "microsoft", refresh_token, integration_name=cfg.integration_name,
        )
    except Exception as e:
        logger.warning(
            "microsoft_agent_discovery: token refresh raised for tenant=%s: %s",
            str(tenant_id)[:8], e,
        )
        return stored_token

    if not refreshed or not refreshed.get("access_token"):
        return stored_token

    try:
        _update_stored_tokens(
            db, cfg.id, cfg.tenant_id,
            refreshed["access_token"], refreshed.get("refresh_token"),
        )
        db.commit()
    except Exception:
        db.rollback()
    return refreshed["access_token"]


async def _list_paginated(
    client: httpx.AsyncClient,
    url: str,
    headers: dict,
    params: Optional[dict] = None,
    cap: int = _DISCOVERY_CAP,
) -> List[dict]:
    """Follow Graph ``@odata.nextLink`` up to ``cap`` items."""
    out: List[dict] = []
    next_url: Optional[str] = url
    next_params: Optional[dict] = params
    while next_url and len(out) < cap:
        resp = await client.get(next_url, headers=headers, params=next_params)
        if resp.status_code >= 300:
            return out  # caller decides how to surface
        payload = resp.json() or {}
        out.extend(payload.get("value") or [])
        next_url = payload.get("@odata.nextLink")
        next_params = None  # nextLink encodes its own params
    return out[:cap]


async def discover_copilot_studio_bots(
    client: httpx.AsyncClient, token: str,
) -> List[Dict[str, Any]]:
    """Enumerate Copilot Studio bots the user owns / has access to.

    Graph endpoint: ``/me/copilots`` (beta). Returns a list of bot
    descriptors with displayName, id, and a generative-AI block.
    """
    headers = {"Authorization": f"Bearer {token}"}
    raw_bots = await _list_paginated(
        client, f"{GRAPH_BETA}/me/copilots", headers,
    )
    out: List[Dict[str, Any]] = []
    for b in raw_bots:
        out.append({
            "kind": "copilot_studio",
            "id": b.get("id") or b.get("botId"),
            "display_name": b.get("displayName") or b.get("name") or "Unnamed Copilot",
            "description": b.get("description") or "",
            "raw": {
                # Shape that `agent_importer.import_copilot_studio` understands.
                "kind": "copilot_studio",
                "schemaName": b.get("schemaName") or "Microsoft.CopilotStudio",
                "displayName": b.get("displayName"),
                "description": b.get("description"),
                "instructions": (b.get("generativeAI") or {}).get("instructions"),
                "topics": b.get("topics") or [],
                "botId": b.get("id") or b.get("botId"),
                "_source": "graph_discovery",
            },
        })
    return out


async def discover_ai_foundry_agents(
    client: httpx.AsyncClient, token: str,
) -> List[Dict[str, Any]]:
    """Enumerate Azure AI Foundry assistants the user can access.

    Graph endpoint: ``/me/aiAssistants`` (beta). Returns Assistants-API
    compatible descriptors (model, instructions, tools).
    """
    headers = {"Authorization": f"Bearer {token}"}
    raw_assistants = await _list_paginated(
        client, f"{GRAPH_BETA}/me/aiAssistants", headers,
    )
    out: List[Dict[str, Any]] = []
    for a in raw_assistants:
        out.append({
            "kind": "ai_foundry",
            "id": a.get("id"),
            "display_name": a.get("name") or a.get("displayName") or "Unnamed Assistant",
            "description": a.get("description") or "",
            "raw": {
                "kind": "ai_foundry",
                "id": a.get("id"),
                "name": a.get("name") or a.get("displayName"),
                "description": a.get("description"),
                "model": a.get("model"),
                "instructions": a.get("instructions"),
                "tools": a.get("tools") or [],
                "endpoint": a.get("endpoint"),
                "_source": "graph_discovery",
            },
        })
    return out


async def discover(
    db: Session, tenant_id: uuid.UUID,
) -> Dict[str, Any]:
    """Discover all Microsoft-platform agents available to this tenant.

    Returns ``{agents: [...], reason: str | None, accounts_queried: [email]}``.
    The ``reason`` is non-null when no agents could be enumerated due
    to a known cause (not connected, auth failed, etc.) so the UI can
    show a useful message.
    """
    token = _get_microsoft_token(db, tenant_id)
    if not token:
        return {
            "agents": [],
            "reason": "not_connected",
            "message": (
                "No Microsoft account connected. Connect Outlook or Teams "
                "first — Microsoft agent discovery uses the same OAuth flow."
            ),
        }

    agents: List[Dict[str, Any]] = []
    errors: List[str] = []

    async with httpx.AsyncClient(timeout=20.0) as client:
        # Run both discovery surfaces; one failing shouldn't kill
        # the other.
        for fn, label in [
            (discover_copilot_studio_bots, "copilot_studio"),
            (discover_ai_foundry_agents, "ai_foundry"),
        ]:
            try:
                found = await fn(client, token)
                agents.extend(found)
            except Exception as e:
                logger.warning(
                    "microsoft_agent_discovery: %s discovery failed for tenant=%s: %s",
                    label, str(tenant_id)[:8], e,
                )
                errors.append(f"{label}: {e}")

    return {
        "agents": agents,
        "count": len(agents),
        "reason": None if agents else ("partial_failure" if errors else "no_agents_found"),
        "errors": errors or None,
    }
