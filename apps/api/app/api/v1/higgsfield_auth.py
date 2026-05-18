"""Higgsfield CLI OAuth login flow — api-owned PKCE redirect exchange.

Mirrors the gemini-cli OAuth flow at `apps/api/app/api/v1/gemini_cli_auth.py`.
Higgsfield's `higgsfield auth login` runs the same browser-based OAuth
shape; we keep the dance entirely on the api side rather than shelling
out to the CLI binary because:

  * the MCP server URL is the only thing leaf agents need at runtime
    (no local `higgsfield` binary in the code-worker image), and
  * the OAuth flow is the same paste-back PKCE shape we already
    operate for Gemini — owning it directly avoids the four
    concurrency hazards documented in gemini_cli_auth.py.

This module exposes:

  POST /higgsfield-auth/start         — mint PKCE state + browser URL
  GET  /higgsfield-auth/status        — poll for connected / pending
  POST /higgsfield-auth/submit-code   — exchange the user-pasted code
  POST /higgsfield-auth/cancel        — abandon the in-flight login
  POST /higgsfield-auth/disconnect    — revoke vault rows for this tenant

The resulting OAuth blob is stored in the encrypted vault under
`credential_key="higgsfield_oauth"` with `credential_type="oauth_token"`
(the contract called out in the Wave 1a plan).

Multi-tenant ToS for Higgsfield is unconfirmed (see plan doc), so this
module ships BYO-Higgsfield-account-per-tenant ONLY. Every tenant goes
through their own OAuth. There is no shared-founder-account code path
here — if that ever ships it lives behind a feature flag, not in this
module.

Design: docs/plans/2026-05-18-cli-integration-catalog.md (Wave 1a)
"""
from __future__ import annotations

import base64
import hashlib
import logging
import os
import secrets
import threading
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, Optional
from urllib.parse import urlencode

import httpx
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.api import deps
from app.db.session import SessionLocal
from app.models.integration_config import IntegrationConfig
from app.models.integration_credential import IntegrationCredential
from app.models.user import User
from app.services.orchestration.credential_vault import store_credential

logger = logging.getLogger(__name__)

router = APIRouter()


# ── Higgsfield OAuth constants ───────────────────────────────────────────
#
# Higgsfield doesn't publish its OAuth client_id/secret in a public bundle
# (unlike gemini-cli), so we read both from env. The CLI binary holds
# the client values internally; for the api-owned PKCE flow we need our
# own registered client. Operators set these once during deployment.
#
# Defaults below are deliberately placeholders. Without env override the
# /start route returns 503 with an actionable message — same shape as
# gemini-cli's 503 when the bundle is missing.
HIGGSFIELD_AUTH_URL = os.environ.get(
    "HIGGSFIELD_AUTH_URL", "https://higgsfield.ai/oauth/authorize"
)
HIGGSFIELD_TOKEN_URL = os.environ.get(
    "HIGGSFIELD_TOKEN_URL", "https://higgsfield.ai/oauth/token"
)
HIGGSFIELD_OAUTH_REDIRECT_URI = os.environ.get(
    "HIGGSFIELD_OAUTH_REDIRECT_URI", "https://higgsfield.ai/cli/authcode"
)
HIGGSFIELD_OAUTH_SCOPE = os.environ.get(
    "HIGGSFIELD_OAUTH_SCOPE", "mcp.read mcp.write generate"
)

# Server-side state TTL. Higgsfield's authorization codes are short-lived;
# 10 minutes matches the gemini flow and gives the user enough time to
# paste the code without leaving stale state lying around indefinitely.
LOGIN_TTL_SECONDS = 600

# httpx timeout for the token exchange. Higgsfield's token endpoint is
# externally hosted; give it generous time but cap it.
TOKEN_EXCHANGE_TIMEOUT = 15.0


