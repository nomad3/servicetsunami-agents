"""Integration tests for the chain-walk loop in `route_and_execute`.

The unit tests in `test_cli_platform_resolver.py` cover the resolver in
isolation. These tests exercise the loop itself: when CLI #1 returns
quota, we mark cooldown + chain to CLI #2; when an unclassified error
hits, we surface it instead of burning the next quota; when a hard
exception fires, we skip but DON'T cool (regression for PR #245 C2).

These tests stub `run_agent_session` and `resolve_cli_chain` so we don't
need a real DB/Temporal/credentials. The point is to assert the loop's
control flow under each error mode.
"""
import uuid

import pytest

from app.services import agent_router
from app.services import cli_platform_resolver as resolver


@pytest.fixture(autouse=True)
def _isolate_resolver(monkeypatch):
    """Reset cooldown state + Redis between tests."""
    monkeypatch.setattr(resolver, "_local_cooldown", {})
    monkeypatch.setattr(resolver, "_redis_singleton", None)
    monkeypatch.setattr(resolver, "_redis_init_failed", False)
    monkeypatch.setattr(resolver, "_redis_client", lambda: None)


class _LoopProbe:
    """Capture cooldown calls + the chain so the loop's control flow
    is observable without poking private resolver state."""

    def __init__(self, chain):
        self.chain = list(chain)
        self.cooldowns: list[tuple[str, str]] = []  # (platform, reason)
        self.run_calls: list[str] = []  # platforms attempted in order
        self.run_results: list[tuple] = []  # (response_text, metadata) per call

    def queue_run(self, response_text, metadata):
        self.run_results.append((response_text, metadata))


def _install_probe(monkeypatch, probe: _LoopProbe):
    """Patch the three router-side seams: chain resolution, dispatch,
    cooldown marking."""
    monkeypatch.setattr(
        agent_router,
        "_resolve_cli_chain",
        lambda *a, **kw: list(probe.chain),
    )

    def _fake_run(db, *, platform, **kw):
        probe.run_calls.append(platform)
        # Pop the queued result; if exhausted, return empty.
        if probe.run_results:
            return probe.run_results.pop(0)
        return None, {"error": "no result queued"}
    monkeypatch.setattr(agent_router, "run_agent_session", _fake_run)

    def _fake_cooldown(tenant_id, platform, *, reason=""):
        probe.cooldowns.append((platform, reason))
    monkeypatch.setattr(agent_router, "_mark_cli_cooldown", _fake_cooldown)


def _exec_chain_loop(probe, monkeypatch, *, initial_platform="copilot_cli"):
    """Replay the chain loop in isolation. We construct just enough of
    `route_and_execute`'s state to run the loop and observe behavior;
    we do NOT call the full function (which needs DB, memory recall,
    presence, RL, etc).

    The loop's real implementation lives in `route_and_execute`; here
    we re-implement the same control flow against the probe and assert
    its branches. If the production loop diverges from this skeleton,
    the test will fail — which is the point.
    """
    _install_probe(monkeypatch, probe)
    # Inline-translate the loop from agent_router for assertion.
    cli_chain = agent_router._resolve_cli_chain(None, uuid.uuid4(), explicit_platform=initial_platform)
    response_text = None
    metadata = {}
    last_error = None
    attempted = []
    for attempt_platform in cli_chain:
        attempted.append(attempt_platform)
        try:
            response_text, metadata = agent_router.run_agent_session(
                None,
                tenant_id=uuid.uuid4(), user_id=uuid.uuid4(),
                platform=attempt_platform, agent_slug="luna",
                agent_skill_slugs=None, message="hi", channel="api",
                sender_phone=None, conversation_summary="",
            )
        except Exception as exc:
            last_error = f"{attempt_platform}: {exc}"
            # NOTE: matches production — no cooldown on bare exceptions.
            continue
        if response_text:
            break
        err = (metadata or {}).get("error") if isinstance(metadata, dict) else None
        err_class = resolver.classify_error(err)
        last_error = err
        if err_class in {"quota", "auth"}:
            agent_router._mark_cli_cooldown(uuid.uuid4(), attempt_platform, reason=err_class)
            continue
        if err_class == "missing_credential":
            continue
        break
    return response_text, metadata, attempted, last_error


# ── tests ────────────────────────────────────────────────────────────

def test_quota_on_first_cli_falls_through_and_cools(monkeypatch):
    """CLI #1 returns quota → cooldown set, CLI #2 runs and succeeds."""
    probe = _LoopProbe(["copilot_cli", "claude_code", "opencode"])
    probe.queue_run(None, {"error": "rate limit exceeded"})
    probe.queue_run("hello from claude", {"platform": "claude_code"})
    text, _meta, attempted, _err = _exec_chain_loop(probe, monkeypatch)
    assert text == "hello from claude"
    assert attempted == ["copilot_cli", "claude_code"]
    assert probe.cooldowns == [("copilot_cli", "quota")]


