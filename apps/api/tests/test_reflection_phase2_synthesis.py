"""Tests for Phase 2 bodies of NightlyReflectionWorkflow activities.

Pure-function helpers (cluster_episodes, _episode_entity_set,
_consolidation_summary, _classify_kind, _truncate_content) get
synchronous unit tests. The DB-touching paths (gather_episodes,
_fetch_citation_memories, synthesize_reflections end-to-end) get
covered through the integration job — we don't duplicate the
SQLAlchemy shim fight here.

Locked properties:
  - Shared-entity clustering builds connected components correctly
  - Mixed-shape key_entities (strings + dicts) both parse
  - Outcome-divergent clusters get classified 'tension'
  - Long synthesis content truncates at a word boundary, not mid-token
  - Empty input produces empty output (no crashes)
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.workflows.activities import reflection_activities as ra


# ── _episode_entity_set ────────────────────────────────────────────────


def test_entity_set_handles_string_list():
    ep = {"key_entities": ["Acme Corp", "Project Pegasus", ""]}
    out = ra._episode_entity_set(ep)
    assert out == {"acme corp", "project pegasus"}


def test_entity_set_handles_dict_list():
    ep = {"key_entities": [
        {"name": "Acme Corp", "type": "org"},
        {"name": "DEFCON", "type": "event"},
    ]}
    out = ra._episode_entity_set(ep)
    assert "acme corp" in out
    assert "defcon" in out


def test_entity_set_handles_mixed_list():
    """Real-world: extractor changed from list-of-strings to
    list-of-dicts mid-rollout — must handle both."""
    ep = {"key_entities": ["Acme Corp", {"name": "DEFCON"}, None, 42]}
    out = ra._episode_entity_set(ep)
    assert out == {"acme corp", "defcon"}


def test_entity_set_empty_returns_empty_set():
    assert ra._episode_entity_set({}) == set()
    assert ra._episode_entity_set({"key_entities": None}) == set()


# ── _truncate_content ──────────────────────────────────────────────────


def test_truncate_short_text_unchanged():
    s = "Short summary."
    assert ra._truncate_content(s) == s


def test_truncate_long_text_breaks_at_word_boundary():
    s = " ".join(["word"] * 200)  # plenty above 500 chars
    out = ra._truncate_content(s)
    assert len(out) <= ra._MAX_CONTENT_CHARS
    assert out.endswith("…")
    # Final word before the ellipsis must be intact (not "wo…")
    body = out[:-1].rstrip()
    assert body.split(" ")[-1] == "word"


# ── _classify_kind ─────────────────────────────────────────────────────


def test_classify_kind_default_is_idea():
    eps = [{"outcome": ""}, {"outcome": ""}]
    assert ra._classify_kind(eps) == "idea"


def test_classify_kind_returns_tension_on_outcome_divergence():
    eps = [{"outcome": "success"}, {"outcome": "failed"}]
    assert ra._classify_kind(eps) == "tension"


def test_classify_kind_uniform_success_is_idea_not_tension():
    eps = [{"outcome": "success"}, {"outcome": "resolved"}]
    assert ra._classify_kind(eps) == "idea"


def test_classify_kind_uniform_failure_is_idea_not_tension():
    eps = [{"outcome": "failed"}, {"outcome": "blocked"}]
    assert ra._classify_kind(eps) == "idea"


# ── _consolidation_summary ────────────────────────────────────────────


def test_consolidation_summary_includes_entity_and_count():
    eps = [
        {"summary": "Discussed Acme integration.", "mood": "calm"},
        {"summary": "Acme rollout meeting.", "mood": "focused"},
    ]
    out = ra._consolidation_summary("acme corp", eps)
    assert "acme corp" in out
    assert "2 episodes" in out


def test_consolidation_summary_obeys_max_content_chars():
    eps = [
        {"summary": "x" * 600, "mood": "long"},
        {"summary": "y" * 600, "mood": "longer"},
    ] * 10
    out = ra._consolidation_summary("entity", eps)
    assert len(out) <= ra._MAX_CONTENT_CHARS


# ── cluster_episodes ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_cluster_episodes_groups_shared_entity():
    episodes = [
        {"id": "ep-1", "key_entities": ["Acme Corp", "Q3 plan"]},
        {"id": "ep-2", "key_entities": ["Acme Corp", "headcount"]},
        {"id": "ep-3", "key_entities": ["unrelated thing"]},
    ]
    clusters = await ra.cluster_episodes(episodes)
    # acme corp appears in ep-1 + ep-2 → one cluster
    acme = [c for c in clusters if c["shared_entity"] == "acme corp"]
    assert len(acme) == 1
    assert acme[0]["episode_ids"] == ["ep-1", "ep-2"]


@pytest.mark.asyncio
async def test_cluster_episodes_dedupes_identical_member_sets():
    """If episodes share two entities, the cluster should appear once
    (under one of the entity names), not twice."""
    episodes = [
        {"id": "ep-1", "key_entities": ["Acme", "Pegasus"]},
        {"id": "ep-2", "key_entities": ["Acme", "Pegasus"]},
    ]
    clusters = await ra.cluster_episodes(episodes)
    member_sets = [tuple(c["episode_ids"]) for c in clusters]
    assert len(member_sets) == 1  # not 2


@pytest.mark.asyncio
async def test_cluster_episodes_drops_singleton_entities():
    """An entity that appears in only one episode is not a cluster."""
    episodes = [
        {"id": "ep-1", "key_entities": ["Acme Corp"]},
        {"id": "ep-2", "key_entities": ["unrelated"]},
    ]
    clusters = await ra.cluster_episodes(episodes)
    assert clusters == []


@pytest.mark.asyncio
async def test_cluster_episodes_empty_input_empty_output():
    assert await ra.cluster_episodes([]) == []


# ── synthesize_reflections ─────────────────────────────────────────────


@pytest.mark.asyncio
async def test_synthesize_reflections_empty_clusters_returns_empty():
    """No clusters → no payloads. Does NOT open a DB session."""
    out = await ra.synthesize_reflections(
        tenant_id="t-1", day="2026-05-20", episodes=[], clusters=[],
    )
    assert out == []


@pytest.mark.asyncio
async def test_synthesize_reflections_skips_cluster_without_citation(
    monkeypatch,
):
    """When no agent_memory mentions the cluster entity, the
    reflection would fail O3 citation validation — skip cleanly
    rather than ship a payload destined for rejection."""
    fake_session = MagicMock()
    fake_session.__enter__ = lambda s: s
    fake_session.__exit__ = lambda *a: None
    monkeypatch.setattr(
        ra, "SessionLocal", lambda: fake_session, raising=False,
    )
    # Force the lazy import to use our mock too
    import sys
    fake_module = MagicMock()
    fake_module.SessionLocal = lambda: fake_session
    monkeypatch.setitem(sys.modules, "app.db.session", fake_module)

    monkeypatch.setattr(
        ra, "_resolve_synthesis_agent_id", lambda db, tid: "agent-1",
    )
    monkeypatch.setattr(
        ra, "_fetch_citation_memories",
        lambda db, tid, entity, limit=5: [],  # no citations
    )

    episodes = [
        {"id": "ep-1", "summary": "x", "key_entities": ["acme"]},
        {"id": "ep-2", "summary": "y", "key_entities": ["acme"]},
    ]
    clusters = [{"shared_entity": "acme", "episode_ids": ["ep-1", "ep-2"]}]
    out = await ra.synthesize_reflections(
        tenant_id="00000000-0000-0000-0000-000000000001",
        day="2026-05-20",
        episodes=episodes,
        clusters=clusters,
    )
    assert out == []


@pytest.mark.asyncio
async def test_synthesize_reflections_skips_when_no_agent_to_anchor(
    monkeypatch,
):
    """Tenant has no agents → can't anchor reflections → return [].
    Graceful: don't crash, don't make up an agent_id."""
    fake_session = MagicMock()
    monkeypatch.setattr(
        ra, "SessionLocal", lambda: fake_session, raising=False,
    )
    import sys
    fake_module = MagicMock()
    fake_module.SessionLocal = lambda: fake_session
    monkeypatch.setitem(sys.modules, "app.db.session", fake_module)

    monkeypatch.setattr(
        ra, "_resolve_synthesis_agent_id", lambda db, tid: None,
    )

    episodes = [{"id": "ep-1", "key_entities": ["acme"]}]
    clusters = [{"shared_entity": "acme", "episode_ids": ["ep-1"]}]
    out = await ra.synthesize_reflections(
        tenant_id="00000000-0000-0000-0000-000000000002",
        day="2026-05-20",
        episodes=episodes,
        clusters=clusters,
    )
    assert out == []


@pytest.mark.asyncio
async def test_synthesize_reflections_builds_valid_payload(monkeypatch):
    """End-to-end happy path with mocked DB. Payload must be the
    exact shape NightlyReflection accepts (so write_reflections
    won't reject on construction)."""
    fake_session = MagicMock()
    monkeypatch.setattr(
        ra, "SessionLocal", lambda: fake_session, raising=False,
    )
    import sys
    fake_module = MagicMock()
    fake_module.SessionLocal = lambda: fake_session
    monkeypatch.setitem(sys.modules, "app.db.session", fake_module)

    monkeypatch.setattr(
        ra, "_resolve_synthesis_agent_id",
        lambda db, tid: "11111111-1111-1111-1111-111111111111",
    )
    monkeypatch.setattr(
        ra, "_fetch_citation_memories",
        lambda db, tid, entity, limit=5: [
            "22222222-2222-2222-2222-222222222222",
            "33333333-3333-3333-3333-333333333333",
        ],
    )

    episodes = [
        {"id": "ep-1", "summary": "Discussed Acme Q3.", "mood": "focused",
         "outcome": "resolved", "key_entities": ["acme"]},
        {"id": "ep-2", "summary": "Acme rollout call.", "mood": "calm",
         "outcome": "completed", "key_entities": ["acme"]},
    ]
    clusters = [{"shared_entity": "acme", "episode_ids": ["ep-1", "ep-2"]}]
    payloads = await ra.synthesize_reflections(
        tenant_id="00000000-0000-0000-0000-000000000003",
        day="2026-05-20",
        episodes=episodes,
        clusters=clusters,
    )
    assert len(payloads) == 1
    p = payloads[0]
    # Payload conforms to NightlyReflection — try constructing it
    from app.schemas.reflection import NightlyReflection
    refl = NightlyReflection(**p)
    assert refl.kind == "idea"  # both outcomes positive → idea
    assert "acme" in refl.content
    assert len(refl.source_memory_ids) == 2
    assert refl.day == "2026-05-20"