def _load_higgsfield_oauth_client() -> tuple[str, str]:
    """Return (client_id, client_secret) for the Higgsfield OAuth client.

    Strategy:
      * env-var overrides — `HIGGSFIELD_OAUTH_CLIENT_ID` and
        `HIGGSFIELD_OAUTH_CLIENT_SECRET`. This is the only supported path
        today; Higgsfield doesn't ship a public bundle we can scrape.
      * raise — never silently pick a wrong client.

    Operators are expected to register an OAuth application with
    Higgsfield (BYO-tenant model) and stamp the resulting client_id +
    secret into the api container's env. See the integration card on
    /integrations for the "powered by your Higgsfield account credits"
    note that surfaces this requirement to tenants.
    """
    client_id = os.environ.get("HIGGSFIELD_OAUTH_CLIENT_ID")
    client_secret = os.environ.get("HIGGSFIELD_OAUTH_CLIENT_SECRET")
    if client_id and client_secret:
        return client_id, client_secret
    raise RuntimeError(
        "Higgsfield OAuth client is not configured. Set "
        "HIGGSFIELD_OAUTH_CLIENT_ID and HIGGSFIELD_OAUTH_CLIENT_SECRET "
        "env vars on the api container (each tenant brings their own "
        "Higgsfield account; calls bill against tenant credits)."
    )


# ── State machine ───────────────────────────────────────────────────────


@dataclass
class HiggsfieldLoginState:
    """Per-tenant in-memory OAuth dance state.

    Lives only until `/submit-code` succeeds or the TTL expires. The
    PKCE `code_verifier` is the sensitive bit — never logged, never
    serialised to the response.
    """

    login_id: str
    tenant_id: str
    code_verifier: str = field(repr=False, compare=False)
    state_token: str = field(repr=False, compare=False)
    verification_url: Optional[str] = None
    status: str = "pending"
    error: Optional[str] = None
    connected: bool = False
    started_at: str = field(default_factory=lambda: datetime.utcnow().isoformat())
    completed_at: Optional[str] = None


