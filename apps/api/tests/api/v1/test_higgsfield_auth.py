"""Tests for the Higgsfield OAuth flow (Wave 1a of the CLI integration
catalog, #270).

Mirrors `test_gemini_cli_auth_owned_flow.py` because the Higgsfield
flow IS the same shape: api-owned PKCE start, paste-back code, direct
token-endpoint exchange, persistence to the encrypted vault.

Coverage:
  * PKCE start mints a URL with all required params.
  * /submit-code happy path writes the higgsfield_oauth row and
    triggers per-tenant MCP source registration.
  * Sad path — Higgsfield returns invalid_grant; nothing persisted;
    user-facing message is sanitised.
  * Idempotency — repeat /submit-code post-success is a no-op.
  * Empty/whitespace input is rejected without burning a Higgsfield
    call.
  * /start returns 503 when HIGGSFIELD_OAUTH_CLIENT_ID/SECRET env
    overrides are missing — same operator-friendly mapping as gemini's
    bundle-missing 503.

Design: docs/plans/2026-05-18-cli-integration-catalog.md (Wave 1a)
"""
from __future__ import annotations

import json
import uuid
from unittest.mock import MagicMock

import pytest

pytest.importorskip("fastapi")

from app.api.v1 import higgsfield_auth as ha


_TENANT = "22222222-2222-2222-2222-222222222222"

_TEST_CLIENT_ID = "test-higgsfield-client-id"
_TEST_CLIENT_SECRET = "test-higgsfield-client-secret"


@pytest.fixture(autouse=True)
def _pin_oauth_client(monkeypatch):
    """Pin the Higgsfield OAuth client to deterministic env values for
    tests so we don't depend on a real registration."""
    monkeypatch.setenv("HIGGSFIELD_OAUTH_CLIENT_ID", _TEST_CLIENT_ID)
    monkeypatch.setenv("HIGGSFIELD_OAUTH_CLIENT_SECRET", _TEST_CLIENT_SECRET)
    yield


def _fresh_manager():
    return ha.HiggsfieldAuthManager()


def _stub_db(monkeypatch, captured: list):
    """Wire SessionLocal + store_credential + MCP register so we can
    assert on writes without touching a real database or external MCP
    server."""
    chain = MagicMock()
    chain.filter.return_value = chain
    cfg = MagicMock()
    cfg.id = uuid.uuid4()
    cfg.enabled = True
    chain.first.return_value = cfg
    db = MagicMock()
    db.query.return_value = chain
    monkeypatch.setattr(ha, "SessionLocal", lambda: db)
    monkeypatch.setattr(
        ha,
        "store_credential",
        lambda db, **kw: captured.append(kw),
    )
    # Stub the MCP registration so persistence doesn't depend on a real
    # MCP server fixture. Tracked separately so tests can assert
    # register_for_tenant was/wasn't called.
    mcp_calls: list = []

    class _StubMcp:
        @staticmethod
        def register_for_tenant(db, tenant_id, oauth_blob):  # noqa: D401
            mcp_calls.append({"tenant_id": tenant_id, "oauth_blob": oauth_blob})

        @staticmethod
        def unregister_for_tenant(db, tenant_id):  # noqa: D401
            mcp_calls.append({"unregister": True, "tenant_id": tenant_id})

    # `_persist_creds` does `from app.services import higgsfield_mcp`,
    # which resolves via the parent package's attribute lookup. Patch
    # both the sys.modules entry (so `import` finds the stub) and the
    # attribute on `app.services` (so `from app.services import ...`
    # picks up the stub on import).
    import sys as _sys

    import app.services as _services_pkg

    monkeypatch.setitem(_sys.modules, "app.services.higgsfield_mcp", _StubMcp)
    monkeypatch.setattr(_services_pkg, "higgsfield_mcp", _StubMcp, raising=False)
    return chain, cfg, mcp_calls


# ── PKCE + URL helpers ──────────────────────────────────────────────────


def test_pkce_challenge_is_s256_base64url_nopad():
    verifier = "dBjftJeZ4CVP-mB92K27uhbUJU1p1r_wW1gFWFOEjXk"
    expected = "E9Melhoa2OwvFrEMTJguCHaoeK1t8URWbuGJSstw-cM"
    assert ha._pkce_challenge(verifier) == expected


