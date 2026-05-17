"""Gemini CLI OAuth login flow — api-owned PKCE redirect exchange.

The previous implementation spawned a `gemini` subprocess in a tenant-
scoped temp `HOME` with `NO_BROWSER=true`, captured the verification URL
from a pty, and forwarded the user-pasted authorization code back to the
subprocess via the pty master. The gemini subprocess then internally
called Google's token endpoint with a PKCE `code_verifier` that lived
only in the subprocess's memory.

That path had four independent concurrency hazards (subprocess retry
loop with codeVerifier rotation, two-thread pty reader race, frontend
poll vs submit-code interleaving, user think-time vs. authcode TTL),
ALL of which surfaced as the same opaque failure: gemini-cli's
`FatalAuthenticationError` (exit code 41) after `maxRetries=2` failed
`authWithUserCode` calls. Two tenants hit it on 2026-05-16; the wider
class of failures is structural, not bug-of-the-week.

This module owns the OAuth dance end-to-end:

  1. `/start` generates a PKCE `code_verifier` + `code_challenge`, builds
     a Google authUrl using the same client_id + scopes + redirect_uri
     gemini-cli embeds, and returns the URL. NO subprocess, no pty, no
     tenant temp HOME.
  2. `/submit-code` receives the user-pasted authorization code, exchanges
     it directly against `https://oauth2.googleapis.com/token`, builds an
     `oauth_creds.json`-shaped blob, and persists the access_token +
     refresh_token to the encrypted vault. The code-worker (which is
     where `gemini` actually runs for chat turns) reads the vault and
     materialises `${HOME}/.gemini/oauth_creds.json` before exec.

Refresh tokens are issued by Google's OAuth client `681255809395-…`
(gemini-cli's installed-app client). They are bound to that client_id —
that's why a refresh token minted by our platform-wide OAuth client
won't work for the gemini-cli code path. By driving the auth flow with
the gemini client_id ourselves we preserve that invariant without
needing the subprocess at all.

Design: docs/plans/2026-05-16-gemini-cli-oauth-exitcode-41.md
"""
import base64
import functools
import glob
import hashlib
import json
import logging
import os
import re
import secrets
import threading
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, Optional, Tuple
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


# ── Gemini-CLI OAuth constants ───────────────────────────────────────────
#
# The api MUST use the SAME OAuth client (client_id + client_secret) that
# gemini-cli embeds, because Google binds refresh_tokens to the issuing
# client. If we used a platform-owned client, the refresh_token we mint
# here would be rejected when the code-worker later refreshes it inside
# the actual `gemini` subprocess during a chat turn.
#
# The gemini-cli client_id + client_secret + redirect_uri are baked into
# the published `@google/gemini-cli` npm bundle (visible verbatim in
# `chunk-7VVHSNDQ.js` and in upstream source at
# `packages/core/src/code_assist/oauth2.ts`). Google documents this as
# the "installed application" client model — the secret is ceremonial,
# not a real secret. We deliberately do NOT check these values into git:
# instead we read them at runtime from the installed gemini-cli bundle
# in the api container (so we stay in sync if Google rotates the client),
# with optional env-var overrides for non-container test envs.
GEMINI_OAUTH_REDIRECT_URI = "https://codeassist.google.com/authcode"
GEMINI_OAUTH_SCOPE = (
    "https://www.googleapis.com/auth/cloud-platform "
    "https://www.googleapis.com/auth/userinfo.email "
    "https://www.googleapis.com/auth/userinfo.profile"
)
GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"

# How long a /start state lives before /submit-code stops accepting it.
# Google's authorization codes are short-lived (~minutes); we cap the
# server-side state lifetime at 10 minutes to match.
LOGIN_TTL_SECONDS = 600

# httpx timeout for the token exchange. Generous; Google usually replies
# in <500ms.
TOKEN_EXCHANGE_TIMEOUT = 15.0


# ── Gemini-CLI client_id/secret discovery ────────────────────────────────
#
# Strategy (in order):
#   1. Env-var overrides — `GEMINI_OAUTH_CLIENT_ID` and
#      `GEMINI_OAUTH_CLIENT_SECRET`. Used by unit tests and any future
#      tenant-specific override.
#   2. Scan the installed `@google/gemini-cli` npm bundle on disk and
#      pull the constants Google ships in the published JavaScript. The
#      api container's Dockerfile already installs the package; the
#      shipped bundle is the canonical source-of-truth for the values
#      we need to match.
#   3. Hard fail with a clear error — we refuse to pretend to start a
#      flow we know will reject the user's code at the exchange step.

