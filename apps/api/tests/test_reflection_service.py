"""Tests for the pure-function reflection service — O1 substrate.

Serialize / deserialize roundtrip + malformed-blob handling. No DB.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone

from app.schemas.reflection import NightlyReflection
from app.services.reflection import (
    REFLECTION_MEMORY_TYPE,
    deserialize_reflection,
    serialize_reflection,
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _canonical() -> NightlyReflection:
    return NightlyReflection(
        tenant_id="00000000-0000-0000-0000-000000000001",
        agent_id="00000000-0000-0000-0000-000000000002",
        day="2026-05-20",
        kind="idea",
        content="Pair offline-synthesis with daytime ECE so calibration drift hits the morning report.",
        source_memory_ids=["mem-1", "mem-2"],
        confidence=0.55,
        ts=_now(),
    )


# ── memory_type discriminator ─────────────────────────────────────────


def test_memory_type_constant_is_nightly_reflection():
    """The substrate join key — agents querying by memory_type must
    use this exact string. Locks the value against accidental rename."""
    assert REFLECTION_MEMORY_TYPE == "nightly_reflection"


# ── serialize ──────────────────────────────────────────────────────────


def test_serialize_produces_valid_json():
    r = _canonical()
    blob = serialize_reflection(r)
    decoded = json.loads(blob)
    assert decoded["kind"] == "idea"
    assert decoded["source_memory_ids"] == ["mem-1", "mem-2"]


def test_serialize_sorts_keys_for_stable_diffs():
    """sort_keys=True is the operator's friend — diffs of agent_memory
    rows in a DB dump should be deterministic, not field-order
    dependent."""
    r = _canonical()
    blob = serialize_reflection(r)
    # Re-encode with sort_keys and confirm equality.
    assert blob == json.dumps(json.loads(blob), sort_keys=True)


# ── deserialize ───────────────────────────────────────────────────────


def test_serialize_deserialize_roundtrip():
    r = _canonical()
    decoded = deserialize_reflection(serialize_reflection(r))
    assert decoded == r


def test_deserialize_returns_none_on_invalid_json():
    assert deserialize_reflection("not json {{{") is None


def test_deserialize_returns_none_on_missing_required_field():
    blob = json.dumps({"tenant_id": "t", "agent_id": "a"})  # missing fields
    assert deserialize_reflection(blob) is None


def test_deserialize_returns_none_on_invariant_breach_unknown_kind():
    """Schema validation runs in __post_init__ — a blob with a bogus
    kind should round-trip back to None, NOT raise."""
    r = _canonical()
    payload = r.to_dict()
    payload["kind"] = "telekinesis"
    blob = json.dumps(payload)
    assert deserialize_reflection(blob) is None


def test_deserialize_returns_none_on_empty_source_memory_ids():
    """Citation invariant is enforced even on read — a corrupted row
    in the DB with no sources should be silently dropped, not crash
    the consumer."""
    r = _canonical()
    payload = r.to_dict()
    payload["source_memory_ids"] = []
    blob = json.dumps(payload)
    assert deserialize_reflection(blob) is None


def test_deserialize_returns_none_on_oversize_content():
    r = _canonical()
    payload = r.to_dict()
    payload["content"] = "x" * 5000
    blob = json.dumps(payload)
    assert deserialize_reflection(blob) is None