class HiggsfieldAuthManager:
    def __init__(self):
        self._lock = threading.Lock()
        self._by_tenant: Dict[str, HiggsfieldLoginState] = {}

    def get_state(self, tenant_id: str) -> Optional[HiggsfieldLoginState]:
        with self._lock:
            return self._by_tenant.get(tenant_id)

    def start_login(self, tenant_id: str) -> HiggsfieldLoginState:
        """Mint a fresh PKCE-backed Higgsfield authUrl for this tenant.

        Always replaces any existing in-memory state — re-clicking
        Connect hands back a new code_verifier so a previously-issued
        authorization code (if any) can't be redeemed against the new
        verifier.
        """
        login_id = str(uuid.uuid4())
        code_verifier = secrets.token_urlsafe(64)
        code_challenge = _pkce_challenge(code_verifier)
        state_token = secrets.token_hex(32)
        auth_url = _build_auth_url(code_challenge, state_token)

        state = HiggsfieldLoginState(
            login_id=login_id,
            tenant_id=tenant_id,
            code_verifier=code_verifier,
            state_token=state_token,
            verification_url=auth_url,
            status="pending",
        )
        with self._lock:
            self._by_tenant[tenant_id] = state
        return state

    def cancel_login(self, tenant_id: str) -> Optional[HiggsfieldLoginState]:
        with self._lock:
            state = self._by_tenant.get(tenant_id)
        if not state:
            return None
        state.status = "cancelled"
        state.error = "Login cancelled"
        state.completed_at = datetime.utcnow().isoformat()
        return state

    def submit_code(self, tenant_id: str, code: str) -> Optional[HiggsfieldLoginState]:
        """Exchange the user-pasted authorization code for OAuth tokens.

        Concurrency: the entire idempotency-check → exchange → persist
        sequence is held under `self._lock`. Two parallel /submit-code
        calls (user double-click, frontend retry) would otherwise burn
        the same authorization code at Higgsfield twice.
        """
        with self._lock:
            state = self._by_tenant.get(tenant_id)
            if not state:
                return None

            # Idempotent: once connected, duplicate submits are no-ops.
            if state.status == "connected" and state.connected:
                return state

            if _is_expired(state):
                state.status = "failed"
                state.error = (
                    "Login session expired. Click Connect again to start a new flow."
                )
                state.completed_at = datetime.utcnow().isoformat()
                return state

            cleaned = (code or "").strip()
            if not cleaned:
                state.status = "failed"
                state.error = "Authorization code is required"
                return state

            try:
                tokens = _exchange_code_for_tokens(cleaned, state.code_verifier)
            except _OAuthExchangeError as exc:
                logger.warning(
                    "Higgsfield OAuth token exchange failed for tenant %s: %s",
                    state.tenant_id[:8],
                    exc.safe_message,
                )
                state.status = "failed"
                state.error = exc.user_message
                state.completed_at = datetime.utcnow().isoformat()
                return state
            except Exception as exc:
                logger.exception(
                    "Unexpected error exchanging Higgsfield OAuth code for tenant %s",
                    state.tenant_id[:8],
                )
                state.status = "failed"
                state.error = f"Token exchange failed: {type(exc).__name__}"
                state.completed_at = datetime.utcnow().isoformat()
                return state

            if not tokens.get("access_token"):
                state.status = "failed"
                state.error = (
                    "Higgsfield did not return an access_token. Click Connect "
                    "to start a new flow."
                )
                state.completed_at = datetime.utcnow().isoformat()
                return state

            try:
                self._persist_creds(state.tenant_id, tokens)
            except Exception as exc:
                logger.exception(
                    "Failed to persist Higgsfield OAuth tokens for tenant %s",
                    state.tenant_id[:8],
                )
                state.status = "failed"
                state.error = f"Failed to store credentials: {type(exc).__name__}"
                state.completed_at = datetime.utcnow().isoformat()
                return state

            state.status = "connected"
            state.connected = True
            state.error = None
            state.completed_at = datetime.utcnow().isoformat()
            logger.info(
                "Higgsfield credentials persisted for tenant %s",
                state.tenant_id[:8],
            )
            return state

    def _persist_creds(self, tenant_id: str, tokens: dict) -> None:
        """Write the higgsfield_oauth row to the vault.

        Stores the full token blob (access_token + refresh_token if
        present + any `mcp_endpoint` field Higgsfield returns post-auth)
        under credential_key="higgsfield_oauth", credential_type
        ="oauth_token". The MCP source layer
        (`apps/api/app/services/higgsfield_mcp.py`) reads this row to
        register the per-tenant MCP server.

        store_credential() upserts (revokes-then-inserts) so re-running
        is idempotent.
        """
        import json as _json

        blob = _build_oauth_blob(tokens)

        db: Session = SessionLocal()
        try:
            tid = uuid.UUID(tenant_id)
            config = (
                db.query(IntegrationConfig)
                .filter(
                    IntegrationConfig.tenant_id == tid,
                    IntegrationConfig.integration_name == "higgsfield",
                )
                .first()
            )
            if not config:
                config = IntegrationConfig(
                    tenant_id=tid,
                    integration_name="higgsfield",
                    enabled=True,
                )
                db.add(config)
                db.commit()
                db.refresh(config)
            elif not config.enabled:
                config.enabled = True
                db.add(config)
                db.commit()
                db.refresh(config)

            store_credential(
                db,
                integration_config_id=config.id,
                tenant_id=tid,
                credential_key="higgsfield_oauth",
                plaintext_value=_json.dumps(blob),
                credential_type="oauth_token",
            )
            # Also store the bare access_token + refresh_token for
            # convenience consumers that don't want to JSON-decode.
            if tokens.get("access_token"):
                store_credential(
                    db,
                    integration_config_id=config.id,
                    tenant_id=tid,
                    credential_key="access_token",
                    plaintext_value=tokens["access_token"],
                    credential_type="oauth_token",
                )
            if tokens.get("refresh_token"):
                store_credential(
                    db,
                    integration_config_id=config.id,
                    tenant_id=tid,
                    credential_key="refresh_token",
                    plaintext_value=tokens["refresh_token"],
                    credential_type="oauth_token",
                )

            # After persisting, register the per-tenant MCP source.
            # Lazy-import to avoid a circular dep at module import time.
            try:
                from app.services import higgsfield_mcp

                higgsfield_mcp.register_for_tenant(
                    db, tenant_id=tid, oauth_blob=blob
                )
            except Exception:
                logger.exception(
                    "Higgsfield credential stored but MCP source registration "
                    "failed for tenant %s — credentials remain valid; the "
                    "next /start or status poll will retry registration.",
                    tenant_id[:8],
                )
        finally:
            db.close()


manager = HiggsfieldAuthManager()


# ── PKCE + URL helpers ──────────────────────────────────────────────────


def _pkce_challenge(verifier: str) -> str:
    """RFC 7636 S256 code challenge."""
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")


def _build_auth_url(code_challenge: str, state_token: str) -> str:
    client_id, _ = _load_higgsfield_oauth_client()
    params = {
        "client_id": client_id,
        "redirect_uri": HIGGSFIELD_OAUTH_REDIRECT_URI,
        "response_type": "code",
        "scope": HIGGSFIELD_OAUTH_SCOPE,
        "code_challenge_method": "S256",
        "code_challenge": code_challenge,
        "state": state_token,
    }
    return f"{HIGGSFIELD_AUTH_URL}?{urlencode(params)}"