# Common install paths for `@google/gemini-cli` npm bundle, in priority
# order. The Linux path matches the apt-installed `npm` global location
# inside the api container; the macOS path matches Homebrew for local
# dev. NPM_PREFIX from the env wins over both.
_GEMINI_BUNDLE_SEARCH_PATHS = (
    os.environ.get("GEMINI_CLI_BUNDLE_DIR"),
    "/usr/local/lib/node_modules/@google/gemini-cli/bundle",
    "/usr/lib/node_modules/@google/gemini-cli/bundle",
    "/opt/homebrew/lib/node_modules/@google/gemini-cli/bundle",
)

# Anchored to the `OAUTH_CLIENT_ID = "…"` literal that gemini-cli's bundle
# declares (currently the installed-app client `681255809395-…`). The
# previous regex matched the first `*.apps.googleusercontent.com` in the
# chunk, which is `CLOUD_SDK_CLIENT_ID = 764086051850-…` re-exported from
# google-auth-library. Signing the authorization URL with that client_id
# combined with `redirect_uri=https://codeassist.google.com/authcode`
# trips Google's "redirect_uri_mismatch" (the codeassist paste-back URI
# is only registered on the gemini-cli client, not on the cloud-sdk one).
_GEMINI_CLIENT_ID_RE = re.compile(
    r'OAUTH_CLIENT_ID\s*=\s*["\'](?P<v>\d{4,}-[a-z0-9]+\.apps\.googleusercontent\.com)["\']'
)
# Google's "installed application" client secrets are always
# `GOCSPX-` + a 28-char url-safe-ish trailing run. Anchored to
# `OAUTH_CLIENT_SECRET = "…"` for the same first-match-wrong reason.
_GEMINI_CLIENT_SECRET_RE = re.compile(
    r'OAUTH_CLIENT_SECRET\s*=\s*["\'](?P<v>GOCSPX-[A-Za-z0-9_\-]{20,})["\']'
)


@functools.lru_cache(maxsize=1)
def _load_gemini_oauth_client() -> Tuple[str, str]:
    """Return (client_id, client_secret) for the gemini-cli OAuth client.

    Lookup precedence:
      * env overrides → use them
      * scan installed bundle → extract from the shipped JS chunks
      * raise — never silently pick a wrong client

    Cached: bundle parsing on first call only.
    """
    env_id = os.environ.get("GEMINI_OAUTH_CLIENT_ID")
    env_secret = os.environ.get("GEMINI_OAUTH_CLIENT_SECRET")
    if env_id and env_secret:
        return env_id, env_secret

    for base in _GEMINI_BUNDLE_SEARCH_PATHS:
        if not base or not os.path.isdir(base):
            continue
        for chunk in sorted(glob.glob(os.path.join(base, "chunk-*.js"))):
            try:
                with open(chunk, "r", encoding="utf-8", errors="ignore") as f:
                    text = f.read()
            except OSError:
                continue
            # Only consider chunks that mention the gemini code-assist
            # endpoint — otherwise we'll match an unrelated client_id
            # from a vendored dependency.
            if "cloudcode-pa.googleapis.com" not in text and "codeassist.google.com" not in text:
                continue
            id_match = _GEMINI_CLIENT_ID_RE.search(text)
            secret_match = _GEMINI_CLIENT_SECRET_RE.search(text)
            if id_match and secret_match:
                return id_match.group("v"), secret_match.group("v")

    raise RuntimeError(
        "Could not locate the @google/gemini-cli OAuth client_id+secret. "
        "Set GEMINI_OAUTH_CLIENT_ID and GEMINI_OAUTH_CLIENT_SECRET env "
        "vars, or ensure @google/gemini-cli is installed in the api "
        "container."
    )


# ── State machine ────────────────────────────────────────────────────────


@dataclass
class GeminiLoginState:
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


