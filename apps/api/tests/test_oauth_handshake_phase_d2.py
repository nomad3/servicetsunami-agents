"""Tests for Phase D-2 body of run_oauth_handshake (#295)."""
from __future__ import annotations

import uuid
from unittest.mock import MagicMock, patch

import httpx
import pytest

from app.workflows.activities import oauth_handshake_activities as oha


def test_non_higgsfield_provider_returns_stub():
    """Phase D-2 only implements Higgsfield. Other providers stay
    on the legacy subprocess.run path — return success=False."""
    for provider in ("gemini_cli", "claude", "codex"):
        out = oha.run_oauth_handshake(
            provider=provider,
            tenant_id=str(uuid.uuid4()),
            code="abc",
            code_verifier="ver",
            redirect_uri="https://x/cb",
        )
        assert out["success"] is False
        assert out["reason"] == "phase_d2_only_higgsfield_supported"


def test_higgsfield_token_exchange_http_failure_returns_structured_error(
    monkeypatch,
):
    """A 401/500 from Higgsfield MUST NOT raise — caller gets a
    structured error and falls back to the legacy reconnect path."""
    response = MagicMock()
    response.status_code = 500
    response.text = '"server error"'

    class _Raise:
        def __call__(self):
            raise httpx.HTTPStatusError(
                "500", request=None, response=response,
            )

    response.raise_for_status = _Raise()
    client = MagicMock()
    client.__enter__.return_value = client
    client.__exit__.return_value = False
    client.post.return_value = response

    with patch.object(oha.httpx, "Client", return_value=client):
        out = oha.run_oauth_handshake(
            provider="higgsfield",
            tenant_id=str(uuid.uuid4()),
            code="abc",
            code_verifier="ver",
            redirect_uri="https://x/cb",
        )
    assert out["success"] is False
    assert out["reason"] == "higgsfield_token_exchange_failed"


def test_higgsfield_missing_access_token_returns_error(monkeypatch):
    """Higgsfield 200 with malformed body (no access_token) → caller
    sees a clean reason, not a KeyError."""
    response = MagicMock()
    response.json.return_value = {"refresh_token": "r1"}  # no access_token
    response.raise_for_status = lambda: None
    client = MagicMock()
    client.__enter__.return_value = client
    client.__exit__.return_value = False
    client.post.return_value = response

    with patch.object(oha.httpx, "Client", return_value=client):
        out = oha.run_oauth_handshake(
            provider="higgsfield",
            tenant_id=str(uuid.uuid4()),
            code="abc",
            code_verifier="ver",
            redirect_uri="https://x/cb",
        )
    assert out["success"] is False
    assert out["reason"] == "higgsfield_response_missing_access_token"


def test_higgsfield_no_integration_config_row_skips_vault_write(
    monkeypatch,
):
    """If the IntegrationConfig row doesn't exist, the activity must
    NOT silently create one (the api-side handler is supposed to do
    that before kicking off the flow). Return a structured error so
    the operator can debug."""
    # Mock the token exchange to succeed.
    response = MagicMock()
    response.json.return_value = {
        "access_token": "tok-1",
        "refresh_token": "r-1",
        "expires_in": 3600,
    }
    response.raise_for_status = lambda: None
    client = MagicMock()
    client.__enter__.return_value = client
    client.__exit__.return_value = False
    client.post.return_value = response

    # Mock the DB to return no IntegrationConfig.
    fake_db = MagicMock()
    fake_db.query.return_value.filter.return_value.first.return_value = None
    import sys
    fake_session_module = MagicMock()
    fake_session_module.SessionLocal = lambda: fake_db
    monkeypatch.setitem(sys.modules, "app.db.session", fake_session_module)

    with patch.object(oha.httpx, "Client", return_value=client):
        out = oha.run_oauth_handshake(
            provider="higgsfield",
            tenant_id=str(uuid.uuid4()),
            code="abc",
            code_verifier="ver",
            redirect_uri="https://x/cb",
        )
    assert out["success"] is False
    assert out["reason"] == "no_integration_config_row"


