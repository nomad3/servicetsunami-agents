"""Tests for src.mcp_auth header resolvers.

These helpers are called on every MCP tool invocation to extract
``X-Tenant-Id``, ``X-User-Id`` and verify ``X-Internal-Key`` from the
FastMCP request context.
"""
from __future__ import annotations

import os
from types import SimpleNamespace

import pytest

from src import mcp_auth


# ---------------------------------------------------------------------------
# resolve_tenant_id / resolve_user_id
# ---------------------------------------------------------------------------

def test_resolve_tenant_id_returns_none_for_missing_ctx():
    assert mcp_auth.resolve_tenant_id(None) is None
    assert mcp_auth.resolve_user_id(None) is None


def test_resolve_tenant_id_returns_none_when_no_request_context():
    ctx = SimpleNamespace()  # no .request_context attribute
    assert mcp_auth.resolve_tenant_id(ctx) is None


def test_resolve_tenant_id_from_dict_request_context():
    ctx = SimpleNamespace(request_context={"X-Tenant-Id": "t-1"})
    assert mcp_auth.resolve_tenant_id(ctx) == "t-1"


def test_resolve_tenant_id_from_dict_lowercase_key():
    ctx = SimpleNamespace(request_context={"x-tenant-id": "t-2"})
    assert mcp_auth.resolve_tenant_id(ctx) == "t-2"


def test_resolve_tenant_id_from_headers_dict():
    rc = SimpleNamespace(headers={"X-Tenant-Id": "t-3"})
    ctx = SimpleNamespace(request_context=rc)
    assert mcp_auth.resolve_tenant_id(ctx) == "t-3"


def test_resolve_tenant_id_from_headers_object_with_get():
    class _Headers:
        def __init__(self, m):
            self._m = m

        def get(self, k, default=None):
            return self._m.get(k.lower(), default)

    rc = SimpleNamespace(headers=_Headers({"x-tenant-id": "t-4"}))
    ctx = SimpleNamespace(request_context=rc)
    assert mcp_auth.resolve_tenant_id(ctx) == "t-4"


def test_resolve_tenant_id_falls_back_to_attribute():
    rc = SimpleNamespace(headers=None, x_tenant_id="t-5")
    ctx = SimpleNamespace(request_context=rc)
    assert mcp_auth.resolve_tenant_id(ctx) == "t-5"


def test_resolve_tenant_id_falls_back_to_tenant_id_alias():
    ctx = SimpleNamespace(request_context={"tenant_id": "t-alias"})
    # "X-Tenant-Id" is missing, falls back to "tenant_id"
    assert mcp_auth.resolve_tenant_id(ctx) == "t-alias"


def test_resolve_user_id_uses_x_user_id_header():
    ctx = SimpleNamespace(request_context={"X-User-Id": "u-1"})
    assert mcp_auth.resolve_user_id(ctx) == "u-1"


def test_resolve_user_id_falls_back_to_user_id_alias():
    ctx = SimpleNamespace(request_context={"user_id": "u-alias"})
    assert mcp_auth.resolve_user_id(ctx) == "u-alias"


# ---------------------------------------------------------------------------
# verify_internal_key
# ---------------------------------------------------------------------------

def test_verify_internal_key_missing_header_returns_false():
    ctx = SimpleNamespace(request_context={})
    assert mcp_auth.verify_internal_key(ctx) is False


def test_verify_internal_key_matches(monkeypatch):
    monkeypatch.setattr(mcp_auth, "INTERNAL_KEY", "secret-123")
    ctx = SimpleNamespace(request_context={"X-Internal-Key": "secret-123"})
    assert mcp_auth.verify_internal_key(ctx) is True


def test_verify_internal_key_rejects_mismatch(monkeypatch):
    monkeypatch.setattr(mcp_auth, "INTERNAL_KEY", "secret-123")
    ctx = SimpleNamespace(request_context={"X-Internal-Key": "wrong"})
    assert mcp_auth.verify_internal_key(ctx) is False


def test_verify_internal_key_accepts_alias_internal_key(monkeypatch):
    monkeypatch.setattr(mcp_auth, "INTERNAL_KEY", "abc")
    ctx = SimpleNamespace(request_context={"internal_key": "abc"})
    assert mcp_auth.verify_internal_key(ctx) is True