def _is_expired(state: HiggsfieldLoginState) -> bool:
    try:
        started = datetime.fromisoformat(state.started_at)
    except (TypeError, ValueError):
        return False
    return (datetime.utcnow() - started).total_seconds() > LOGIN_TTL_SECONDS


# ── Token exchange ──────────────────────────────────────────────────────


class _OAuthExchangeError(Exception):
    """Raised when Higgsfield's token endpoint rejects the code.

    Carries TWO messages: `safe_message` for server logs (includes the
    raw error_description) and `user_message` for the API response
    (sanitised — never includes the user-pasted code).
    """

    def __init__(self, safe_message: str, user_message: str):
        super().__init__(safe_message)
        self.safe_message = safe_message
        self.user_message = user_message


def _exchange_code_for_tokens(code: str, code_verifier: str) -> dict:
    """POST authorization code + verifier to Higgsfield's token endpoint."""
    client_id, client_secret = _load_higgsfield_oauth_client()
    payload = {
        "grant_type": "authorization_code",
        "code": code,
        "code_verifier": code_verifier,
        "client_id": client_id,
        "client_secret": client_secret,
        "redirect_uri": HIGGSFIELD_OAUTH_REDIRECT_URI,
    }
    with httpx.Client(timeout=TOKEN_EXCHANGE_TIMEOUT) as client:
        resp = client.post(
            HIGGSFIELD_TOKEN_URL,
            data=payload,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
    if resp.status_code >= 400:
        try:
            body = resp.json()
        except ValueError:
            body = {"raw": resp.text[:500]}
        error_code = body.get("error") if isinstance(body, dict) else None
        description = body.get("error_description") if isinstance(body, dict) else None
        safe = f"{resp.status_code} {error_code or 'unknown'}: {description or body}"
        if error_code == "invalid_grant":
            user_msg = (
                "Higgsfield rejected the authorization code (it may have "
                "expired or already been used). Click Connect to start a "
                "new flow."
            )
        elif error_code == "invalid_request":
            user_msg = (
                "Higgsfield rejected the request as malformed. Make sure "
                "you pasted ONLY the code shown by Higgsfield — not the "
                "whole URL."
            )
        else:
            user_msg = (
                f"Higgsfield rejected the code ({error_code or resp.status_code})."
            )
        raise _OAuthExchangeError(safe, user_msg)
    return resp.json()


def _build_oauth_blob(tokens: dict) -> dict:
    """Shape tokens into the persisted blob.

    Always carries access_token (required for MCP calls). Optionally
    carries refresh_token, expires_in, scope, and an `mcp_endpoint`
    field — if Higgsfield's token response includes a discovered MCP
    server URL specific to this tenant's account, we surface it so
    `higgsfield_mcp.register_for_tenant` can use the per-account
    endpoint instead of the canonical guess.
    """
    blob = {
        "access_token": tokens.get("access_token"),
        "token_type": tokens.get("token_type") or "Bearer",
        "scope": tokens.get("scope") or HIGGSFIELD_OAUTH_SCOPE,
    }
    if tokens.get("refresh_token"):
        blob["refresh_token"] = tokens["refresh_token"]
    if tokens.get("expires_in"):
        blob["expires_in"] = tokens["expires_in"]
    # Surface a per-account MCP endpoint if Higgsfield returns one.
    if tokens.get("mcp_endpoint"):
        blob["mcp_endpoint"] = tokens["mcp_endpoint"]
    return blob


# ── Response serialisation ──────────────────────────────────────────────


def _serialize_state(
    state: Optional[HiggsfieldLoginState], connected: bool = False
) -> dict:
    if not state:
        return {
            "status": "connected" if connected else "idle",
            "connected": connected,
            "verification_url": None,
            "login_id": None,
            "error": None,
            "started_at": None,
            "completed_at": None,
        }

    # Sync stale manager flag with DB truth.
    if not connected and state.connected:
        state.connected = False
        if state.status == "connected":
            state.status = "idle"

    return {
        "login_id": state.login_id,
        "status": state.status,
        "verification_url": state.verification_url,
        "error": state.error,
        "connected": connected,
        "started_at": state.started_at,
        "completed_at": state.completed_at,
    }


def _tenant_has_higgsfield_credential(db: Session, tenant_id: uuid.UUID) -> bool:
    config = (
        db.query(IntegrationConfig)
        .filter(
            IntegrationConfig.tenant_id == tenant_id,
            IntegrationConfig.integration_name == "higgsfield",
            IntegrationConfig.enabled.is_(True),
        )
        .first()
    )
    if not config:
        return False

    credential = (
        db.query(IntegrationCredential.id)
        .filter(
            IntegrationCredential.integration_config_id == config.id,
            IntegrationCredential.tenant_id == tenant_id,
            IntegrationCredential.credential_key.in_(
                ["higgsfield_oauth", "access_token"]
            ),
            IntegrationCredential.status == "active",
        )
        .first()
    )
    return credential is not None


# ── Routes ──────────────────────────────────────────────────────────────


class SubmitCodeBody(BaseModel):
    code: str


@router.post("/start")
def start_higgsfield_auth(
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(deps.get_current_active_user),
):
    try:
        state = manager.start_login(str(current_user.tenant_id))
    except RuntimeError as exc:
        # _load_higgsfield_oauth_client raises RuntimeError when env
        # overrides aren't set. That's an operator-level config gap —
        # surface as 503 with an actionable message.
        raise HTTPException(
            status_code=503,
            detail=(
                "Higgsfield OAuth client is not configured on the api "
                "container — set HIGGSFIELD_OAUTH_CLIENT_ID and "
                "HIGGSFIELD_OAUTH_CLIENT_SECRET. Each tenant brings their "
                "own Higgsfield account; calls bill against tenant credits."
            ),
        ) from exc
    connected = _tenant_has_higgsfield_credential(db, current_user.tenant_id)
    return _serialize_state(state, connected=connected)


@router.get("/status")
def get_higgsfield_auth_status(
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(deps.get_current_active_user),
):
    state = manager.get_state(str(current_user.tenant_id))
    connected = _tenant_has_higgsfield_credential(db, current_user.tenant_id)
    return _serialize_state(state, connected=connected)


@router.post("/submit-code")
def submit_higgsfield_auth_code(
    body: SubmitCodeBody,
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(deps.get_current_active_user),
):
    state = manager.submit_code(str(current_user.tenant_id), body.code)
    if not state:
        raise HTTPException(status_code=404, detail="No active Higgsfield login flow")
    connected = _tenant_has_higgsfield_credential(db, current_user.tenant_id)
    return _serialize_state(state, connected=connected)


@router.post("/cancel")
def cancel_higgsfield_auth(
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(deps.get_current_active_user),
):
    state = manager.cancel_login(str(current_user.tenant_id))
    if not state:
        raise HTTPException(status_code=404, detail="No active Higgsfield login flow")
    connected = _tenant_has_higgsfield_credential(db, current_user.tenant_id)
    return _serialize_state(state, connected=connected)


@router.post("/disconnect")
def disconnect_higgsfield_auth(
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(deps.get_current_active_user),
):
    """Revoke all Higgsfield credentials for the current tenant, drop the
    per-tenant MCP source, and reset in-memory manager state.
    """
    tid = current_user.tenant_id
    config = (
        db.query(IntegrationConfig)
        .filter(
            IntegrationConfig.tenant_id == tid,
            IntegrationConfig.integration_name == "higgsfield",
        )
        .first()
    )
    revoked_count = 0
    if config:
        active_creds = (
            db.query(IntegrationCredential)
            .filter(
                IntegrationCredential.integration_config_id == config.id,
                IntegrationCredential.tenant_id == tid,
                IntegrationCredential.status == "active",
            )
            .all()
        )
        for cred in active_creds:
            cred.status = "revoked"
            revoked_count += 1
        db.commit()

    # Drop the per-tenant MCP source row so discovery stops listing it.
    try:
        from app.services import higgsfield_mcp

        higgsfield_mcp.unregister_for_tenant(db, tenant_id=tid)
    except Exception:
        logger.exception(
            "Higgsfield credentials revoked but MCP source unregister "
            "failed for tenant %s",
            str(tid)[:8],
        )

    with manager._lock:
        manager._by_tenant.pop(str(tid), None)

    return {
        "status": "idle",
        "connected": False,
        "verification_url": None,
        "login_id": None,
        "error": None,
        "started_at": None,
        "completed_at": None,
        "revoked": revoked_count,
    }
