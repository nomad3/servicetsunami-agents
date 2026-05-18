"""Tests for the Higgsfield per-tenant MCP source registration layer.

The service owns the binding between "tenant has Higgsfield OAuth
credentials" and "an mcp_server_connectors row points the tenant at
Higgsfield's MCP endpoint". Discovery + tool calls go through the
existing mcp_server_connectors path; this module only exercises the
upsert + URL-resolution logic.

Coverage:
  * register_for_tenant resolves the MCP URL preference order
    (oauth_blob.mcp_endpoint > env override > canonical default).
  * register_for_tenant is idempotent — existing row gets refreshed
    in-place, no new row created.
  * unregister_for_tenant removes the row when present, returns False
    when there's nothing to drop.
  * register_for_tenant refuses to register without an access_token
    (no auth → no MCP source).
  * Tool-name fallback list matches the documented Higgsfield surface
    (Soul, Cinema Studio, Flux, Seedream, Nano Banana, Seedance, Kling,
    Veo, Minimax Hailuo, Ad Engine, virality prediction).
"""
from __future__ import annotations

import uuid
from unittest.mock import MagicMock

import pytest

pytest.importorskip("sqlalchemy")

from app.services import higgsfield_mcp


_TENANT = uuid.UUID("33333333-3333-3333-3333-333333333333")


# ── URL resolution ──────────────────────────────────────────────────────


def test_resolve_mcp_url_prefers_oauth_blob_endpoint(monkeypatch):
    """When Higgsfield's token response includes an mcp_endpoint we use
    it — that's the per-account endpoint and routes to the tenant's
    own credit pool."""
    monkeypatch.setenv("HIGGSFIELD_MCP_URL", "https://env-override.example/mcp")
    blob = {"mcp_endpoint": "https://api.higgsfield.ai/mcp/account/abc"}
    assert (
        higgsfield_mcp._resolve_mcp_url(blob)
        == "https://api.higgsfield.ai/mcp/account/abc"
    )


def test_resolve_mcp_url_falls_back_to_env_override(monkeypatch):
    monkeypatch.setenv("HIGGSFIELD_MCP_URL", "https://env-override.example/mcp")
    assert (
        higgsfield_mcp._resolve_mcp_url({})
        == "https://env-override.example/mcp"
    )


def test_resolve_mcp_url_canonical_default(monkeypatch):
    """Wave 1a chose api.higgsfield.ai/mcp as the canonical guess until
    Higgsfield publishes the real URL or the token response surfaces a
    per-account override."""
    monkeypatch.delenv("HIGGSFIELD_MCP_URL", raising=False)
    assert (
        higgsfield_mcp._resolve_mcp_url({})
        == "https://api.higgsfield.ai/mcp"
    )


def test_resolve_mcp_url_ignores_non_http_endpoint(monkeypatch):
    """A malformed mcp_endpoint (e.g. stripped to a path) shouldn't
    silently downgrade us to a non-URL. Fall back to env/default."""
    monkeypatch.delenv("HIGGSFIELD_MCP_URL", raising=False)
    blob = {"mcp_endpoint": "/relative/path"}
    assert (
        higgsfield_mcp._resolve_mcp_url(blob)
        == "https://api.higgsfield.ai/mcp"
    )


# ── register_for_tenant ─────────────────────────────────────────────────


def _stub_existing(monkeypatch, returns):
    """Patch _existing_connector to return a known value."""
    monkeypatch.setattr(
        higgsfield_mcp, "_existing_connector", lambda db, tenant_id: returns
    )


def test_register_for_tenant_creates_new_row_when_none_exists(monkeypatch):
    _stub_existing(monkeypatch, None)

    captured_kwargs: dict = {}

    def fake_create(db, **kwargs):
        captured_kwargs.update(kwargs)
        connector = MagicMock()
        connector.id = uuid.uuid4()
        return connector

    monkeypatch.setattr(
        higgsfield_mcp.mcp_server_connectors, "create_mcp_server", fake_create
    )
    monkeypatch.delenv("HIGGSFIELD_MCP_URL", raising=False)

    db = MagicMock()
    connector = higgsfield_mcp.register_for_tenant(
        db,
        tenant_id=_TENANT,
        oauth_blob={"access_token": "hf_at"},
    )
    assert connector is not None
    assert captured_kwargs["tenant_id"] == _TENANT
    assert captured_kwargs["name"] == "higgsfield"
    assert captured_kwargs["server_url"] == "https://api.higgsfield.ai/mcp"
    assert captured_kwargs["transport"] == "sse"
    assert captured_kwargs["auth_type"] == "bearer"
    assert captured_kwargs["auth_token"] == "hf_at"
    assert captured_kwargs["enabled"] is True


