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


# ── Phase 1.5 review I-A — declared apps/api fallback-trigger surface ──
#
# Phase 1.5's classifier widening (commit 5f874cac, fixup 90a42ea9-style)
# added 6 new fragments that the legacy `_QUOTA_PATTERNS` / `_AUTH_PATTERNS`
# regexes did NOT catch. Every chat turn flows through this resolver
# (agent_router.py:1026), so widening the classifier widens the chat-side
# fallback-trigger surface too. This was an INTENTIONAL correction — the
# legacy regexes undercounted real CLI quota / auth signals — but it's a
# behaviour change that deserves an explicit test record.
#
# Reviewer recommendation: pin the new triggers as a parametrized
# documentation test so a future change that flips one of these back to
# None has to do so deliberately.

@pytest.mark.parametrize(
    "stderr,expected_label",
    [
        # Auth signals that legacy regex missed → classifier now catches.
        ("HTTP 401 Unauthorized — not authorized", "auth"),
        # 403 is more specifically auth than quota — codex auth rule
        # (\\b403\\b) wins over copilot quota rule (forbidden), which
        # matches both legacy CODEX _AUTH_PATTERNS semantics and the
        # right operational meaning (403 = revoke + reauth, not throttle).
        ("HTTP 403 Forbidden", "auth"),
        # Quota signals that legacy regex missed → classifier now catches.
        ("you are out of extra usage for the month", "quota"),
        ("token limit exceeded for this conversation", "quota"),
        # Bare-token narrowing (review I-A): bare 'capacity' / 'billing'
        # in user prose must NOT trigger fallback. Anchored forms do.
        ("monthly billing invoice was generated", None),
        ("the capacity planning meeting is at 3pm", None),
        ("billing error: payment method expired", "quota"),
        ("server at capacity exceeded available shards", "quota"),
        # Bare 'forbidden' in copilot quota rule remains untightened —
        # pre-existing legacy COPILOT_CREDIT_ERROR_PATTERNS contract,
        # Phase 2 narrows naturally via per-adapter routing (each
        # adapter's stderr only sees its own platform's tokens). Pinned
        # here so a future tightening is deliberate, not a silent edit.
        # Note: 'HTTP 403 Forbidden' classifies as 'auth' not 'quota'
        # because the codex \\b403\\b auth rule wins over copilot quota.
        ("permission to read /etc is forbidden", "quota"),
    ],
)
def test_phase_1_5_widened_fallback_surface(stderr, expected_label):
    """Phase 1.5 widened the chat-side fallback trigger surface. This
    test records the new contract: which previously-None strings now
    fire fallback, and which bare tokens are intentionally narrowed
    so user prose doesn't false-positive."""
    assert r.classify_error(stderr) == expected_label, stderr


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
    priority order. As of 2026-05-05 the order is
    gemini_cli > codex > copilot_cli > claude_code > qwen_code > opencode
    (most tenants only have Google integrations connected, which auto-grant
    gemini for free, so we lead with that; Wave 1b adds qwen_code below
    the established subscription CLIs). If product preferences change,
    update _DEFAULT_PRIORITY in cli_platform_resolver.py and this test
    changes too — that's a feature, not a bug."""
    _stub_connected(monkeypatch, {"github", "claude_code", "gemini_cli", "codex"})
    chain = r.resolve_cli_chain(None, uuid.uuid4(), explicit_platform=None)
    # opencode always last
    assert chain[-1] == "opencode"
    # gemini_cli is the new first in the default priority
    assert chain[0] == "gemini_cli"
    # codex is second
    assert chain[1] == "codex"
    # All connected CLIs appear
    for cli in ("copilot_cli", "claude_code"):
        assert cli in chain


# ── Wave 1b — qwen_code ──────────────────────────────────────────────

