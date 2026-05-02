"""Tests for the GitHub MCP multi-account resolver helpers.

Covers the four branches of `_resolve_accounts` plus the
`_try_each_account` helper. The HTTP layer is stubbed via patched
`_list_github_accounts` so these tests don't need a live api or
credential vault.
"""
import asyncio
from unittest.mock import AsyncMock, patch

import pytest


# Lazy import inside fixtures because src.mcp_app imports MCP framework
# which has its own startup side effects.
def _load_module():
    from src.mcp_tools import github as gh
    return gh


@pytest.mark.asyncio
async def test_resolve_no_accounts_returns_empty():
    """No connected accounts → empty list. Caller must surface a
    "GitHub not connected" error."""
    gh = _load_module()
    with patch.object(gh, "_list_github_accounts", new=AsyncMock(return_value=([], None))):
        result = await gh._resolve_accounts("tenant-1", None)
    assert result == []


@pytest.mark.asyncio
async def test_resolve_explicit_email_wins_over_pin():
    """When the caller passes account_email, it overrides any tenant
    pin. This lets an LLM agent address a specific account explicitly."""
    gh = _load_module()
    accounts = [
        ("a@example.com", "token-a"),
        ("b@example.com", "token-b"),
    ]
    # Pin says A is primary, but caller asks for B
    with patch.object(gh, "_list_github_accounts", new=AsyncMock(return_value=(accounts, "a@example.com"))):
        result = await gh._resolve_accounts("tenant-1", "b@example.com")
    assert result == [("b@example.com", "token-b")]


@pytest.mark.asyncio
async def test_resolve_explicit_email_unknown_returns_empty():
    """Caller asked for an account that isn't connected — return empty
    rather than silently fanning out to the wrong account. Caller
    surfaces a useful 'not connected for this tenant' error."""
    gh = _load_module()
    accounts = [("a@example.com", "token-a")]
    with patch.object(gh, "_list_github_accounts", new=AsyncMock(return_value=(accounts, None))):
        result = await gh._resolve_accounts("tenant-1", "ghost@nowhere.com")
    assert result == []


@pytest.mark.asyncio
async def test_resolve_pin_wins_over_fanout():
    """No explicit email, pin set, pin matches a connected account →
    return ONLY that account. Saves wasted Graph round-trips on
    accounts that don't have repo access (e.g. EMU)."""
    gh = _load_module()
    accounts = [
        ("personal@example.com", "personal-tok"),
        ("emu@employer.com", "emu-tok"),
    ]
    with patch.object(
        gh, "_list_github_accounts",
        new=AsyncMock(return_value=(accounts, "personal@example.com")),
    ):
        result = await gh._resolve_accounts("tenant-1", None)
    assert result == [("personal@example.com", "personal-tok")]


@pytest.mark.asyncio
async def test_resolve_pin_case_insensitive():
    """Email comparison is case-insensitive — 'Alice@Corp.com' matches
    'alice@corp.com'."""
    gh = _load_module()
    accounts = [("alice@corp.com", "tok")]
    with patch.object(
        gh, "_list_github_accounts",
        new=AsyncMock(return_value=(accounts, "Alice@CORP.COM")),
    ):
        result = await gh._resolve_accounts("tenant-1", None)
    assert result == [("alice@corp.com", "tok")]


@pytest.mark.asyncio
async def test_resolve_stale_pin_falls_back_to_fanout():
    """Pin references an account that's no longer connected (admin
    disconnected after pinning) → fall back to fan-out across all
    connected accounts. Don't fail closed."""
    gh = _load_module()
    accounts = [
        ("personal@example.com", "personal-tok"),
        ("backup@example.com", "backup-tok"),
    ]
    with patch.object(
        gh, "_list_github_accounts",
        new=AsyncMock(return_value=(accounts, "removed@example.com")),
    ):
        result = await gh._resolve_accounts("tenant-1", None)
    # Falls through to fan-out
    assert len(result) == 2


@pytest.mark.asyncio
async def test_resolve_no_pin_returns_all():
    """No explicit email, no pin → fan out across all connected accounts.
    This is the default behavior for tenants with multiple GitHub
    accounts and no explicit primary preference."""
    gh = _load_module()
    accounts = [
        ("a@example.com", "tok-a"),
        ("b@example.com", "tok-b"),
        ("c@example.com", "tok-c"),
    ]
    with patch.object(gh, "_list_github_accounts", new=AsyncMock(return_value=(accounts, None))):
        result = await gh._resolve_accounts("tenant-1", None)
    assert len(result) == 3
    assert {e for e, _ in result} == {"a@example.com", "b@example.com", "c@example.com"}


# ── _try_each_account ────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_try_each_account_returns_first_success():
    """First account returns 200 → that's the response, no further
    accounts queried."""
    gh = _load_module()
    accounts = [("a@x.com", "tok-a"), ("b@x.com", "tok-b")]
    calls = []

    async def do(token):
        calls.append(token)
        if token == "tok-a":
            return ("ok", {"status": "success", "data": "from-a"})
        return ("ok", {"status": "success", "data": "from-b"})

    email, payload = await gh._try_each_account(accounts, do)
    assert email == "a@x.com"
    assert payload["data"] == "from-a"
    assert payload["account_email"] == "a@x.com"
    assert calls == ["tok-a"]  # b was never queried


@pytest.mark.asyncio
async def test_try_each_account_walks_past_404():
    """First account returns not_found → walk to next. Resource lives
    under a different account."""
    gh = _load_module()
    accounts = [("a@x.com", "tok-a"), ("b@x.com", "tok-b")]

    async def do(token):
        if token == "tok-a":
            return ("not_found", {"status": "error", "error": "404"})
        return ("ok", {"status": "success", "data": "from-b"})

    email, payload = await gh._try_each_account(accounts, do)
    assert email == "b@x.com"
    assert payload["data"] == "from-b"
    assert payload["account_email"] == "b@x.com"


@pytest.mark.asyncio
async def test_try_each_account_aborts_on_hard_error():
    """A 5xx / network error is "error" outcome → bubble up immediately,
    don't burn through every account on a transient failure."""
    gh = _load_module()
    accounts = [("a@x.com", "tok-a"), ("b@x.com", "tok-b")]
    calls = []

    async def do(token):
        calls.append(token)
        if token == "tok-a":
            return ("error", {"status": "error", "error": "GitHub returned 500"})
        return ("ok", {"status": "success"})

    email, payload = await gh._try_each_account(accounts, do)
    assert email is None
    assert "500" in payload["error"]
    assert calls == ["tok-a"]  # b never queried


@pytest.mark.asyncio
async def test_try_each_account_all_not_found():
    """All accounts return not_found → return last_err with the not-
    accessible message."""
    gh = _load_module()
    accounts = [("a@x.com", "tok-a"), ("b@x.com", "tok-b")]

    async def do(token):
        return ("not_found", {"status": "error", "error": "404"})

    email, payload = await gh._try_each_account(accounts, do)
    assert email is None
    assert payload["status"] == "error"