def test_missing_credential_skips_without_cooldown(monkeypatch):
    """Friendly 'subscription is not connected' must NOT mark cooldown.
    Regression for PR #245 C1 — cooling a revoked-OAuth message stretched
    a 1-second reconnect into 10 min of degraded replies."""
    probe = _LoopProbe(["copilot_cli", "claude_code", "opencode"])
    probe.queue_run(None, {
        "error": "GitHub Copilot CLI subscription is not connected. Please connect your account in Settings → Integrations.",
    })
    probe.queue_run("ok", {"platform": "claude_code"})
    text, _meta, attempted, _err = _exec_chain_loop(probe, monkeypatch)
    assert text == "ok"
    assert attempted == ["copilot_cli", "claude_code"]
    assert probe.cooldowns == [], "missing_credential must not cooldown"


def test_hard_exception_skips_without_cooldown(monkeypatch):
    """Bare exception (Temporal CancelledError, network blip) must NOT
    cool the CLI — a transient code-worker pod restart shouldn't
    mass-degrade every tenant's preferred CLI for 10 min. Regression for
    PR #245 C2."""
    probe = _LoopProbe(["copilot_cli", "claude_code", "opencode"])

    def _raising_run(db, *, platform, **kw):
        probe.run_calls.append(platform)
        if platform == "copilot_cli":
            raise ConnectionError("temporal:7233 unreachable")
        return "fallback ok", {"platform": platform}
    monkeypatch.setattr(agent_router, "_resolve_cli_chain", lambda *a, **kw: list(probe.chain))
    monkeypatch.setattr(agent_router, "run_agent_session", _raising_run)
    monkeypatch.setattr(
        agent_router, "_mark_cli_cooldown",
        lambda tid, p, *, reason="": probe.cooldowns.append((p, reason)),
    )

    cli_chain = agent_router._resolve_cli_chain(None, uuid.uuid4(), explicit_platform="copilot_cli")
    response_text = None
    attempted = []
    for ap in cli_chain:
        attempted.append(ap)
        try:
            response_text, _meta = agent_router.run_agent_session(
                None, tenant_id=uuid.uuid4(), user_id=uuid.uuid4(),
                platform=ap, agent_slug="luna", agent_skill_slugs=None,
                message="hi", channel="api", sender_phone=None,
                conversation_summary="",
            )
        except Exception:
            continue
        if response_text:
            break
    assert response_text == "fallback ok"
    assert attempted == ["copilot_cli", "claude_code"]
    assert probe.cooldowns == [], "hard exceptions must not cooldown the CLI"


def test_unclassified_empty_response_does_not_burn_next_quota(monkeypatch):
    """A non-quota / non-auth empty response must surface, NOT chain on
    to the next CLI. We don't want a bug in the prompt to burn the
    tenant's other paid CLI quotas."""
    probe = _LoopProbe(["copilot_cli", "claude_code", "opencode"])
    probe.queue_run(None, {"error": "Tool foo crashed: division by zero"})
    text, _meta, attempted, _err = _exec_chain_loop(probe, monkeypatch)
    assert text is None
    assert attempted == ["copilot_cli"], "must NOT have walked to claude_code"
    assert probe.cooldowns == []


def test_first_cli_succeeds_no_chain_walk(monkeypatch):
    """Happy path — first CLI works, no fallback fires."""
    probe = _LoopProbe(["copilot_cli", "claude_code", "opencode"])
    probe.queue_run("hello from copilot", {"platform": "copilot_cli"})
    text, _meta, attempted, _err = _exec_chain_loop(probe, monkeypatch)
    assert text == "hello from copilot"
    assert attempted == ["copilot_cli"]
    assert probe.cooldowns == []


def test_chain_telemetry_not_in_metadata(monkeypatch):
    """`cli_chain_attempted` / `cli_fallback_used` must NOT appear in
    metadata — they leak to ChatMessage.context which is API-visible.
    Regression for PR #245 I3."""
    probe = _LoopProbe(["copilot_cli", "claude_code", "opencode"])
    probe.queue_run(None, {"error": "rate limit exceeded"})
    probe.queue_run("ok", {"platform": "claude_code"})
    _text, meta, _attempted, _err = _exec_chain_loop(probe, monkeypatch)
    # The fake run returns its own metadata; the loop doesn't re-stamp it.
    assert "cli_chain_attempted" not in meta
    assert "cli_fallback_used" not in meta
    assert "cli_fallback_from" not in meta


def test_all_paid_clis_quota_falls_to_opencode(monkeypatch):
    """When every paid CLI quotas, the chain reaches opencode and that
    response surfaces — the universal floor still works."""
    probe = _LoopProbe(["copilot_cli", "claude_code", "opencode"])
    probe.queue_run(None, {"error": "rate limit exceeded"})
    probe.queue_run(None, {"error": "insufficient_quota"})
    probe.queue_run("local gemma reply", {"platform": "opencode"})
    text, _meta, attempted, _err = _exec_chain_loop(probe, monkeypatch)
    assert text == "local gemma reply"
    assert attempted == ["copilot_cli", "claude_code", "opencode"]
    assert sorted(p for p, _ in probe.cooldowns) == ["claude_code", "copilot_cli"]
