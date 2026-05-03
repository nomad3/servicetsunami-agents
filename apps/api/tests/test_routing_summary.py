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
    _classify_exception,
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
    ``cli_platform_resolver.classify_error`` plus the ``exception`` /
    ``internal_error`` exception classifiers) must have a friendly
    label. If a new classification is added without a label, the UI
    will fall back to the generic explanation but the test catches it."""
    expected = {"quota", "auth", "missing_credential", "exception", "internal_error"}
    assert expected.issubset(_FALLBACK_REASON_LABELS.keys())


# ── C2: chain-exhausted error_state surface ────────────────────────────

def test_summary_renders_chain_exhausted_state():
    """When all CLIs in the chain returned errors, the summary must
    surface error_state="exhausted" + last_attempted so the UI can
    render "Tried X — chain exhausted" instead of silently dropping
    the footer entirely. C2 from the PR #256 review."""
    s = _build_routing_summary(
        served_by=None, requested="claude_code",
        chain_length=3, fallback_reason="quota",
        error_state="exhausted", last_attempted="opencode",
    )
    assert s["error_state"] == "exhausted"
    assert s["last_attempted_platform"] == "opencode"
    assert s["last_attempted"] == "OpenCode (local)"
    assert s["fallback_reason"] == "quota"
    # served_by is None — there's no successful CLI to attribute
    assert s["served_by_platform"] is None


# ── M9: case-insensitive comparison ────────────────────────────────────

def test_fallback_comparison_is_case_insensitive():
    """Future drift: if someone stores ``platform="Copilot_CLI"``
    (capitalized) instead of snake_case, the served-vs-requested
    comparison must NOT spuriously fire fallback. M9 from the review."""
    s = _build_routing_summary(
        served_by="Copilot_CLI", requested="copilot_cli",
        chain_length=1, fallback_reason=None,
    )
    assert "fallback_reason" not in s
    assert s["chain_length"] == 1


# ── I4: exception classification ───────────────────────────────────────

def test_classify_exception_buckets_transient_vs_internal():
    """CancelledError / TimeoutError / ConnectionError → "exception"
    (transient, retry helps). Pydantic ValidationError / TypeError /
    KeyError → "internal_error" (programming bug, retry won't help).
    Conservative default: anything else → "exception". I4 from review."""
    import asyncio
    # Transient
    assert _classify_exception(asyncio.CancelledError()) == "exception"
    assert _classify_exception(TimeoutError()) == "exception"
    assert _classify_exception(ConnectionError()) == "exception"
    assert _classify_exception(ConnectionResetError()) == "exception"
    # Internal — programming errors
    assert _classify_exception(TypeError("bad")) == "internal_error"
    assert _classify_exception(AttributeError("bad")) == "internal_error"
    assert _classify_exception(KeyError("bad")) == "internal_error"
    assert _classify_exception(ValueError("bad")) == "internal_error"
    # Default — unknown bucket
    class _Custom(Exception):
        pass
    assert _classify_exception(_Custom("?")) == "exception"
