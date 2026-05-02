"""Tests for the CLI platform resolver — autodetect + fallback chain."""
import os
import time
import uuid

import pytest

from app.services import cli_platform_resolver as r


@pytest.fixture(autouse=True)
def _isolate_local_cooldown(monkeypatch):
    """Each test gets a fresh in-process cooldown dict and Redis disabled
    so we exercise the local fallback deterministically."""
    monkeypatch.setattr(r, "_local_cooldown", {})
    monkeypatch.setattr(r, "_redis_singleton", None)
    monkeypatch.setattr(r, "_redis_init_failed", False)
    monkeypatch.setattr(r, "_redis_client", lambda: None)


# ── classify_error ───────────────────────────────────────────────────

def test_classify_quota_phrasings():
    for s in [
        "rate limit exceeded",
        "Quota exceeded for organization",
        "insufficient_quota",
        "credit balance is too low",
        "out of tokens",
        "429 Too Many Requests",
        "API quota_exhausted",
    ]:
        assert r.classify_error(s) == "quota", s


def test_classify_auth_phrasings():
    for s in [
        "401 Unauthorized",
        "invalid_grant",
        "token expired",
        "authentication failed",
        "403 Forbidden",
    ]:
        assert r.classify_error(s) == "auth", s


def test_classify_missing_credential_does_not_match_auth():
    """The platform's friendly 'subscription is not connected' message
    must classify as ``missing_credential`` (chain-skip without cooldown),
    NOT ``auth`` (chain-skip + 10-min cooldown). Cooling on a config
    issue stretches a 1-second reconnect into 10 min of degraded replies.
    Regression for the C1 finding in the PR #245 review."""
    for s in [
        "GitHub Copilot CLI subscription is not connected and the local model is unavailable.",
        "Gemini CLI subscription is not connected. Please connect your account in Settings → Integrations.",
        "GitHub Copilot CLI is not connected. Please connect your account in Settings → Integrations.",
    ]:
        assert r.classify_error(s) == "missing_credential", s


def test_classify_short_form_not_connected():
    """Code-worker historically returned both long form ("X subscription
    is not connected. Please connect ...") AND short form ("X not
    connected"). The 2026-05-02 holistic review caught that the short
    form silently bypassed missing_credential classification, breaking
    chain fallback for tenants with a partially-wired CLI.

    PR-A standardized code-worker on the long form via
    ``_INTEGRATION_NOT_CONNECTED_MESSAGES``, but the regex was widened
    defensively to match the short form too — so this test pins both.
    """
    for s in [
        "Claude Code not connected",
        "Codex not connected",
        "Gemini CLI not connected",
        "GitHub not connected",
        "Copilot CLI not connected",
        "GitHub Copilot CLI not connected",
    ]:
        assert r.classify_error(s) == "missing_credential", s


def test_classify_does_not_falsely_match_user_phrases_about_not_connected():
    """The short-form regex anchors on a CLI-name word boundary, so
    a user message that happens to contain "not connected" doesn't
    classify as missing_credential. Keeps the false-positive rate
    near zero — a chat about "the database is not connected" or "my
    monitor is not connected" must NOT trigger chain skip."""
    for s in [
        "the database is not connected",
        "my monitor is not connected",
        "I am not connected to the VPN",
        "wifi was not connected when I tried",
    ]:
        assert r.classify_error(s) is None, s


def test_classify_returns_none_for_user_errors():
    """Don't burn another CLI's quota on a bug in the prompt or a tool
    misuse. The resolver must NOT classify these as retryable."""
    for s in [
        "Tool 'sql_query' raised: division by zero",
        "Could not parse response: invalid JSON",
        "Workflow returned empty response",
        "",
        None,
    ]:
        assert r.classify_error(s) is None, s


# ── cooldown ─────────────────────────────────────────────────────────

def test_cooldown_marks_and_clears(monkeypatch):
    tid = uuid.uuid4()
    assert r.is_in_cooldown(tid, "copilot_cli") is False
    r.mark_cooldown(tid, "copilot_cli", reason="quota")
    assert r.is_in_cooldown(tid, "copilot_cli") is True
    # Expire the cooldown by rewinding the stored deadline.
    key = r._cooldown_key(tid, "copilot_cli")
    r._local_cooldown[key] = time.time() - 1
    assert r.is_in_cooldown(tid, "copilot_cli") is False


def test_cooldown_does_not_apply_to_opencode():
    """opencode is the universal floor — never cool it down or the chain
    becomes empty for tenants with zero subscriptions."""
    tid = uuid.uuid4()
    r.mark_cooldown(tid, "opencode", reason="quota")
    assert r.is_in_cooldown(tid, "opencode") is False