class GeminiAuthManager:
    def __init__(self):
        self._lock = threading.Lock()
        self._by_tenant: Dict[str, GeminiLoginState] = {}

    def get_state(self, tenant_id: str) -> Optional[GeminiLoginState]:
        with self._lock:
            return self._by_tenant.get(tenant_id)

    def start_login(self, tenant_id: str) -> GeminiLoginState:
        """Mint a fresh PKCE-backed Google authUrl for this tenant.

        Always replaces any existing in-memory state — if the user
        re-clicks "Connect" after starting a flow we don't want to
        hand them the stale URL whose code_verifier may be older than
        the authorization code Google is about to issue.
        """
        login_id = str(uuid.uuid4())
        # 64 bytes of entropy → 86-char base64url string. Well above
        # Google's 43-char PKCE minimum.
        code_verifier = secrets.token_urlsafe(64)
        code_challenge = _pkce_challenge(code_verifier)
        state_token = secrets.token_hex(32)
        auth_url = _build_auth_url(code_challenge, state_token)

        state = GeminiLoginState(
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

    def cancel_login(self, tenant_id: str) -> Optional[GeminiLoginState]:
        with self._lock:
            state = self._by_tenant.get(tenant_id)
        if not state:
            return None
        state.status = "cancelled"
        state.error = "Login cancelled"
        state.completed_at = datetime.utcnow().isoformat()
        return state

    def submit_code(self, tenant_id: str, code: str) -> Optional[GeminiLoginState]:
        """Exchange the user-pasted authorization code for OAuth tokens.

        Returns the (mutated) GeminiLoginState. The caller is responsible
        for translating the state into the response shape via
        `_serialize_state`. Failures are recorded on the state, not
        raised, so the frontend can render the error inline (mirrors
        the old subprocess flow's contract).

        Concurrency: the entire idempotency-check → exchange → persist
        sequence is held under `self._lock`. Two parallel /submit-code
        calls (e.g. user double-click, frontend retry-on-network-blip)
        would otherwise race past the `state.connected` check and burn
        the same authorization code at Google twice — only one would
        win, the other would surface as `invalid_grant`. Google's token
        endpoint typically replies in <500ms so holding the lock for
        the full exchange is acceptable.
        """
        with self._lock:
            state = self._by_tenant.get(tenant_id)
            if not state:
                return None

            # Idempotent: if we've already connected, don't re-exchange
            # the code. The frontend can poll /status after success and
            # we want those polls — and any concurrent submit-code
            # racers — to be no-ops, not 4xxs.
            if state.status == "connected" and state.connected:
                return state

            if _is_expired(state):
                state.status = "failed"
                state.error = (
                    "Login session expired. Please click Connect again to start a new flow."
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
                # Don't leak the raw Google error blob into the response —
                # callers may include the user-pasted code in the body.
                # We DO log it server-side for debugging.
                logger.warning(
                    "Gemini OAuth token exchange failed for tenant %s: %s",
                    state.tenant_id[:8],
                    exc.safe_message,
                )
                state.status = "failed"
                state.error = exc.user_message
                state.completed_at = datetime.utcnow().isoformat()
                return state
            except Exception as exc:  # network / parsing errors
                logger.exception(
                    "Unexpected error exchanging Gemini OAuth code for tenant %s",
                    state.tenant_id[:8],
                )
                state.status = "failed"
                state.error = f"Token exchange failed: {type(exc).__name__}"
                state.completed_at = datetime.utcnow().isoformat()
                return state

            if not tokens.get("refresh_token"):
                # Without refresh_token the credential is useless after ~1h.
                # Google withholds refresh_token if the user has previously
                # granted consent for this client_id and `prompt=consent`
                # wasn't requested — we DO pass prompt=consent in the
                # authUrl so this should be rare, but fail loud if it
                # happens.
                state.status = "failed"
                state.error = (
                    "Google did not return a refresh_token. Please revoke the "
                    "Gemini Code Assist app from your Google account at "
                    "https://myaccount.google.com/permissions and try again."
                )
                state.completed_at = datetime.utcnow().isoformat()
                return state

            try:
                self._persist_creds(state.tenant_id, tokens)
            except Exception as exc:
                logger.exception(
                    "Failed to persist Gemini OAuth tokens for tenant %s",
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
                "Gemini CLI credentials persisted for tenant %s",
                state.tenant_id[:8],
            )
            return state

    # ── Persistence ─────────────────────────────────────────────────────

    def _persist_creds(self, tenant_id: str, tokens: dict) -> None:
        """Write the oauth_creds.json blob + per-field rows to the vault.

        Three rows are written:
          * `oauth_creds`   — the full JSON blob in gemini's
            `oauth_creds.json` shape. The code-worker materialises this
            to `${HOME}/.gemini/oauth_creds.json` before spawning gemini.
          * `oauth_token`   — bare access_token for convenience consumers
            (mirrors the legacy `_fetch_integration_credentials` reader).
          * `refresh_token` — bare refresh_token for the same reason.

        store_credential() upserts (revokes-then-inserts) by
        credential_key, so re-running this is idempotent w.r.t. the
        vault — each row points at the most recent successful auth.
        """
        creds_blob = _build_oauth_creds_blob(tokens)
        creds_json = json.dumps(creds_blob, indent=2)

        db: Session = SessionLocal()
        try:
            tid = uuid.UUID(tenant_id)
            config = (
                db.query(IntegrationConfig)
                .filter(
                    IntegrationConfig.tenant_id == tid,
                    IntegrationConfig.integration_name == "gemini_cli",
                )
                .first()
            )
            if not config:
                config = IntegrationConfig(
                    tenant_id=tid,
                    integration_name="gemini_cli",
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
                credential_key="oauth_creds",
                plaintext_value=creds_json,
                credential_type="oauth_token",
            )
            if tokens.get("access_token"):
                store_credential(
                    db,
                    integration_config_id=config.id,
                    tenant_id=tid,
                    credential_key="oauth_token",
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
        finally:
            db.close()


manager = GeminiAuthManager()


# ── PKCE + URL helpers ───────────────────────────────────────────────────


def _pkce_challenge(verifier: str) -> str:
    """RFC 7636 S256 code challenge = base64url(SHA256(verifier)), no padding."""
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")


def _build_auth_url(code_challenge: str, state_token: str) -> str:
    client_id, _ = _load_gemini_oauth_client()
    params = {
        "client_id": client_id,
        "redirect_uri": GEMINI_OAUTH_REDIRECT_URI,
        "response_type": "code",
        "scope": GEMINI_OAUTH_SCOPE,
        "access_type": "offline",
        "code_challenge_method": "S256",
        "code_challenge": code_challenge,
        "state": state_token,
        # Force the consent screen so Google always returns a fresh
        # refresh_token. Without this, repeated logins for the same
        # google account may omit refresh_token.
        "prompt": "consent",
    }
    return f"{GOOGLE_AUTH_URL}?{urlencode(params)}"


def _is_expired(state: GeminiLoginState) -> bool:
    try:
        started = datetime.fromisoformat(state.started_at)
    except (TypeError, ValueError):
        return False
    return (datetime.utcnow() - started).total_seconds() > LOGIN_TTL_SECONDS


# ── Token exchange ──────────────────────────────────────────────────────


class _OAuthExchangeError(Exception):
    """Raised when Google's token endpoint rejects the code.

    Carries TWO messages: `safe_message` for server logs (includes the
    raw error_description), and `user_message` for the API response
    (sanitised — never includes the user-pasted code or raw Google
    response). The split exists because the api response goes back
    through the frontend and may end up in browser screenshots / shared
    bug reports.
    """

    def __init__(self, safe_message: str, user_message: str):
        super().__init__(safe_message)
        self.safe_message = safe_message
        self.user_message = user_message


def _exchange_code_for_tokens(code: str, code_verifier: str) -> dict:
    """POST authorization code + verifier to Google's token endpoint.

    Returns the parsed JSON response on success. Raises
    `_OAuthExchangeError` for any 4xx (bad code, expired code,
    verifier mismatch) and lets network/5xx errors propagate.
    """
    client_id, client_secret = _load_gemini_oauth_client()
    payload = {
        "grant_type": "authorization_code",
        "code": code,
        "code_verifier": code_verifier,
        "client_id": client_id,
        "client_secret": client_secret,
        "redirect_uri": GEMINI_OAUTH_REDIRECT_URI,
    }
    with httpx.Client(timeout=TOKEN_EXCHANGE_TIMEOUT) as client:
        resp = client.post(
            GOOGLE_TOKEN_URL,
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
                "Google rejected the authorization code (it may have expired "
                "or already been used). Click Connect to start a new flow."
            )
        elif error_code == "invalid_request":
            user_msg = (
                "Google rejected the request as malformed. Make sure you "
                "pasted ONLY the code shown on codeassist.google.com — not "
                "the whole URL."
            )
        else:
            user_msg = f"Google rejected the code ({error_code or resp.status_code})."
        raise _OAuthExchangeError(safe, user_msg)
    return resp.json()


def _build_oauth_creds_blob(tokens: dict) -> dict:
    """Shape tokens into gemini-cli's `oauth_creds.json` schema.

    gemini-cli's `google-auth-library` writes a Credentials object with
    these keys: access_token, refresh_token, scope, token_type,
    expiry_date (ms since epoch), and optionally id_token. The
    code-worker materialises this verbatim into
    `${HOME}/.gemini/oauth_creds.json`.

    We also include `client_id` and `client_secret` so that when
    gemini-cli's `google-auth-library` tries to refresh the access
    token it can call `oauth2.googleapis.com/token` with the matching
    installed-app client. The legacy subprocess fallback in
    `apps/code-worker/workflows.py` (`_synthesise_oauth_creds_from_legacy_rows`)
    embeds these same fields; dropping them here would break refresh.
    """
    now_ms = int(time.time() * 1000)
    expires_in = tokens.get("expires_in")
    expiry_date: Optional[int]
    if isinstance(expires_in, (int, float)) and expires_in > 0:
        expiry_date = now_ms + int(expires_in * 1000)
    else:
        expiry_date = None
    client_id, client_secret = _load_gemini_oauth_client()
    blob = {
        "access_token": tokens.get("access_token"),
        "refresh_token": tokens.get("refresh_token"),
        "scope": tokens.get("scope") or GEMINI_OAUTH_SCOPE,
        "token_type": tokens.get("token_type") or "Bearer",
        "client_id": client_id,
        "client_secret": client_secret,
    }
    if expiry_date is not None:
        blob["expiry_date"] = expiry_date
    if tokens.get("id_token"):
        blob["id_token"] = tokens["id_token"]
    return blob


# ── Response serialisation + DB-truth helper ─────────────────────────────


def _serialize_state(state: Optional[GeminiLoginState], connected: bool = False) -> dict:
    """Serialize manager state. The `connected` flag is the DB truth and is
    authoritative — manager state is in-memory cache and may be stale after
    a disconnect, so we never let an old state.connected override a fresh DB
    "no creds" answer.
    """
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

    # Sync stale manager flag with DB truth so a revoke is reflected immediately.
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


def _tenant_has_gemini_credential(db: Session, tenant_id: uuid.UUID) -> bool:
    config = (
        db.query(IntegrationConfig)
        .filter(
            IntegrationConfig.tenant_id == tenant_id,
            IntegrationConfig.integration_name == "gemini_cli",
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
            IntegrationCredential.credential_key.in_(["oauth_creds", "oauth_token"]),
            IntegrationCredential.status == "active",
        )
        .first()
    )
    return credential is not None


# ── Routes ───────────────────────────────────────────────────────────────


class SubmitCodeBody(BaseModel):
    code: str


@router.post("/start")
def start_gemini_auth(
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(deps.get_current_active_user),
):
    try:
        state = manager.start_login(str(current_user.tenant_id))
    except RuntimeError as exc:
        # `_load_gemini_oauth_client` raises RuntimeError when neither
        # env-var overrides nor an installed `@google/gemini-cli` bundle
        # is available. That's an operator-level config gap — surface
        # it as 503 with an actionable message instead of an opaque 500.
        msg = str(exc)
        if "gemini-cli" in msg.lower() or "client_id" in msg.lower():
            raise HTTPException(
                status_code=503,
                detail=(
                    "Gemini CLI OAuth client is not configured in the api "
                    "container (missing @google/gemini-cli install and no "
                    "GEMINI_OAUTH_CLIENT_ID/SECRET env override) — contact ops."
                ),
            )
        raise
    connected = _tenant_has_gemini_credential(db, current_user.tenant_id)
    return _serialize_state(state, connected=connected)


@router.get("/status")
def get_gemini_auth_status(
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(deps.get_current_active_user),
):
    state = manager.get_state(str(current_user.tenant_id))
    connected = _tenant_has_gemini_credential(db, current_user.tenant_id)
    return _serialize_state(state, connected=connected)


@router.post("/submit-code")
def submit_gemini_auth_code(
    body: SubmitCodeBody,
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(deps.get_current_active_user),
):
    state = manager.submit_code(str(current_user.tenant_id), body.code)
    if not state:
        raise HTTPException(status_code=404, detail="No active Gemini login flow")
    connected = _tenant_has_gemini_credential(db, current_user.tenant_id)
    return _serialize_state(state, connected=connected)


@router.post("/cancel")
def cancel_gemini_auth(
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(deps.get_current_active_user),
):
    state = manager.cancel_login(str(current_user.tenant_id))
    if not state:
        raise HTTPException(status_code=404, detail="No active Gemini login flow")
    connected = _tenant_has_gemini_credential(db, current_user.tenant_id)
    return _serialize_state(state, connected=connected)


@router.post("/disconnect")
def disconnect_gemini_auth(
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(deps.get_current_active_user),
):
    """Revoke all Gemini CLI credentials for the current tenant and reset
    in-memory manager state. Returns idle status.
    """
    tid = current_user.tenant_id
    config = (
        db.query(IntegrationConfig)
        .filter(
            IntegrationConfig.tenant_id == tid,
            IntegrationConfig.integration_name == "gemini_cli",
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

    # Reset in-memory manager state for this tenant
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
