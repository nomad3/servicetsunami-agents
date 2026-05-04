"""Shared pytest fixtures for the MCP server test suite.

Provides a few helpers used across the tool-module tests:

- ``mock_ctx``: a stand-in for ``mcp.server.fastmcp.Context`` that the
  tools accept but never inspect beyond ``resolve_tenant_id``.
- ``patch_settings``: forces deterministic API base URL + internal key
  so we can assert on outbound HTTP requests.
- ``DummyHttpxClient``: a minimal async-context-manager ``httpx``
  replacement that records every call and returns scripted responses.

These intentionally avoid mocking our own helpers (``resolve_tenant_id``,
config readers) — only the network layer and external SDKs are stubbed.
"""
from __future__ import annotations

from types import SimpleNamespace
from typing import Any, Callable, Dict, List, Optional, Sequence

import pytest


# Test env defaults so tools that read os.environ at import time get
# stable values. We use a session-scoped autouse fixture wrapping
# `pytest.MonkeyPatch.context()` so the values are unset when the test
# session ends rather than leaking into a parent process or a subsequent
# session running in the same interpreter (e.g. under pytest-watch or a
# long-lived `python -m pytest` invoker).
_TEST_ENV_DEFAULTS = {
    "MCP_API_KEY": "test-mcp-key",
    "API_INTERNAL_KEY": "test-mcp-key",
    "API_BASE_URL": "http://api:8000",
}


@pytest.fixture(scope="session", autouse=True)
def _mcp_env_defaults():
    """Set test env defaults for the session and unset them on teardown.

    Uses ``MonkeyPatch.context()`` (rather than function-scoped
    ``monkeypatch``) so the values survive the entire session but don't
    bleed across sessions. Only sets a key if it isn't already populated,
    matching the previous ``os.environ.setdefault`` behaviour.
    """
    with pytest.MonkeyPatch.context() as mp:
        import os

        for key, value in _TEST_ENV_DEFAULTS.items():
            if not os.environ.get(key):
                mp.setenv(key, value)
        yield


@pytest.fixture
def mock_ctx():
    """A minimal Context substitute. Tools only use ``resolve_tenant_id``
    on it, which reads ``ctx.request_context.headers`` — by leaving that
    None we force the resolver to fall back to the explicit ``tenant_id``
    argument supplied by each test."""
    return SimpleNamespace(request_context=None)


@pytest.fixture
def patch_settings(monkeypatch):
    """Force deterministic API base url + internal key on src.config.settings."""
    from src import config as cfg

    monkeypatch.setattr(cfg.settings, "API_BASE_URL", "http://api:8000", raising=False)
    monkeypatch.setattr(cfg.settings, "API_INTERNAL_KEY", "test-mcp-key", raising=False)
    yield cfg.settings


class _DummyResponse:
    """Stand-in for httpx.Response. Returns scripted JSON / status."""

    def __init__(self, status_code: int = 200, json_data: Any = None, text: str = ""):
        self.status_code = status_code
        self._json = json_data if json_data is not None else {}
        self.text = text or (str(json_data) if json_data is not None else "")

    def json(self):
        return self._json

    def raise_for_status(self):
        if self.status_code >= 400:
            from httpx import HTTPStatusError, Request, Response
            raise HTTPStatusError(
                f"{self.status_code}",
                request=Request("GET", "http://test"),
                response=Response(self.status_code),
            )


class DummyHttpxClient:
    """Async-context-manager replacement for httpx.AsyncClient.

    Usage in a test::

        responses = {
            ("GET", "http://api:8000/foo"): DummyResponse(200, {"ok": True}),
        }
        client = DummyHttpxClient(responses)
        # then monkeypatch httpx.AsyncClient → lambda *a, **kw: client
        # or use pytest's monkeypatch helper.

    All HTTP method calls (get/post/put/delete/patch) record their
    arguments on ``client.calls`` for later assertion.
    """

    def __init__(
        self,
        responses: Optional[Dict] = None,
        default: Optional[_DummyResponse] = None,
        side_effect: Optional[Callable] = None,
    ):
        self.responses = responses or {}
        self.default = default or _DummyResponse(200, {})
        self.side_effect = side_effect
        self.calls: List[Dict[str, Any]] = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def _do(self, method: str, url: str, **kwargs):
        call = {"method": method, "url": url, **kwargs}
        self.calls.append(call)
        if self.side_effect is not None:
            r = self.side_effect(method, url, kwargs)
            if r is not None:
                return r
        # try exact match (METHOD, URL) then URL-only
        for key in [(method.upper(), url), url]:
            if key in self.responses:
                return self.responses[key]
        # fuzzy: match if URL endswith stored key string
        for key, resp in self.responses.items():
            if isinstance(key, str) and url.endswith(key):
                return resp
            if isinstance(key, tuple) and url.endswith(key[1]) and key[0].upper() == method.upper():
                return resp
        return self.default

    async def get(self, url, **kwargs):
        return await self._do("GET", url, **kwargs)

    async def post(self, url, **kwargs):
        return await self._do("POST", url, **kwargs)

    async def put(self, url, **kwargs):
        return await self._do("PUT", url, **kwargs)

    async def delete(self, url, **kwargs):
        return await self._do("DELETE", url, **kwargs)

    async def patch(self, url, **kwargs):
        return await self._do("PATCH", url, **kwargs)


@pytest.fixture
def DummyResponse():
    return _DummyResponse


@pytest.fixture
def make_client():
    """Factory that returns a DummyHttpxClient pre-loaded with scripted
    responses. Returns the client object so tests can assert on
    ``client.calls`` after the tool finishes."""
    def _factory(
        responses: Optional[Dict] = None,
        default_status: int = 200,
        default_json: Any = None,
        side_effect: Optional[Callable] = None,
    ) -> DummyHttpxClient:
        return DummyHttpxClient(
            responses=responses,
            default=_DummyResponse(default_status, default_json or {}),
            side_effect=side_effect,
        )
    return _factory