def test_chain_when_only_qwen_connected(monkeypatch):
    """Tenant with only Qwen Code integrated → chain leads with qwen_code,
    falls through to opencode floor. Wave 1b BYOK happy path."""
    _stub_connected(monkeypatch, {"qwen_code"})
    chain = r.resolve_cli_chain(None, uuid.uuid4(), explicit_platform=None)
    assert chain[0] == "qwen_code"
    assert chain[-1] == "opencode"
    # The four established CLIs must NOT appear because their creds
    # aren't wired.
    assert set(chain).isdisjoint({"claude_code", "codex", "gemini_cli", "copilot_cli"})


def test_qwen_code_ranks_below_subscription_clis(monkeypatch):
    """With every CLI connected, qwen_code sits below the four
    established subscription CLIs (gemini > codex > copilot > claude)
    and above the opencode floor. Locks in the Wave 1b ranking decision
    so a future re-shuffle is an explicit edit."""
    _stub_connected(
        monkeypatch,
        {"gemini_cli", "codex", "github", "claude_code", "qwen_code"},
    )
    chain = r.resolve_cli_chain(None, uuid.uuid4(), explicit_platform=None)
    assert chain.index("qwen_code") > chain.index("claude_code")
    assert chain.index("qwen_code") < chain.index("opencode")


def test_qwen_code_explicit_preference_wins_when_available(monkeypatch):
    """When an agent sets preferred_cli=qwen_code AND the qwen_code
    integration is connected, qwen_code leads even if subscription CLIs
    are also available."""
    _stub_connected(monkeypatch, {"qwen_code", "gemini_cli"})
    chain = r.resolve_cli_chain(None, uuid.uuid4(), explicit_platform="qwen_code")
    assert chain[0] == "qwen_code"
    assert "gemini_cli" in chain
    assert chain.index("qwen_code") < chain.index("gemini_cli")


def test_qwen_code_cooldown_respected(monkeypatch):
    """A quota'd Qwen Code is filtered from the chain just like the
    other paid CLIs. Mirrors the copilot_cli cooldown contract."""
    tid = uuid.uuid4()
    _stub_connected(monkeypatch, {"qwen_code", "gemini_cli"})
    r.mark_cooldown(tid, "qwen_code", reason="quota")
    chain = r.resolve_cli_chain(None, tid, explicit_platform="qwen_code")
    assert "qwen_code" not in chain
    assert chain[0] == "gemini_cli"


def test_qwen_code_is_public_in_connected_clis_list(monkeypatch):
    """``connected_clis_for_tenant`` powers the chat-header InlineCliPicker;
    qwen_code must surface there (unlike opencode, which is the routing
    floor and intentionally excluded)."""
    _stub_connected(monkeypatch, {"qwen_code"})
    public = r.connected_clis_for_tenant(None, uuid.uuid4())
    assert "qwen_code" in public
    assert "opencode" not in public


# ── Kimi K2 (Wave 1c Lane B) ─────────────────────────────────────────


def test_chain_kimi_k2_via_integration(monkeypatch):
    """A tenant with the Moonshot AI integration connected gets kimi_k2
    in the resolver chain. Mirrors the gemini-via-gmail piggy-back test
    for the simpler 1:1 integration mapping."""
    _stub_connected(monkeypatch, {"kimi_k2"})
    chain = r.resolve_cli_chain(None, uuid.uuid4(), explicit_platform=None)
    assert "kimi_k2" in chain
    assert chain[-1] == "opencode"


def test_chain_kimi_k2_explicit_platform_wins(monkeypatch):
    """An agent with preferred_cli=kimi_k2 leads the chain when the
    integration is wired, ahead of any other connected CLIs."""
    _stub_connected(monkeypatch, {"kimi_k2", "claude_code", "github"})
    chain = r.resolve_cli_chain(None, uuid.uuid4(), explicit_platform="kimi_k2")
    assert chain[0] == "kimi_k2"
    # Default-priority fallbacks still appear after.
    assert "claude_code" in chain
    assert "copilot_cli" in chain


