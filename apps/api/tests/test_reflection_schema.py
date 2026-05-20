"""Unit tests for the NightlyReflection schema — O1 substrate.

Pure-dataclass tests — no DB. Locks the load-bearing invariants:
REFLECTION_KINDS membership, content non-empty and <= 500 chars,
source_memory_ids required and non-empty (the citation invariant),
confidence in [0, 1], to_dict roundtrip.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from app.schemas.reflection import (
    MAX_CONTENT_LEN,
    REFLECTION_KINDS,
    NightlyReflection,
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _canonical(**overrides) -> NightlyReflection:
    """Build a default-valid reflection; tests override one field."""
    base = dict(
        tenant_id="00000000-0000-0000-0000-000000000001",
        agent_id="00000000-0000-0000-0000-000000000002",
        day="2026-05-20",
        kind="next_move",
        content="Tomorrow: roll dreams-O2 workflow.",
        source_memory_ids=["mem-1"],
        confidence=0.7,
        ts=_now(),
    )
    base.update(overrides)
    return NightlyReflection(**base)


# ── Canonical shape ───────────────────────────────────────────────────


def test_accepts_canonical_shape():
    r = _canonical()
    assert r.kind in REFLECTION_KINDS
    assert r.confidence == 0.7
    assert len(r.source_memory_ids) == 1


def test_all_known_kinds_accepted():
    for kind in REFLECTION_KINDS:
        r = _canonical(kind=kind)
        assert r.kind == kind


# ── kind validation ───────────────────────────────────────────────────


def test_rejects_unknown_kind():
    with pytest.raises(ValueError, match="kind must be one of"):
        _canonical(kind="hallucination")


# ── content validation ────────────────────────────────────────────────


def test_rejects_empty_content():
    with pytest.raises(ValueError, match="content must be a non-empty string"):
        _canonical(content="")


def test_rejects_whitespace_only_content():
    with pytest.raises(ValueError, match="content must be a non-empty string"):
        _canonical(content="   \n  ")


def test_rejects_content_over_max_length():
    too_long = "x" * (MAX_CONTENT_LEN + 1)
    with pytest.raises(ValueError, match="content must be <="):
        _canonical(content=too_long)


def test_accepts_content_at_max_length():
    """Boundary — exactly MAX_CONTENT_LEN chars is allowed."""
    at_max = "x" * MAX_CONTENT_LEN
    r = _canonical(content=at_max)
    assert len(r.content) == MAX_CONTENT_LEN


# ── source_memory_ids — citation invariant ────────────────────────────


def test_rejects_empty_source_memory_ids():
    """Hard citation invariant — every reflection must cite >= 1
    source. This is the canonical-design §3.6 guarantee that
    reflections aren't hallucinations."""
    with pytest.raises(ValueError, match="source_memory_ids must be a non-empty list"):
        _canonical(source_memory_ids=[])


def test_rejects_non_list_source_memory_ids():
    with pytest.raises(ValueError, match="source_memory_ids must be a non-empty list"):
        # A bare string would otherwise pass the truthiness check while
        # being structurally wrong.
        _canonical(source_memory_ids="mem-1")  # type: ignore[arg-type]


def test_accepts_multiple_source_memory_ids():
    r = _canonical(source_memory_ids=["m-1", "m-2", "m-3"])
    assert r.source_memory_ids == ["m-1", "m-2", "m-3"]


# ── confidence validation ─────────────────────────────────────────────


def test_rejects_out_of_range_confidence():
    for bad in (-0.01, 1.01, 5.0, -1.0):
        with pytest.raises(ValueError, match="confidence must be in"):
            _canonical(confidence=bad)


def test_accepts_boundary_confidences():
    for ok in (0.0, 1.0):
        r = _canonical(confidence=ok)
        assert r.confidence == ok


# ── to_dict roundtrip ─────────────────────────────────────────────────


def test_to_dict_roundtrips_all_fields():
    r = _canonical(
        kind="risk",
        content="Embedding service hangs when HF_HUB_DISABLE_XET unset.",
        source_memory_ids=["mem-a", "mem-b"],
        confidence=0.42,
    )
    d = r.to_dict()
    assert d["kind"] == "risk"
    assert d["source_memory_ids"] == ["mem-a", "mem-b"]
    assert d["confidence"] == 0.42
    # Reconstruction must yield an identical object
    assert NightlyReflection(**d) == r


# ── frozen dataclass discipline ───────────────────────────────────────


def test_reflection_is_frozen():
    r = _canonical()
    with pytest.raises((AttributeError, Exception)):  # FrozenInstanceError
        r.confidence = 0.99  # type: ignore[misc]
