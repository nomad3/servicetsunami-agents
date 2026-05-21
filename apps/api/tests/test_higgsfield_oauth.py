"""Tests for higgsfield_oauth token refresh helpers (#298).

Mirrors the inbox_monitor Google-refresh tests' pattern. We stub
httpx and the credential-vault retrieval so we exercise the
control flow without an actual Higgsfield endpoint.

Locked properties:
  - refresh_higgsfield_access_token NEVER raises (failure → None)
  - get_higgsfield_token_for_tenant prefers refresh; falls back to
    stored access_token; returns None when integration absent
  - Bad tenant_id is a clean None, not a crash
  - HTTP 401/500 from Higgsfield is caught and logged, not raised
"""
from __future__ import annotations

import uuid
from unittest.mock import MagicMock, patch

import httpx
import pytest


def test_refresh_returns_new_access_token_on_success():
    from app.services import higgsfield_oauth as ho

    response = MagicMock()
    response.json.return_value = {
        "access_token": "new-access-tok",
        "refresh_token": "rotated-refresh-tok",
        "expires_in": 3600,
    }
    response.raise_for_status = lambda: None

    client = MagicMock()
    client.__enter__.return_value = client
    client.__exit__.return_value = False
    client.post.return_value = response

    with patch.object(ho.httpx, "Client", return_value=client):
        out = ho.refresh_higgsfield_access_token(
            refresh_token="r-1", client_id="cid", client_secret="csec",
        )
    assert out == "new-access-tok"


def test_refresh_returns_none_on_http_error():
    """A 401 from Higgsfield (revoked refresh_token) must NOT raise —
    callers fall back to the stored access_token + surface the
    existing manual-reconnect path."""
    from app.services import higgsfield_oauth as ho

    response = MagicMock()
    response.status_code = 401
    response.text = '{"error":"invalid_grant"}'

    class _Raise:
        def __call__(self):
            raise httpx.HTTPStatusError(
                "401", request=None, response=response,
            )

    response.raise_for_status = _Raise()

    client = MagicMock()
    client.__enter__.return_value = client
    client.__exit__.return_value = False
    client.post.return_value = response

    with patch.object(ho.httpx, "Client", return_value=client):
        out = ho.refresh_higgsfield_access_token(refresh_token="r-1")
    assert out is None


def test_refresh_returns_none_on_network_error():
    from app.services import higgsfield_oauth as ho

    client = MagicMock()
    client.__enter__.return_value = client
    client.__exit__.return_value = False
    client.post.side_effect = httpx.ConnectError("unreachable")

    with patch.object(ho.httpx, "Client", return_value=client):
        out = ho.refresh_higgsfield_access_token(refresh_token="r-1")
    assert out is None


def test_refresh_returns_none_when_response_missing_access_token():
    """Higgsfield 200 with malformed body — refresh helper logs +
    returns None rather than propagating a KeyError."""
    from app.services import higgsfield_oauth as ho

    response = MagicMock()
    response.json.return_value = {"refresh_token": "still-here"}
    response.raise_for_status = lambda: None

    client = MagicMock()
    client.__enter__.return_value = client
    client.__exit__.return_value = False
    client.post.return_value = response

    with patch.object(ho.httpx, "Client", return_value=client):
        out = ho.refresh_higgsfield_access_token(refresh_token="r-1")
    assert out is None


def test_get_token_returns_none_when_integration_absent():
    """No IntegrationConfig row → return None (caller surfaces
    'not connected' to the user)."""
    from app.services import higgsfield_oauth as ho

    db = MagicMock()
    db.query.return_value.filter.return_value.first.return_value = None

    out = ho.get_higgsfield_token_for_tenant(db, str(uuid.uuid4()))
    assert out is None


def test_get_token_prefers_refresh_path_when_refresh_token_present():
    """When refresh_token is in the vault, refresh runs and the new
    token wins over the stored access_token."""
    from app.services import higgsfield_oauth as ho

    db = MagicMock()
    config = MagicMock()
    config.id = uuid.uuid4()
    db.query.return_value.filter.return_value.first.return_value = config

    with patch.object(ho, "retrieve_credentials_for_skill") as mock_creds, \
         patch.object(ho, "refresh_higgsfield_access_token") as mock_refresh:
        mock_creds.return_value = {
            "refresh_token": "r-1",
            "access_token": "stored-stale",
            "client_id": "cid",
            "client_secret": "csec",
        }
        mock_refresh.return_value = "fresh-tok"
        out = ho.get_higgsfield_token_for_tenant(db, str(uuid.uuid4()))
    assert out == "fresh-tok"
    mock_refresh.assert_called_once()


def test_get_token_falls_back_to_stored_when_refresh_fails():
    """Refresh helper returns None → fall back to stored access_token
    so caller still has SOMETHING to try (and will surface 401 via
    the existing path if it's expired)."""
    from app.services import higgsfield_oauth as ho

    db = MagicMock()
    config = MagicMock()
    config.id = uuid.uuid4()
    db.query.return_value.filter.return_value.first.return_value = config

    with patch.object(ho, "retrieve_credentials_for_skill") as mock_creds, \
         patch.object(ho, "refresh_higgsfield_access_token", return_value=None):
        mock_creds.return_value = {
            "refresh_token": "r-1",
            "access_token": "stored-stale-but-only-thing-we-have",
        }
        out = ho.get_higgsfield_token_for_tenant(db, str(uuid.uuid4()))
    assert out == "stored-stale-but-only-thing-we-have"


def test_get_token_uses_access_token_when_no_refresh_token_stored():
    """Higgsfield's OAuth response sometimes lacks refresh_token. We
    skip refresh and just return the stored access_token."""
    from app.services import higgsfield_oauth as ho

    db = MagicMock()
    config = MagicMock()
    config.id = uuid.uuid4()
    db.query.return_value.filter.return_value.first.return_value = config

    with patch.object(ho, "retrieve_credentials_for_skill") as mock_creds, \
         patch.object(ho, "refresh_higgsfield_access_token") as mock_refresh:
        mock_creds.return_value = {
            "access_token": "the-only-token",
            # no refresh_token
        }
        out = ho.get_higgsfield_token_for_tenant(db, str(uuid.uuid4()))
    assert out == "the-only-token"
    mock_refresh.assert_not_called()


def test_get_token_bad_tenant_id_returns_none():
    """Malformed tenant_id (e.g. not a UUID) returns None, not raise."""
    from app.services import higgsfield_oauth as ho

    db = MagicMock()
    out = ho.get_higgsfield_token_for_tenant(db, "not-a-uuid")
    assert out is None