# ---------------------------------------------------------------------------
# Primary FastMCP shape — headers live at request_context.request.headers
#
# These tests pin the 2026-05-25 regression fix. Before this change,
# `_get_header()` only looked at `request_context.headers` directly —
# which is None on real FastMCP requests because the Starlette Request
# object is stored at `request_context.request`. The bug caused EVERY
# HTTP-transported tool call to land as `tier=anonymous`, regardless of
# what Authorization / X-Tenant-Id / X-Internal-Key the caller sent.
#
# FastMCP populates request_context.request via
# `ServerMessageMetadata(request_context=request)` in BOTH transports:
#   * SSE: mcp/server/sse.py:244
#   * Streamable-HTTP: mcp/server/streamable_http.py:403, 417, 505
# ---------------------------------------------------------------------------


def _make_starlette_like_ctx(headers: dict[str, str]):
    """Build a ctx that mirrors what FastMCP constructs on HTTP transports.

    The real shape is:
      ctx.request_context = RequestContext(
          request_id=..., meta=..., session=..., lifespan_context=...,
          request=<Starlette Request>,
      )
    where the Starlette Request has a case-insensitive `.headers` mapping.
    """
    class _Headers:
        """Mimic Starlette's case-insensitive Headers mapping."""

        def __init__(self, m: dict[str, str]):
            # Starlette lower-cases everything internally
            self._m = {k.lower(): v for k, v in m.items()}

        def get(self, k, default=None):
            return self._m.get(k.lower(), default)

    request_obj = SimpleNamespace(headers=_Headers(headers))
    rc = SimpleNamespace(request=request_obj)
    return SimpleNamespace(request_context=rc)


def test_resolve_tenant_id_via_request_context_request_headers():
    """The primary path: headers live at ctx.request_context.request.headers
    (Starlette Request object attached by FastMCP)."""
    ctx = _make_starlette_like_ctx({"X-Tenant-Id": "tenant-via-request"})
    assert mcp_auth.resolve_tenant_id(ctx) == "tenant-via-request"


def test_resolve_user_id_via_request_context_request_headers():
    ctx = _make_starlette_like_ctx({"X-User-Id": "user-via-request"})
    assert mcp_auth.resolve_user_id(ctx) == "user-via-request"


def test_verify_internal_key_via_request_context_request_headers(monkeypatch):
    monkeypatch.setattr(mcp_auth, "INTERNAL_KEY", "via-request-secret")
    ctx = _make_starlette_like_ctx({"X-Internal-Key": "via-request-secret"})
    assert mcp_auth.verify_internal_key(ctx) is True


def test_resolve_auth_context_authorization_via_request_headers():
    """End-to-end: Authorization Bearer token at the primary path should
    resolve to agent_token tier when the token is valid. Uses the
    real-shape ctx so this would have FAILED before the fix."""
    fake_auth_ctx = mcp_auth.AuthContext(
        tier="agent_token",
        tenant_id="t-X",
        agent_id="a-X",
        task_id="task-X",
    )

    import src.agent_token_verify as _atv

    original = _atv.decode_agent_token_if_present

    def _fake_decode(auth_header):
        # Only "succeed" when called with the test's Bearer header.
        if auth_header == "Bearer dummy-jwt":
            return fake_auth_ctx
        return None

    try:
        # Patch both where mcp_auth imported it (src.mcp_auth namespace)
        # and the source module — defense in depth against import-time
        # name binding.
        mcp_auth.decode_agent_token_if_present = _fake_decode
        _atv.decode_agent_token_if_present = _fake_decode

        ctx = _make_starlette_like_ctx({"Authorization": "Bearer dummy-jwt"})
        resolved = mcp_auth.resolve_auth_context(ctx)
        assert resolved.tier == "agent_token"
        assert resolved.tenant_id == "t-X"
        assert resolved.agent_id == "a-X"
    finally:
        mcp_auth.decode_agent_token_if_present = original
        _atv.decode_agent_token_if_present = original


def test_anonymous_when_no_headers_anywhere():
    """No request, no headers, no dict — should resolve as anonymous,
    matching the documented tier-4 fallthrough."""
    rc = SimpleNamespace(request=None)
    ctx = SimpleNamespace(request_context=rc)
    resolved = mcp_auth.resolve_auth_context(ctx)
    assert resolved.tier == "anonymous"


def test_request_present_but_no_headers_falls_through_to_other_branches():
    """If request exists but has no headers attribute, lookup falls
    through to the legacy dict/attr branches without crashing."""
    request_obj = SimpleNamespace()  # no .headers
    rc = SimpleNamespace(
        request=request_obj,
        # Add legacy dict header to confirm the next branch picks up
        headers={"X-Tenant-Id": "t-from-legacy-headers-attr"},
    )
    ctx = SimpleNamespace(request_context=rc)
    assert mcp_auth.resolve_tenant_id(ctx) == "t-from-legacy-headers-attr"
