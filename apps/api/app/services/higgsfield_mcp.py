"""Higgsfield MCP source registration.

After a tenant completes the Higgsfield OAuth dance (via
`/api/v1/higgsfield-auth/submit-code`), we register a per-tenant MCP
server connector pointing at Higgsfield's MCP endpoint. Tools surface
through the existing `discover_mcp_tools` / `call_mcp_tool` path the
Marketing/Sales specialist agent already speaks.

The server URL preference order is:

  1. `mcp_endpoint` returned by Higgsfield's token endpoint (per-account)
  2. `HIGGSFIELD_MCP_URL` env override (operator pin)
  3. `https://api.higgsfield.ai/mcp` — canonical guess per Wave 1a plan

This module is intentionally thin: it owns the binding between
"the tenant has higgsfield credentials" and "an mcp_server_connectors
row exists for the tenant pointing at Higgsfield". Discovery and tool
calls go through `apps/api/app/services/mcp_server_connectors.py` and
the existing MCP infrastructure — no per-provider tool registry needed.

Design: docs/plans/2026-05-18-cli-integration-catalog.md (Wave 1a)
"""
from __future__ import annotations

import logging
import os
import uuid
from typing import Optional

from sqlalchemy.orm import Session

from app.models.mcp_server_connector import MCPServerConnector
from app.services import mcp_server_connectors

logger = logging.getLogger(__name__)


HIGGSFIELD_CONNECTOR_NAME = "higgsfield"

# Canonical guess per Wave 1a plan. Higgsfield's public CLI docs reference
# "the Higgsfield MCP server URL" without printing the literal value, so
# we use the most plausible api.higgsfield.ai/mcp path. If their token
# response includes an `mcp_endpoint` field we prefer that.
_DEFAULT_MCP_URL = "https://api.higgsfield.ai/mcp"


def _resolve_mcp_url(oauth_blob: dict) -> str:
    """Pick the most authoritative Higgsfield MCP server URL available."""
    endpoint = oauth_blob.get("mcp_endpoint")
    if isinstance(endpoint, str) and endpoint.startswith("http"):
        return endpoint
    env_override = os.environ.get("HIGGSFIELD_MCP_URL")
    if env_override:
        return env_override
    return _DEFAULT_MCP_URL


def _existing_connector(
    db: Session, tenant_id: uuid.UUID
) -> Optional[MCPServerConnector]:
    return (
        db.query(MCPServerConnector)
        .filter(
            MCPServerConnector.tenant_id == tenant_id,
            MCPServerConnector.name == HIGGSFIELD_CONNECTOR_NAME,
        )
        .first()
    )


def register_for_tenant(
    db: Session,
    tenant_id: uuid.UUID,
    oauth_blob: dict,
) -> MCPServerConnector:
    """Upsert the Higgsfield MCP server connector for this tenant.

    Called from `higgsfield_auth.HiggsfieldAuthManager._persist_creds`
    after the OAuth blob lands in the vault. Idempotent — if a row
    already exists for this tenant, the access_token and URL are
    refreshed in-place.

    Authentication mode is `bearer` — the encrypted MCP layer attaches
    `Authorization: Bearer <access_token>` to every JSON-RPC request.
    Tenants see "powered by your Higgsfield account credits" on the
    integration card; calls bill against the tenant's own Higgsfield
    subscription (BYO-account model, per the Wave 1a constraint).
    """
    access_token = oauth_blob.get("access_token")
    if not access_token:
        raise ValueError(
            "Higgsfield OAuth blob is missing access_token; refusing to "
            "register an MCP source with no auth."
        )

    server_url = _resolve_mcp_url(oauth_blob)
    existing = _existing_connector(db, tenant_id)

    if existing is None:
        connector = mcp_server_connectors.create_mcp_server(
            db,
            tenant_id=tenant_id,
            name=HIGGSFIELD_CONNECTOR_NAME,
            server_url=server_url,
            transport="sse",
            auth_type="bearer",
            auth_token=access_token,
            description=(
                "Higgsfield creative-content MCP server. Tools include Soul, "
                "Cinema Studio, Flux, Seedream, Nano Banana (image), Seedance, "
                "Kling, Veo, Minimax Hailuo (video), plus Ad Engine + virality "
                "prediction. Calls bill against the tenant's own Higgsfield "
                "account credits."
            ),
            enabled=True,
        )
        logger.info(
            "Registered Higgsfield MCP source for tenant %s at %s",
            str(tenant_id)[:8],
            server_url,
        )
        return connector

    # Refresh existing row in-place — same connector_id so any agent
    # bindings stay valid.
    existing.server_url = server_url
    existing.auth_token = access_token
    existing.auth_type = "bearer"
    existing.transport = "sse"
    existing.enabled = True
    db.add(existing)
    db.commit()
    db.refresh(existing)
    logger.info(
        "Refreshed Higgsfield MCP source for tenant %s at %s",
        str(tenant_id)[:8],
        server_url,
    )
    return existing


def unregister_for_tenant(db: Session, tenant_id: uuid.UUID) -> bool:
    """Drop the per-tenant Higgsfield MCP connector and call logs.

    Called from the /disconnect route after credentials are revoked.
    Returns True if a row was removed, False if there wasn't one.
    """
    existing = _existing_connector(db, tenant_id)
    if not existing:
        return False
    return mcp_server_connectors.delete_mcp_server(
        db, tenant_id=tenant_id, connector_id=existing.id
    )


# ── Tool group binding ──────────────────────────────────────────────────
#
# The Marketing/Sales specialist agent's `tool_groups` includes
# "higgsfield" (declared in apps/api/app/services/tool_groups.py).
# At runtime the agent calls `discover_mcp_tools` against this tenant's
# Higgsfield connector and surfaces the discovered tool names — we
# don't need to enumerate them here because the MCP server is the
# source of truth. The names below are the documented tool surface as
# of Wave 1a (2026-05-18) and are used by `tool_groups.TOOL_GROUPS`
# as the static fallback when discovery hasn't run yet.
HIGGSFIELD_TOOL_NAMES = [
    # Image generation
    "higgsfield_soul",
    "higgsfield_cinema_studio",
    "higgsfield_flux",
    "higgsfield_seedream",
    "higgsfield_nano_banana",
    # Video generation
    "higgsfield_seedance",
    "higgsfield_kling",
    "higgsfield_veo",
    "higgsfield_minimax_hailuo",
    # Marketing / higher-order tools
    "higgsfield_ad_engine",
    "higgsfield_virality_prediction",
]
