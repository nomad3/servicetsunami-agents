"""Activity stubs for OAuthHandshakeWorkflow (Phase D-1, #295).

Phase D-1 ships these as no-ops. The real work happens in:

  D-2: implement run_oauth_handshake on the code-worker side —
       shell out to the bundled CLI ({gemini_cli, claude, codex,
       higgsfield}_auth) and persist the resulting tokens via the
       same internal token-write endpoint the api uses today. The
       activity runs on the code-worker task queue so the api
       doesn't need the npm CLIs.

  D-3: flip apps/api/app/api/v1/{gemini_cli, claude, codex,
       higgsfield}_auth.py to dispatch this workflow when env
       OAUTH_DISPATCH_MODE_<PROVIDER>=workflow.

  D-4: drop the npm CLI install from apps/api/Dockerfile. Recovers
       ~1 GB per the image-shrink plan §Phase D.

Until D-2 + D-3, this activity returns success=False so api-side
callers keep using the legacy subprocess.run path (no regression).
"""
from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from temporalio import activity

log = logging.getLogger(__name__)


@activity.defn(name="oauth.run_oauth_handshake")
async def run_oauth_handshake(
    provider: str,
    tenant_id: str,
    code: str,
    code_verifier: str,
    redirect_uri: str,
) -> Dict[str, Any]:
    """Phase D-1 stub. Returns success=False with reason='phase_d1_stub'
    so callers keep the legacy subprocess.run path.

    Phase D-2 body will:
      1. Resolve the provider's token-exchange URL.
      2. POST grant_type=authorization_code + code + code_verifier +
         redirect_uri + client_id/secret.
      3. On success, call back into the api's internal token-store
         endpoint with X-Internal-Key + X-Tenant-Id.
      4. Return the persisted shape.

    The activity runs on the code-worker task queue so the api
    container can drop the npm CLI install entirely.
    """
    log.info(
        "run_oauth_handshake STUB provider=%s tenant=%s",
        provider, tenant_id,
    )
    return {
        "success": False,
        "provider": provider,
        "tenant_id": tenant_id,
        "reason": "phase_d1_stub",
        "access_token_stored": False,
        "refresh_token_stored": False,
        "expires_in": None,
    }


__all__ = ["run_oauth_handshake"]
