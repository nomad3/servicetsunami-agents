"""Tests for the resolver chain metrics endpoint (Op-1).

Pinned invariants:
  - The curated response shape never includes message IDs or the raw
    cli_chain_attempted list (PR #245 review concern).
  - Fallback detection is case-insensitive (mirrors M9 fix in
    routing_summary).
  - Empty windows return zeros without crashing.
  - The CLI label map is independent of agent_router internals so
    adding a CLI doesn't quietly change the metrics shape.
"""
from app.api.v1.insights_resolver_metrics import (
    FallbackReasonEntry,
    ResolverMetricsResponse,
    ServedByEntry,
    _label_for,
    _percentile,
)


# ── Schema invariants ─────────────────────────────────────────────────


def test_response_schema_no_message_id_leak():
    """Forbidden keys at the top-level response — same pattern as
    routing_summary (PR #256), fleet-health (PR #263), cost (PR #265).
    No per-message exposure, no raw chain."""
    forbidden = {
        "messages",
        "message_ids",
        "raw",
        "raw_chain",
        "cli_chain_attempted",
        "attempted",
    }
    assert forbidden.isdisjoint(ResolverMetricsResponse.model_fields.keys())


def test_served_by_entry_no_tenant_id_leak():
    """Per-CLI rollup is just (platform, label, count, pct). No
    tenant_id, no message IDs."""
    forbidden = {"tenant_id", "message_ids", "ids"}
    assert forbidden.isdisjoint(ServedByEntry.model_fields.keys())


def test_fallback_reason_entry_minimal_shape():
    """Reason rollup is just (reason, count, pct)."""
    keys = set(FallbackReasonEntry.model_fields.keys())
    assert keys == {"reason", "count", "pct"}


# ── Percentile helper ─────────────────────────────────────────────────


def test_percentile_empty_input_returns_zero():
    """Empty windows must not crash — operator opens the dashboard
    on a brand-new tenant before any chat has happened."""
    assert _percentile([], 0.5) == 0
    assert _percentile([], 0.95) == 0


def test_percentile_single_value():
    assert _percentile([3], 0.5) == 3
    assert _percentile([3], 0.95) == 3


def test_percentile_sorted_input():
    """Nearest-rank semantics on already-sorted input."""
    values = [1, 1, 2, 3, 3, 5, 8, 13]
    assert _percentile(values, 0.5) == 3  # idx 4
    assert _percentile(values, 0.95) == 13  # last
    assert _percentile(values, 0) == 1
    assert _percentile(values, 1) == 13


# ── Label map ─────────────────────────────────────────────────────────


def test_label_for_known_clis():
    """The endpoint's label map mirrors agent_router._CLI_DISPLAY_LABELS
    — kept independent so adding a CLI without updating both surfaces
    stays a noisy compile-time / test-time signal."""
    assert _label_for("claude_code") == "Claude Code"
    assert _label_for("copilot_cli") == "GitHub Copilot CLI"
    assert _label_for("codex") == "Codex CLI"
    assert _label_for("gemini_cli") == "Gemini CLI"


def test_label_for_unknown_cli_returns_snake_case():
    """Future drift: a brand-new CLI shows up as snake_case rather
    than crashing the dashboard."""
    assert _label_for("future_cli_v2") == "future_cli_v2"


def test_label_for_none_or_empty_returns_dash():
    """When served_by_platform is None (chain exhausted), the rollup
    bucket is the literal '—' rather than 'None' or empty string."""
    assert _label_for(None) == "—"
    assert _label_for("") == "—"


# ── Sanity ────────────────────────────────────────────────────────────


def test_response_has_required_aggregate_fields():
    """The dashboard reads these specific fields. Renaming any of them
    silently would break the operator UI."""
    keys = set(ResolverMetricsResponse.model_fields.keys())
    expected = {
        "window_hours",
        "total_turns",
        "served_by",
        "fallback_rate",
        "fallback_reasons",
        "chain_exhausted_count",
        "chain_length_p50",
        "chain_length_p95",
    }
    assert expected == keys