def test_higgsfield_happy_path_persists_both_tokens(monkeypatch):
    """End-to-end success: token exchange returns access+refresh,
    both get stored to the vault, activity returns success=True
    with refresh_token_stored=True."""
    # Mock the token exchange.
    response = MagicMock()
    response.json.return_value = {
        "access_token": "tok-1",
        "refresh_token": "r-1",
        "expires_in": 3600,
    }
    response.raise_for_status = lambda: None
    client = MagicMock()
    client.__enter__.return_value = client
    client.__exit__.return_value = False
    client.post.return_value = response

    # Mock IntegrationConfig + store_credential.
    fake_cfg = MagicMock()
    fake_cfg.id = uuid.uuid4()
    fake_db = MagicMock()
    fake_db.query.return_value.filter.return_value.first.return_value = fake_cfg
    import sys
    fake_session_module = MagicMock()
    fake_session_module.SessionLocal = lambda: fake_db
    monkeypatch.setitem(sys.modules, "app.db.session", fake_session_module)

    store_calls: list[dict] = []

    def _capture_store(db, *, config_id, tenant_id, credential_key, plaintext_value):
        store_calls.append({
            "config_id": config_id,
            "tenant_id": tenant_id,
            "credential_key": credential_key,
            "plaintext_value": plaintext_value,
        })

    fake_secrets = MagicMock()
    fake_secrets.store_credential = _capture_store
    monkeypatch.setitem(
        sys.modules, "app.services.integration_secrets", fake_secrets,
    )

    with patch.object(oha.httpx, "Client", return_value=client):
        out = oha.run_oauth_handshake(
            provider="higgsfield",
            tenant_id=str(uuid.uuid4()),
            code="abc",
            code_verifier="ver",
            redirect_uri="https://x/cb",
        )

    assert out["success"] is True
    assert out["access_token_stored"] is True
    assert out["refresh_token_stored"] is True
    assert out["expires_in"] == 3600
    # Both credentials persisted
    keys = {c["credential_key"] for c in store_calls}
    assert keys == {"access_token", "refresh_token"}


def test_higgsfield_happy_path_without_refresh_token(monkeypatch):
    """Higgsfield doesn't always return refresh_token. Activity
    still succeeds + persists access_token; refresh_token_stored=False."""
    response = MagicMock()
    response.json.return_value = {
        "access_token": "tok-only",
        "expires_in": 3600,
    }
    response.raise_for_status = lambda: None
    client = MagicMock()
    client.__enter__.return_value = client
    client.__exit__.return_value = False
    client.post.return_value = response

    fake_cfg = MagicMock()
    fake_cfg.id = uuid.uuid4()
    fake_db = MagicMock()
    fake_db.query.return_value.filter.return_value.first.return_value = fake_cfg
    import sys
    fake_session_module = MagicMock()
    fake_session_module.SessionLocal = lambda: fake_db
    monkeypatch.setitem(sys.modules, "app.db.session", fake_session_module)

    store_calls: list[str] = []

    def _capture_store(db, *, config_id, tenant_id, credential_key, plaintext_value):
        store_calls.append(credential_key)

    fake_secrets = MagicMock()
    fake_secrets.store_credential = _capture_store
    monkeypatch.setitem(
        sys.modules, "app.services.integration_secrets", fake_secrets,
    )

    with patch.object(oha.httpx, "Client", return_value=client):
        out = oha.run_oauth_handshake(
            provider="higgsfield",
            tenant_id=str(uuid.uuid4()),
            code="abc",
            code_verifier="ver",
            redirect_uri="https://x/cb",
        )

    assert out["success"] is True
    assert out["access_token_stored"] is True
    assert out["refresh_token_stored"] is False
    # Only access_token was stored
    assert store_calls == ["access_token"]