# ── resolve_cli_chain (autodetect) ───────────────────────────────────

def _stub_connected(monkeypatch, integration_names: set[str]):
    """Patch get_connected_integrations to report a specific set."""
    def _fake(_db, _tid):
        return {n: {"connected": True, "name": n, "icon": ""} for n in integration_names}
    monkeypatch.setattr(
        "app.services.integration_status.get_connected_integrations",
        _fake,
    )


def test_chain_when_only_github_connected(monkeypatch):
    """Tenant with only Copilot integrated → chain leads with copilot_cli,
    falls through to opencode floor. This is the user's exact scenario."""
    _stub_connected(monkeypatch, {"github"})
    chain = r.resolve_cli_chain(None, uuid.uuid4(), explicit_platform=None)
    assert chain[0] == "copilot_cli"
    assert chain[-1] == "opencode"
    # claude_code / codex / gemini_cli must NOT appear because their
    # creds aren't wired.
    assert set(chain).isdisjoint({"claude_code", "codex", "gemini_cli"})


def test_chain_explicit_preference_wins_when_available(monkeypatch):
    """When an agent has preferred_cli=copilot_cli set AND github is
    connected, copilot_cli leads even if claude_code is also available."""
    _stub_connected(monkeypatch, {"github", "claude_code"})
    chain = r.resolve_cli_chain(None, uuid.uuid4(), explicit_platform="copilot_cli")
    assert chain[0] == "copilot_cli"
    assert "claude_code" in chain
    # Claude should still be in the chain as a fallback option.
    assert chain.index("copilot_cli") < chain.index("claude_code")


def test_chain_explicit_preference_dropped_when_unavailable(monkeypatch):
    """An imported Microsoft agent has preferred_cli=copilot_cli but the
    tenant never connected GitHub. The override is silently dropped and
    autodetect picks whatever IS connected."""
    _stub_connected(monkeypatch, {"claude_code"})
    chain = r.resolve_cli_chain(None, uuid.uuid4(), explicit_platform="copilot_cli")
    assert "copilot_cli" not in chain
    assert chain[0] == "claude_code"


def test_chain_no_subscriptions_falls_to_opencode(monkeypatch):
    """Tenant with zero CLI integrations → only opencode."""
    _stub_connected(monkeypatch, set())
    chain = r.resolve_cli_chain(None, uuid.uuid4(), explicit_platform=None)
    assert chain == ["opencode"]


def test_chain_skips_cooled_platform(monkeypatch):
    """A CLI in cooldown is filtered from the chain so we don't waste a
    request hitting it again."""
    tid = uuid.uuid4()
    _stub_connected(monkeypatch, {"github", "claude_code"})
    r.mark_cooldown(tid, "copilot_cli", reason="quota")
    chain = r.resolve_cli_chain(None, tid, explicit_platform="copilot_cli")
    assert "copilot_cli" not in chain
    assert chain[0] == "claude_code"


def test_chain_skip_cooldown_flag_includes_cooled_platforms(monkeypatch):
    """``skip_cooldown=True`` is for tests / admin diagnostics — the
    chain should include cooled platforms so you can verify they're
    actually wired."""
    tid = uuid.uuid4()
    _stub_connected(monkeypatch, {"github"})
    r.mark_cooldown(tid, "copilot_cli", reason="quota")
    chain = r.resolve_cli_chain(None, tid, explicit_platform=None, skip_cooldown=True)
    assert "copilot_cli" in chain


def test_chain_gemini_cli_via_google_integration(monkeypatch):
    """Gemini CLI auth piggy-backs on any google_* integration (it shares
    the same OAuth provider). A tenant with only gmail connected should
    still get gemini_cli in the chain."""
    _stub_connected(monkeypatch, {"gmail"})
    chain = r.resolve_cli_chain(None, uuid.uuid4(), explicit_platform=None)
    assert "gemini_cli" in chain


def test_default_priority_when_multiple_clis_connected(monkeypatch):
    """No explicit preference + multiple connected → use the default
    priority order (claude_code > copilot_cli > gemini_cli > codex >
    opencode). If product preferences change, this test changes too —
    that's a feature, not a bug."""
    _stub_connected(monkeypatch, {"github", "claude_code", "gemini_cli", "codex"})
    chain = r.resolve_cli_chain(None, uuid.uuid4(), explicit_platform=None)
    # opencode always last
    assert chain[-1] == "opencode"
    # claude_code is the first in the default priority
    assert chain[0] == "claude_code"
    # All connected CLIs appear
    for cli in ("copilot_cli", "gemini_cli", "codex"):
        assert cli in chain
