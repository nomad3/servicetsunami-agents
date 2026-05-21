"""Activities for OAuthHandshakeWorkflow (Phase D-2, #295).

Phase D-2 implementation status by provider:

  - higgsfield: REAL BODY (HTTP token exchange via the existing
    HIGGSFIELD_TOKEN_URL endpoint). Persists access_token +
    refresh_token to the integration_credentials vault.
  - gemini_cli, claude, codex: STUB. These still require the
    bundled npm CLI to mint tokens (each has provider-specific
    PKCE / device-code flows that aren't a single POST). They
    stay on the legacy subprocess.run path in their respective
    {gemini_cli, claude, codex}_auth.py handlers.

Phase D-3 will ship the env-gate in apps/api/app/api/v1/
{higgsfield, gemini_cli, claude, codex}_auth.py that flips
dispatch from subprocess.run to OAuthHandshakeWorkflow when
OAUTH_DISPATCH_MODE_<PROVIDER>=workflow.

Phase D-4 will move this activity to apps/code-worker/ so the
api Dockerfile can drop the npm CLIs entirely.

Until D-3 lands, the api still subprocess.runs everything — this
activity is dormant. The implementation here is the *contract*
the api will dispatch against once flipped.
"""
from __future__ import annotations

import logging
import os
from typing import Any, Dict, Optional

from temporalio import activity

log = logging.getLogger(__name__)


HIGGSFIELD_TOKEN_URL = os.environ.get(
    "HIGGSFIELD_TOKEN_URL", "https://higgsfield.ai/oauth/token"
)


def _stub_response(provider: str, tenant_id: str, reason: str) -> Dict[str, Any]:
    return {
        "success": False,
        "provider": provider,
        "tenant_id": tenant_id,
        "reason": reason,
        "access_token_stored": False,
        "refresh_token_stored": False,
        "expires_in": None,
    }


def _exchange_higgsfield(
    code: str,
    code_verifier: str,
    redirect_uri: str,
    timeout: float = 15.0,
) -> Optional[Dict[str, Any]]:
    """POST to Higgsfield's token endpoint with grant_type=authorization_code.

    Returns the parsed JSON on success or None on any failure mode
    (HTTP 4xx/5xx, network error, malformed body). The caller logs
    + returns a structured error to the workflow.

    Same shape as ``app/services/higgsfield_oauth.refresh_higgsfield_access_token``
    but uses ``authorization_code`` instead of ``refresh_token``.
    """
    import httpx

    payload = {
        "grant_type": "authorization_code",
        "code": code,
        "code_verifier": code_verifier,
        "redirect_uri": redirect_uri,
        "client_id": os.environ.get("HIGGSFIELD_CLIENT_ID", ""),
        "client_secret": os.environ.get("HIGGSFIELD_CLIENT_SECRET", ""),
    }
    try:
        with httpx.Client(timeout=timeout) as client:
            resp = client.post(HIGGSFIELD_TOKEN_URL, data=payload)
            resp.raise_for_status()
            return resp.json()
    except httpx.HTTPStatusError as exc:
        log.warning(
            "oauth_handshake.higgsfield: HTTP %s: %s",
            exc.response.status_code, exc.response.text[:200],
        )
        return None
    except (httpx.RequestError, ValueError) as exc:
        log.warning("oauth_handshake.higgsfield: %s", exc)
        return None


@activity.defn(name="oauth.run_oauth_handshake")
def run_oauth_handshake(
    provider: str,
    tenant_id: str,
    code: str,
    code_verifier: str,
    redirect_uri: str,
) -> Dict[str, Any]:
    """Exchange an OAuth ``code`` for ``access_token`` + ``refresh_token``
    and persist to the integration_credentials vault.

    Phase D-2 implementation: Higgsfield is the only provider with a
    real body. Other providers return success=False so the legacy
    subprocess.run path in their api handlers stays in use.

    Synchronous activity — uses ThreadPoolExecutor on the worker
    side (mirrors skill_eval_activities pattern). Token store uses
    the same vault API as the existing higgsfield_auth.py.
    """
    if provider != "higgsfield":
        return _stub_response(
            provider, tenant_id,
            reason="phase_d2_only_higgsfield_supported",
        )

    tokens = _exchange_higgsfield(code, code_verifier, redirect_uri)
    if tokens is None:
        return _stub_response(
            provider, tenant_id, reason="higgsfield_token_exchange_failed",
        )

    access_token = tokens.get("access_token")
    if not access_token:
        log.warning(
            "oauth_handshake.higgsfield: success response missing "
            "access_token: %s", tokens,
        )
        return _stub_response(
            provider, tenant_id,
            reason="higgsfield_response_missing_access_token",
        )

    refresh_token = tokens.get("refresh_token")
    expires_in = tokens.get("expires_in")

    # Persist to the integration_credentials vault. Lazy import keeps
    # the activity registerable even in test contexts where the full
    # api isn't initialized.
    refresh_stored = False
    try:
        import uuid as _uuid

        from app.db.session import SessionLocal
        from app.models.integration_config import IntegrationConfig
        from app.services.integration_secrets import store_credential

        tid = _uuid.UUID(tenant_id)
        db = SessionLocal()
        try:
            cfg = (
                db.query(IntegrationConfig)
                .filter(
                    IntegrationConfig.tenant_id == tid,
                    IntegrationConfig.integration_name == "higgsfield",
                )
                .first()
            )
            if cfg is None:
                # No integration row to attach tokens to. The api-side
                # legacy handler usually creates this row before kicking
                # off the OAuth flow; if it doesn't exist we can't
                # persist the tokens.
                log.warning(
                    "oauth_handshake.higgsfield: no IntegrationConfig "
                    "for tenant=%s — tokens not stored",
                    tenant_id,
                )
                return _stub_response(
                    provider, tenant_id,
                    reason="no_integration_config_row",
                )

            store_credential(
                db,
                config_id=cfg.id,
                tenant_id=tid,
                credential_key="access_token",
                plaintext_value=access_token,
            )
            if refresh_token:
                store_credential(
                    db,
                    config_id=cfg.id,
                    tenant_id=tid,
                    credential_key="refresh_token",
                    plaintext_value=refresh_token,
                )
                refresh_stored = True
            db.commit()
        finally:
            db.close()
    except Exception as exc:  # noqa: BLE001
        log.exception(
            "oauth_handshake.higgsfield: vault persist failed: %s", exc,
        )
        return _stub_response(
            provider, tenant_id, reason=f"vault_persist_failed: {exc}",
        )

    return {
        "success": True,
        "provider": provider,
        "tenant_id": tenant_id,
        "reason": "ok",
        "access_token_stored": True,
        "refresh_token_stored": refresh_stored,
        "expires_in": expires_in,
    }


__all__ = ["run_oauth_handshake"]