def test_register_for_tenant_refreshes_existing_row(monkeypatch):
    """Re-auth on the same tenant must NOT create a duplicate
    connector — the agent's tool_groups binding by connector id would
    otherwise fork between the old and new row."""
    existing = MagicMock()
    existing.id = uuid.uuid4()
    existing.server_url = "https://old.example/mcp"
    existing.auth_token = "old_token"
    existing.enabled = False
    _stub_existing(monkeypatch, existing)

    create_calls: list = []

    def fake_create(db, **kwargs):
        create_calls.append(kwargs)
        return MagicMock()

    monkeypatch.setattr(
        higgsfield_mcp.mcp_server_connectors, "create_mcp_server", fake_create
    )
    monkeypatch.delenv("HIGGSFIELD_MCP_URL", raising=False)

    db = MagicMock()
    connector = higgsfield_mcp.register_for_tenant(
        db,
        tenant_id=_TENANT,
        oauth_blob={
            "access_token": "new_token",
            "mcp_endpoint": "https://new.example/mcp",
        },
    )
    assert connector is existing
    assert existing.server_url == "https://new.example/mcp"
    assert existing.auth_token == "new_token"
    assert existing.auth_type == "bearer"
    assert existing.enabled is True
    # No new row created.
    assert create_calls == []
    # db.commit fired during the refresh.
    assert db.commit.called


def test_register_for_tenant_refuses_without_access_token(monkeypatch):
    """An MCP source with no auth would 401 every call. Refuse to
    register; the auth route propagates this as a persisted-failed
    state so the user sees the error."""
    _stub_existing(monkeypatch, None)
    monkeypatch.delenv("HIGGSFIELD_MCP_URL", raising=False)
    db = MagicMock()
    with pytest.raises(ValueError) as exc:
        higgsfield_mcp.register_for_tenant(
            db, tenant_id=_TENANT, oauth_blob={}
        )
    assert "access_token" in str(exc.value)


# ── unregister_for_tenant ───────────────────────────────────────────────


def test_unregister_for_tenant_drops_existing_row(monkeypatch):
    existing = MagicMock()
    existing.id = uuid.uuid4()
    _stub_existing(monkeypatch, existing)

    deleted_with: dict = {}

    def fake_delete(db, tenant_id, connector_id):
        deleted_with["tenant_id"] = tenant_id
        deleted_with["connector_id"] = connector_id
        return True

    monkeypatch.setattr(
        higgsfield_mcp.mcp_server_connectors, "delete_mcp_server", fake_delete
    )

    db = MagicMock()
    assert higgsfield_mcp.unregister_for_tenant(db, tenant_id=_TENANT) is True
    assert deleted_with["tenant_id"] == _TENANT
    assert deleted_with["connector_id"] == existing.id


def test_unregister_for_tenant_returns_false_when_no_row():
    """No connector row → noop. /disconnect must still complete cleanly
    so revocation isn't blocked by a missing MCP source."""
    # Patch via monkeypatch is overkill here; the function reads the
    # connector via db.query directly.
    db = MagicMock()
    chain = MagicMock()
    chain.filter.return_value = chain
    chain.first.return_value = None
    db.query.return_value = chain
    assert higgsfield_mcp.unregister_for_tenant(db, tenant_id=_TENANT) is False


# ── Static tool-name fallback ───────────────────────────────────────────


def test_static_tool_names_cover_documented_higgsfield_surface():
    """The static fallback list is what the Marketing/Sales agent's
    tool_groups[\"higgsfield\"] resolves to before live discovery has
    populated mcp_server_connectors.tools_discovered. It must mention
    every capability the Wave 1a plan called out so the agent's
    prompting picks up the right tools."""
    names = higgsfield_mcp.HIGGSFIELD_TOOL_NAMES
    # Image
    for image_tool in ("soul", "cinema_studio", "flux", "seedream", "nano_banana"):
        assert any(image_tool in n for n in names), f"missing {image_tool}"
    # Video
    for video_tool in ("seedance", "kling", "veo", "minimax_hailuo"):
        assert any(video_tool in n for n in names), f"missing {video_tool}"
    # Marketing higher-order
    assert any("ad_engine" in n for n in names)
    assert any("virality_prediction" in n for n in names)