def test_build_auth_url_includes_pkce_state_and_higgsfield_client():
    url = ha._build_auth_url("CHAL", "STATE")
    assert url.startswith(ha.HIGGSFIELD_AUTH_URL + "?")
    assert _TEST_CLIENT_ID in url
    assert "code_challenge=CHAL" in url
    assert "code_challenge_method=S256" in url
    assert "state=STATE" in url
    assert "response_type=code" in url


def test_build_oauth_blob_preserves_mcp_endpoint_when_present():
    """The per-account `mcp_endpoint` field, if Higgsfield returns one,
    MUST survive into the persisted blob — the MCP source registration
    layer prefers it over the canonical default."""
    blob = ha._build_oauth_blob(
        {
            "access_token": "hf_at",
            "refresh_token": "hf_rt",
            "expires_in": 3600,
            "mcp_endpoint": "https://api.higgsfield.ai/mcp/account/abc123",
        }
    )
    assert blob["access_token"] == "hf_at"
    assert blob["refresh_token"] == "hf_rt"
    assert blob["expires_in"] == 3600
    assert blob["mcp_endpoint"] == "https://api.higgsfield.ai/mcp/account/abc123"


# ── start_login ─────────────────────────────────────────────────────────


def test_start_login_creates_state_with_verification_url():
    mgr = _fresh_manager()
    state = mgr.start_login(_TENANT)
    assert state.tenant_id == _TENANT
    assert state.status == "pending"
    assert state.verification_url is not None
    assert state.verification_url.startswith(ha.HIGGSFIELD_AUTH_URL)
    assert state.code_verifier and len(state.code_verifier) >= 43


def test_start_login_replaces_existing_state_for_same_tenant():
    mgr = _fresh_manager()
    first = mgr.start_login(_TENANT)
    second = mgr.start_login(_TENANT)
    assert first.code_verifier != second.code_verifier
    assert first.login_id != second.login_id


# ── submit_code: happy path ─────────────────────────────────────────────


def test_submit_code_happy_path_writes_oauth_blob_and_registers_mcp(monkeypatch):
    """The contract Wave 1a depends on: a higgsfield_oauth row lands in
    the vault carrying the JSON blob, and register_for_tenant fires so
    the per-tenant MCP source exists immediately."""
    mgr = _fresh_manager()
    state = mgr.start_login(_TENANT)

    captured_writes: list = []
    _, _, mcp_calls = _stub_db(monkeypatch, captured_writes)

    def fake_exchange(code, verifier):
        assert code == "HF_AUTH_CODE"
        assert verifier == state.code_verifier
        return {
            "access_token": "hf_at",
            "refresh_token": "hf_rt",
            "token_type": "Bearer",
            "expires_in": 3600,
        }

    monkeypatch.setattr(ha, "_exchange_code_for_tokens", fake_exchange)

    result = mgr.submit_code(_TENANT, "  HF_AUTH_CODE  ")
    assert result is state
    assert result.status == "connected"
    assert result.connected is True
    assert result.error is None

    keys = [w["credential_key"] for w in captured_writes]
    # Plan specifies higgsfield_oauth as the canonical key. We also
    # store access_token and refresh_token for convenience consumers.
    assert "higgsfield_oauth" in keys
    assert "access_token" in keys
    assert "refresh_token" in keys

    blob_row = next(w for w in captured_writes if w["credential_key"] == "higgsfield_oauth")
    blob = json.loads(blob_row["plaintext_value"])
    assert blob["access_token"] == "hf_at"
    assert blob["refresh_token"] == "hf_rt"

    # MCP source registration must fire so the Marketing/Sales agent
    # picks up the Higgsfield connector on its next discover_mcp_tools.
    assert len(mcp_calls) == 1
    assert mcp_calls[0]["oauth_blob"]["access_token"] == "hf_at"


def test_submit_code_rejects_response_without_access_token(monkeypatch):
    """Higgsfield not returning an access_token is fatal — there's
    nothing for MCP calls to authenticate with. Don't persist."""
    mgr = _fresh_manager()
    mgr.start_login(_TENANT)

    captured_writes: list = []
    _stub_db(monkeypatch, captured_writes)

    monkeypatch.setattr(
        ha,
        "_exchange_code_for_tokens",
        lambda code, verifier: {"expires_in": 3600},  # no access_token
    )

    result = mgr.submit_code(_TENANT, "any-code")
    assert result.status == "failed"
    assert result.connected is False
    assert "access_token" in (result.error or "")
    assert captured_writes == []


