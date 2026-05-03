"""Tests for the curated routing_summary that lands in ChatMessage.context.

Distinct from the raw ``cli_chain_attempted`` debug telemetry (which
deliberately stays in logs only — PR #245 review C-I3 concern about
exposing internal routing decisions). This is the polished, customer-
facing summary the chat UI renders under each assistant message.
"""
from app.services.agent_router import (
    _build_routing_summary,
    _CLI_DISPLAY_LABELS,
    _FALLBACK_REASON_LABELS,
)


def test_summary_contains_friendly_label_for_known_clis():
    """Every CLI we route to has a polished display name. Internal
    snake_case identifiers stay in the data; the label is what the
    customer sees."""
    for snake, label in [
        ("claude_code", "Claude Code"),
        ("copilot_cli", "GitHub Copilot CLI"),
        ("codex", "Codex CLI"),
        ("gemini_cli", "Gemini CLI"),
    ]:
        s = _build_routing_summary(
            served_by=snake, requested=snake, chain_length=1, fallback_reason=None,
        )
        assert s["served_by"] == label
        assert s["served_by_platform"] == snake


def test_summary_no_fallback_when_served_matches_requested():
    """Happy path: tenant requested Copilot, Copilot served. No fallback
    fields should appear — the UI rendering should be the simple
    one-line "Served by GitHub Copilot CLI"."""
    s = _build_routing_summary(
        served_by="copilot_cli", requested="copilot_cli",
        chain_length=1, fallback_reason=None,
    )
    assert "fallback_reason" not in s
    assert "fallback_explanation" not in s
    assert "requested" not in s
    assert s["chain_length"] == 1


def test_summary_includes_fallback_when_served_differs_from_requested():
    """Fallback fired: tenant requested Claude, Copilot served. Footer
    should explain why ("rate limit / quota exceeded") so the customer
    understands what happened to their turn."""
    s = _build_routing_summary(
        served_by="copilot_cli", requested="claude_code",
        chain_length=2, fallback_reason="quota",
    )
    assert s["served_by"] == "GitHub Copilot CLI"
    assert s["requested_platform"] == "claude_code"
    assert s["requested"] == "Claude Code"
    assert s["fallback_reason"] == "quota"
    assert s["fallback_explanation"] == "rate limit / quota exceeded"
    assert s["chain_length"] == 2


def test_summary_handles_unknown_fallback_reason_gracefully():
    """An unrecognized classification (future drift) must NOT crash;
    the explanation falls back to a generic message."""
    s = _build_routing_summary(
        served_by="codex", requested="claude_code",
        chain_length=2, fallback_reason="weird_new_class",
    )
    assert s["fallback_reason"] == "weird_new_class"
    assert "fell back" in s["fallback_explanation"]


def test_summary_handles_unknown_cli_gracefully():
    """An unknown platform identifier (e.g. a new CLI shipped without
    updating the label map) shows the snake_case as the label rather
    than crashing."""
    s = _build_routing_summary(
        served_by="future_cli", requested="future_cli",
        chain_length=1, fallback_reason=None,
    )
    assert s["served_by"] == "future_cli"


def test_summary_chain_length_floor_is_one():
    """chain_length=0 (theoretically impossible — a chain that served
    a response must have attempted at least one CLI) is normalized to 1
    so the UI doesn't render "tried 0 CLIs"."""
    s = _build_routing_summary(
        served_by="copilot_cli", requested="copilot_cli",
        chain_length=0, fallback_reason=None,
    )
    assert s["chain_length"] == 1


def test_summary_does_not_leak_full_chain():
    """The summary deliberately excludes the raw `attempted` list of
    CLIs the resolver tried — that was the PR #245 review's exact
    concern about exposing internal routing decisions to end-users.
    Only the FINAL outcome (served_by / fallback_from / reason) is
    customer-facing; the full chain stays in operator logs."""
    s = _build_routing_summary(
        served_by="copilot_cli", requested="claude_code",
        chain_length=3, fallback_reason="quota",
    )
    forbidden_keys = {"chain", "cli_chain_attempted", "attempted"}
    assert forbidden_keys.isdisjoint(s.keys())


def test_fallback_reason_label_map_covers_all_classifications():
    """Every classification the resolver can emit (from
    ``cli_platform_resolver.classify_error``) must have a friendly
    label. If a new classification is added without a label, the UI
    will fall back to the generic explanation but the test catches it."""
    expected = {"quota", "auth", "missing_credential", "exception"}
    assert expected.issubset(_FALLBACK_REASON_LABELS.keys())
