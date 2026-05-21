"""Higgsfield OAuth token lifecycle helpers (#298).

Mirrors the Google OAuth pattern in
``app/workflows/activities/inbox_monitor.py`` (``_refresh_google_token``
+ ``_get_google_token``). Callers — the MCP gateway, agent_router,
and any future Higgsfield-aware activity — invoke
``get_higgsfield_token_for_tenant`` and get back a fresh access_token
without having to know whether the stored one expired.

Why this exists:

Today, when a Higgsfield API call hits 401, the user has to manually
disconnect + reconnect via the OAuth flow. That's the reactive pattern.
This module makes refresh PROACTIVE: if a refresh_token is stored,
we exchange it for a new access_token on every call. The cost is one
extra HTTP roundtrip per Higgsfield-dispatching request; the benefit
is that expired-access-token-only states heal themselves.

What's NOT in this module (yet):

- A scheduled poll that pre-refreshes tokens before they expire. The
  on-call refresh path is sufficient for Phase 1 — refresh is cheap
  and Higgsfield's rate limits don't bite on token endpoint use.
  A Temporal workflow that polls ``integration_credentials.expires_at``
  is a Phase 2 follow-up; same pattern would serve Google + Microsoft.

- Wiring into ``higgsfield_mcp.py``. That's a one-line call-site
  change per caller — separate PR to avoid mixing the helper ship
  with the call-site cutover.

Safety notes:

- Refresh failures fall back to the stored access_token (may be
  expired). The caller then hits 401 and surfaces it the old way —
  no behavior regression.
- Higgsfield may not return a refresh_token in all OAuth responses;
  in that case ``get_higgsfield_token_for_tenant`` returns the
  stored access_token directly without attempting refresh.
- Network failures during refresh are swallowed with a warning log;
  fall back to stored token.
"""
from __future__ import annotations

import logging
import os
import uuid
from typing import Optional

import httpx
from sqlalchemy.orm import Session

from app.models.integration_config import IntegrationConfig
from app.services.integration_secrets import retrieve_credentials_for_skill

logger = logging.getLogger(__name__)


# Same env knob as the OAuth handler so dev + test environments can
# point at a mock token endpoint without code changes.
HIGGSFIELD_TOKEN_URL = os.environ.get(
    "HIGGSFIELD_TOKEN_URL", "https://higgsfield.ai/oauth/token"
)


def refresh_higgsfield_access_token(
    *,
    refresh_token: str,
    client_id: Optional[str] = None,
    client_secret: Optional[str] = None,
    timeout: float = 10.0,
) -> Optional[str]:
    """Exchange a Higgsfield refresh_token for a fresh access_token.

    Returns the new access_token on success, or None on any failure.
    Does NOT raise — callers fall back to the stored token (may be
    expired) and let the upstream 401 surface the way it does today.

    ``client_id`` / ``client_secret`` default to env vars if unset.
    Higgsfield's OAuth handler stores these alongside the token
    payload when the user first connects (see
    ``higgsfield_auth.py:_persist_creds``); call sites that already
    have them on hand should pass them through to save a vault read.
    """
    cid = client_id or os.environ.get("HIGGSFIELD_CLIENT_ID", "")
    csec = client_secret or os.environ.get("HIGGSFIELD_CLIENT_SECRET", "")
    payload = {
        "grant_type": "refresh_token",
        "refresh_token": refresh_token,
    }
    if cid:
        payload["client_id"] = cid
    if csec:
        payload["client_secret"] = csec

    try:
        with httpx.Client(timeout=timeout) as client:
            resp = client.post(HIGGSFIELD_TOKEN_URL, data=payload)
            resp.raise_for_status()
            data = resp.json()
            new_access = data.get("access_token")
            if not new_access:
                logger.warning(
                    "higgsfield_oauth: refresh response missing access_token: %s",
                    data,
                )
                return None
            return new_access
    except httpx.HTTPStatusError as exc:
        logger.warning(
            "higgsfield_oauth: refresh HTTP %s: %s",
            exc.response.status_code, exc.response.text[:200],
        )
        return None
    except (httpx.RequestError, ValueError) as exc:
        logger.warning(
            "higgsfield_oauth: refresh failed: %s", exc,
        )
        return None


def get_higgsfield_token_for_tenant(
    db: Session,
    tenant_id: str,
    *,
    integration_name: str = "higgsfield",
) -> Optional[str]:
    """Return a usable Higgsfield access_token for a tenant.

    Tries refresh-via-refresh_token first when a refresh_token is
    available in the credential vault. Falls back to the stored
    access_token on refresh failure. Returns None when the tenant
    has no Higgsfield integration configured (caller should treat
    as "not connected" and surface the user-facing reconnect prompt).

    Same shape as ``inbox_monitor._get_google_token`` — call sites
    should be able to swap providers by changing the integration_name
    and import line.
    """
    try:
        tid = uuid.UUID(tenant_id) if isinstance(tenant_id, str) else tenant_id
    except (ValueError, TypeError) as exc:
        logger.warning(
            "higgsfield_oauth: bad tenant_id %r: %s", tenant_id, exc,
        )
        return None

    config = (
        db.query(IntegrationConfig)
        .filter(
            IntegrationConfig.tenant_id == tid,
            IntegrationConfig.integration_name == integration_name,
            IntegrationConfig.enabled.is_(True),
        )
        .first()
    )
    if not config:
        return None

    creds = retrieve_credentials_for_skill(db, config.id, tid)
    refresh_tok = creds.get("refresh_token")

    if refresh_tok:
        new_token = refresh_higgsfield_access_token(
            refresh_token=refresh_tok,
            client_id=creds.get("client_id"),
            client_secret=creds.get("client_secret"),
        )
        if new_token:
            return new_token

    # Fall back to stored access token. May be expired — caller sees
    # 401 and surfaces the existing manual-reconnect path.
    return creds.get("access_token") or creds.get("oauth_token")


__all__ = [
    "refresh_higgsfield_access_token",
    "get_higgsfield_token_for_tenant",
    "HIGGSFIELD_TOKEN_URL",
]