def test_connected_clis_for_tenant_surfaces_kimi(monkeypatch):
    """``connected_clis_for_tenant`` (the public list driving the
    InlineCliPicker) MUST include kimi_k2 when wired — NOT hidden the
    way opencode is."""
    _stub_connected(monkeypatch, {"kimi_k2"})
    listed = r.connected_clis_for_tenant(None, uuid.uuid4())
    assert "kimi_k2" in listed
    # opencode stays hidden — it's the routing floor, not user-pickable.
    assert "opencode" not in listed


def test_chain_kimi_k2_cooldown_respected(monkeypatch):
    """A quota'd kimi_k2 is filtered from the chain just like the other
    CLIs (only opencode is exempt from cooldown)."""
    tid = uuid.uuid4()
    _stub_connected(monkeypatch, {"kimi_k2", "claude_code"})
    r.mark_cooldown(tid, "kimi_k2", reason="quota")
    chain = r.resolve_cli_chain(None, tid, explicit_platform="kimi_k2")
    assert "kimi_k2" not in chain
    assert "claude_code" in chain


# ── Goose (Wave 2d) ──────────────────────────────────────────────────


def test_chain_goose_via_integration(monkeypatch):
    """A tenant with the Goose integration connected gets goose in the
    resolver chain. Mirrors the 1:1 integration → CLI mapping the other
    BYOK CLIs use."""
    _stub_connected(monkeypatch, {"goose"})
    chain = r.resolve_cli_chain(None, uuid.uuid4(), explicit_platform=None)
    assert "goose" in chain
    assert chain[-1] == "opencode"


def test_goose_ranks_below_subscription_clis_and_byok_alternates(monkeypatch):
    """With every CLI connected, goose sits below qwen_code / kimi_k2
    (the established BYOK alternates) and above the opencode floor.
    Locks in the Wave 2d ranking — a future re-shuffle must be an
    explicit edit to _DEFAULT_PRIORITY."""
    _stub_connected(
        monkeypatch,
        {"gemini_cli", "codex", "github", "claude_code", "qwen_code", "kimi_k2", "goose"},
    )
    chain = r.resolve_cli_chain(None, uuid.uuid4(), explicit_platform=None)
    assert chain.index("goose") > chain.index("kimi_k2")
    assert chain.index("goose") < chain.index("opencode")


def test_goose_explicit_preference_wins_when_available(monkeypatch):
    """An agent with preferred_cli=goose AND the goose integration wired
    leads the chain ahead of any other connected CLIs."""
    _stub_connected(monkeypatch, {"goose", "claude_code", "github"})
    chain = r.resolve_cli_chain(None, uuid.uuid4(), explicit_platform="goose")
    assert chain[0] == "goose"
    # Default-priority fallbacks still appear after.
    assert "claude_code" in chain
    assert "copilot_cli" in chain


def test_goose_not_connected_message_classifies_as_missing_credential():
    """The worker-side not-connected message for goose MUST classify as
    ``missing_credential`` so the orchestrator chain-walks past goose
    WITHOUT a 10-minute cooldown. Quick reconnect → next chat turn picks
    it up; a cooldown would mask that."""
    msg = (
        "Goose is not connected. "
        "Please connect your Goose account in Settings → Integrations."
    )
    assert r.classify_error(msg) == "missing_credential"


def test_kimi_k2_not_connected_message_classifies_as_missing_credential():
    """The worker-side not-connected message for kimi_k2 (returned both
    by ``_fetch_integration_credentials`` 404 path and the executor's
    empty-api-key path) MUST be classified as ``missing_credential`` so
    the orchestrator chain-walks past kimi_k2 WITHOUT a 10-minute
    cooldown. A quick reconnect should be picked up on the next chat
    turn, not masked by cooldown.

    Regression for B1 in the superpowers review of PR #552: the prior
    phrasing ("Please paste a MOONSHOT_API_KEY in Settings → Integrations")
    didn't hit any branch of the missing-credential alternation in
    ``packages/cli_orchestrator/classifier.py``."""
    msg = (
        "Kimi K2 is not connected. "
        "Please connect your Moonshot account in Settings → Integrations."
    )
    assert r.classify_error(msg) == "missing_credential"