# ── submit_code: sad path ───────────────────────────────────────────────


def test_submit_code_translates_invalid_grant_to_user_message(monkeypatch):
    mgr = _fresh_manager()
    mgr.start_login(_TENANT)

    captured_writes: list = []
    _stub_db(monkeypatch, captured_writes)

    def raising_exchange(code, verifier):
        raise ha._OAuthExchangeError(
            safe_message="400 invalid_grant: Bad Request",
            user_message=(
                "Higgsfield rejected the authorization code (it may have "
                "expired or already been used). Click Connect to start a "
                "new flow."
            ),
        )

    monkeypatch.setattr(ha, "_exchange_code_for_tokens", raising_exchange)

    result = mgr.submit_code(_TENANT, "expired-code")
    assert result.status == "failed"
    assert result.connected is False
    assert "authorization code" in (result.error or "").lower()
    # Raw upstream body MUST NOT leak.
    assert "400" not in (result.error or "")
    assert captured_writes == []


def test_submit_code_handles_empty_code_without_calling_higgsfield(monkeypatch):
    mgr = _fresh_manager()
    mgr.start_login(_TENANT)

    calls = []

    def spy_exchange(code, verifier):
        calls.append(code)
        return {"access_token": "x"}

    monkeypatch.setattr(ha, "_exchange_code_for_tokens", spy_exchange)

    result = mgr.submit_code(_TENANT, "   \n  ")
    assert result.status == "failed"
    assert "required" in (result.error or "").lower()
    assert calls == []


# ── submit_code: idempotency ────────────────────────────────────────────


def test_submit_code_is_idempotent_after_success(monkeypatch):
    mgr = _fresh_manager()
    mgr.start_login(_TENANT)

    captured_writes: list = []
    _stub_db(monkeypatch, captured_writes)

    exchange_calls = []

    def counting_exchange(code, verifier):
        exchange_calls.append(code)
        return {
            "access_token": "hf_at",
            "refresh_token": "hf_rt",
            "expires_in": 3600,
        }

    monkeypatch.setattr(ha, "_exchange_code_for_tokens", counting_exchange)

    first = mgr.submit_code(_TENANT, "AUTH_CODE")
    assert first.status == "connected"
    assert len(exchange_calls) == 1
    rows_after_first = len(captured_writes)

    second = mgr.submit_code(_TENANT, "AUTH_CODE")
    assert second.status == "connected"
    assert second.connected is True
    assert len(exchange_calls) == 1  # not re-burned
    assert len(captured_writes) == rows_after_first


def test_submit_code_returns_none_for_unknown_tenant():
    mgr = _fresh_manager()
    assert mgr.submit_code("no-such-tenant", "any-code") is None


# ── /start route: missing env → 503 ─────────────────────────────────────


def test_start_login_503_when_env_missing(monkeypatch):
    """When neither HIGGSFIELD_OAUTH_CLIENT_ID nor _SECRET is set,
    _load_higgsfield_oauth_client raises RuntimeError. The route layer
    must translate that to a 503 with operator-friendly detail."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from app.api import deps
    from app.api.v1.higgsfield_auth import router as higgsfield_router

    monkeypatch.delenv("HIGGSFIELD_OAUTH_CLIENT_ID", raising=False)
    monkeypatch.delenv("HIGGSFIELD_OAUTH_CLIENT_SECRET", raising=False)

    user = MagicMock()
    user.id = uuid.uuid4()
    user.tenant_id = uuid.UUID(_TENANT)
    user.is_active = True

    app = FastAPI()
    app.include_router(higgsfield_router, prefix="/api/v1/higgsfield-auth")
    app.dependency_overrides[deps.get_db] = lambda: MagicMock()
    app.dependency_overrides[deps.get_current_active_user] = lambda: user

    client = TestClient(app)
    resp = client.post("/api/v1/higgsfield-auth/start")
    assert resp.status_code == 503, resp.text
    detail = resp.json().get("detail", "")
    assert "higgsfield" in detail.lower()
    assert "client_id" in detail.lower() or "configured" in detail.lower()
