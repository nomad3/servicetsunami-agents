"""Tests for the fleet-health endpoint helpers (Tier 3 of visibility roadmap).

Schema-level pinning + cursor encoding correctness. The full
endpoint test would need a populated DB which the existing test suite
spins up via conftest; this file focuses on the pure-logic surfaces
that don't need DB so the tests run on the host without docker-compose.
"""
import uuid
from datetime import datetime, timezone

from app.api.v1.insights_fleet_health import (
    FleetHealthRow,
    FleetHealthResponse,
    _agent_source,
    _decode_cursor,
    _encode_cursor,
)


# ── Schema invariants — curate-don't-dump (PR #248 / #256 / #260 lineage) ──

def test_fleet_health_row_does_not_leak_tenant_or_admin_flags():
    """The slim row schema must NOT carry tenant_id, raw User object,
    or admin flags. Owner is `email` only — same lean schema discipline
    as PR #248's UserBrief."""
    fields = (
        FleetHealthRow.model_fields
        if hasattr(FleetHealthRow, "model_fields")
        else FleetHealthRow.__fields__
    )
    forbidden = {"tenant_id", "is_superuser", "owner", "config", "metadata", "tenant"}
    assert forbidden.isdisjoint(fields.keys())


def test_fleet_health_row_only_exposes_aggregates_not_raw_audit_rows():
    """Audit log rows must never appear in the response — only counts
    + sums. A future regression that adds `audit_logs: list[...]` to the
    row schema would expose every individual call to the tenant."""
    fields = (
        FleetHealthRow.model_fields
        if hasattr(FleetHealthRow, "model_fields")
        else FleetHealthRow.__fields__
    )
    forbidden = {"audit_logs", "calls", "raw_invocations", "history"}
    assert forbidden.isdisjoint(fields.keys())
    # Aggregates that SHOULD be present
    expected_aggregates = {
        "invocations_24h", "invocations_7d", "tokens_used_7d", "cost_usd_7d",
    }
    assert expected_aggregates.issubset(fields.keys())


def test_fleet_health_response_no_pagination_leak():
    """The cursor is opaque — the response shouldn't expose offset/total
    counts that let a curious tenant size the agent table or do
    enumeration attacks against pagination edges."""
    fields = (
        FleetHealthResponse.model_fields
        if hasattr(FleetHealthResponse, "model_fields")
        else FleetHealthResponse.__fields__
    )
    forbidden = {"total", "offset", "page", "page_count", "agent_count"}
    assert forbidden.isdisjoint(fields.keys())


# ── source classifier ───────────────────────────────────────────────────

class _FakeAgent:
    def __init__(self, config):
        self.config = config


def test_agent_source_returns_native_when_no_config():
    """Agents created via the wizard / API directly have no
    `config.metadata.source` and should classify as native, not unknown."""
    assert _agent_source(_FakeAgent(None)) == "native"
    assert _agent_source(_FakeAgent({})) == "native"
    assert _agent_source(_FakeAgent({"metadata": {}})) == "native"
    assert _agent_source(_FakeAgent({"metadata": None})) == "native"


def test_agent_source_returns_importer_value():
    """When agent_importer wrote `config.metadata.source`, surface it
    verbatim. Mirrors the values agent_importer.py writes."""
    for src in ["copilot_studio", "ai_foundry", "crewai", "langchain", "autogen"]:
        agent = _FakeAgent({"metadata": {"source": src}})
        assert _agent_source(agent) == src


def test_agent_source_is_robust_to_malformed_config():
    """A non-dict `config.metadata` (legacy data, accidental string)
    must NOT crash — fall back to native."""
    assert _agent_source(_FakeAgent({"metadata": "broken"})) == "native"
    assert _agent_source(_FakeAgent("not-a-dict")) == "native"


# ── cursor encoding ─────────────────────────────────────────────────────

def test_cursor_round_trip_with_timestamp():
    ts = datetime(2026, 5, 3, 12, 0, 0, tzinfo=timezone.utc)
    aid = uuid.uuid4()
    encoded = _encode_cursor(ts, aid)
    decoded = _decode_cursor(encoded)
    assert decoded is not None
    decoded_ts, decoded_id = decoded
    assert decoded_ts == ts
    assert decoded_id == aid


def test_cursor_round_trip_with_null_timestamp():
    """An agent with no audit history has last_invoked_at=None — the
    cursor must handle that without throwing."""
    aid = uuid.uuid4()
    encoded = _encode_cursor(None, aid)
    assert encoded.startswith("null|")
    decoded = _decode_cursor(encoded)
    assert decoded is not None
    decoded_ts, decoded_id = decoded
    assert decoded_ts is None
    assert decoded_id == aid


def test_cursor_decodes_garbage_to_none():
    """Tampered or malformed cursors return None so the caller starts
    from page 1 — better than 500-ing on a query parameter the client
    may have copied wrong."""
    for bad in ["", "not-iso|not-uuid", "garbage", "2026-05-03T12:00:00", "|", None]:
        assert _decode_cursor(bad) is None


def test_cursor_decodes_iso_with_timezone():
    """The encoded form uses isoformat() which produces timezone-aware
    strings for tz-aware datetimes. Decode must round-trip them."""
    ts = datetime(2026, 5, 3, 12, 0, 0, tzinfo=timezone.utc)
    encoded = _encode_cursor(ts, uuid.UUID("12345678-1234-5678-1234-567812345678"))
    decoded_ts, decoded_id = _decode_cursor(encoded)
    assert decoded_ts == ts
    assert str(decoded_id) == "12345678-1234-5678-1234-567812345678"
