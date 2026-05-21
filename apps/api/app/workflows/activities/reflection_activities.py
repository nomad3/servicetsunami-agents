"""Temporal activities for NightlyReflectionWorkflow.

Phase 2 (this PR): real bodies for gather_episodes + cluster_episodes
+ synthesize_reflections (memory-consolidation mechanism). The other
three mechanisms (counterfactual-replay, affect-recalibration,
policy-rehearsal) remain TODO follow-ups — they're individually
shippable on top of this skeleton.

Four activities matching the four dream mechanisms in canonical
design §3.2-§3.5:

  1. ``gather_episodes``          — pull the day's conversation_episodes
                                    for the tenant. PHASE 2 BODY.
  2. ``cluster_episodes``         — group them into shared-entity
                                    clusters. PHASE 2 BODY (simple
                                    overlap-based grouping).
  3. ``synthesize_reflections``   — emit memory-consolidation reflections
                                    citing tenant agent_memory rows.
                                    PHASE 2 BODY (one mechanism shipped;
                                    three TODO).
  4. ``write_reflections``        — persist via reflection_io, gated
                                    by the O3 validator chain.

Each activity is a separate Temporal activity so the workflow can
checkpoint between them and retry the failing leg without re-running
the LLM-heavy synthesis step. Activities must be pure of the workflow
runtime — they open their own DB session via SessionLocal.
"""
from __future__ import annotations

import logging
import uuid
from collections import defaultdict
from datetime import datetime, timezone
from typing import List, Optional

from temporalio import activity

log = logging.getLogger(__name__)


# Locked sample caps. Synthesis cost grows with episode count;
# capping at the source keeps the nightly run bounded.
_MAX_EPISODES_PER_RUN = 200
_MAX_REFLECTIONS_PER_RUN = 50
_MAX_CONTENT_CHARS = 500  # matches NightlyReflection.MAX_CONTENT_LEN


def _to_iso(dt) -> str:
    if dt is None:
        return ""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).isoformat()


@activity.defn(name="reflection.gather_episodes")
async def gather_episodes(
    tenant_id: str,
    day: str,
) -> List[dict]:
    """Return the tenant's conversation_episodes for ``day`` (YYYY-MM-DD UTC).

    Phase 2 body: SELECT against conversation_episodes filtered by
    tenant + day. Capped at ``_MAX_EPISODES_PER_RUN`` so a tenant
    with thousands of episodes doesn't drown the synthesis loop.
    """
    from sqlalchemy import text as sql_text

    from app.db.session import SessionLocal

    db = SessionLocal()
    try:
        rows = db.execute(
            sql_text(
                """
                SELECT
                    id, summary, key_entities, key_topics, mood,
                    outcome, affect_vector, agent_slug, source_channel,
                    created_at
                FROM conversation_episodes
                WHERE tenant_id = :tid
                  AND DATE(created_at) = DATE(:day)
                ORDER BY created_at DESC
                LIMIT :lim
                """
            ),
            {
                "tid": tenant_id,
                "day": day,
                "lim": _MAX_EPISODES_PER_RUN,
            },
        ).all()
    finally:
        db.close()

    out: List[dict] = []
    for r in rows:
        out.append({
            "id": str(r.id),
            "summary": r.summary or "",
            "key_entities": r.key_entities or [],
            "key_topics": r.key_topics or [],
            "mood": r.mood or "",
            "outcome": r.outcome or "",
            "affect_vector": r.affect_vector or {},
            "agent_slug": r.agent_slug or "",
            "source_channel": r.source_channel or "",
            "created_at": _to_iso(r.created_at),
        })
    log.info(
        "gather_episodes tenant=%s day=%s n=%s", tenant_id, day, len(out),
    )
    return out


def _episode_entity_set(ep: dict) -> set:
    """Normalize an episode's entity tokens to a comparable lowercase
    set. ``key_entities`` can be a list of strings OR a list of
    {name, type, ...} dicts depending on the upstream extractor —
    handle both shapes."""
    raw = ep.get("key_entities") or []
    out: set = set()
    for item in raw:
        if isinstance(item, str):
            out.add(item.strip().lower())
        elif isinstance(item, dict):
            name = item.get("name") or item.get("entity")
            if isinstance(name, str):
                out.add(name.strip().lower())
    out.discard("")
    return out


@activity.defn(name="reflection.cluster_episodes")
async def cluster_episodes(
    episodes: List[dict],
) -> List[dict]:
    """Cluster episodes by shared key_entities — connected components.

    Phase 2 body (simple overlap-based grouping):
      - Build an entity -> [episode_ids] inverted index.
      - For each entity that appears in ≥2 episodes, emit one cluster
        {shared_entity, episode_ids}.
      - Dedupe clusters that have identical episode-id sets so one
        thread mentioning two entities doesn't surface twice.

    Phase 3 may replace with embedding-cosine k-means or
    metacognition-signal-aware clustering (low-confidence/high-reward
    pairs per §3.2). For memory-consolidation today, shared-entity
    clusters are exactly the right shape.
    """
    inverted: dict[str, list[str]] = defaultdict(list)
    for ep in episodes:
        ep_id = ep.get("id")
        if not ep_id:
            continue
        for entity in _episode_entity_set(ep):
            inverted[entity].append(ep_id)

    clusters: list[dict] = []
    seen_sets: set[frozenset] = set()
    for entity, ep_ids in inverted.items():
        if len(ep_ids) < 2:
            continue
        sig = frozenset(ep_ids)
        if sig in seen_sets:
            continue
        seen_sets.add(sig)
        clusters.append({
            "shared_entity": entity,
            "episode_ids": sorted(ep_ids),
        })

    log.info(
        "cluster_episodes in=%s clusters=%s",
        len(episodes), len(clusters),
    )
    return clusters


def _fetch_citation_memories(
    db, tenant_id: str, entity: str, limit: int = 5,
) -> List[str]:
    """Find ``agent_memory`` rows in the tenant that mention ``entity``
    in their content. Returns up to ``limit`` IDs as strings — these
    become ``source_memory_ids`` on the synthesized reflection. The
    O3 ``validate_citation`` check confirms each ID exists in
    agent_memories for the tenant, so we MUST return real IDs only.

    Substring match on lowercased content is the simplest path that
    works without an embedding lookup; it's enough for Phase 2
    memory-consolidation (the entity is already a string token that
    appeared in episode metadata, so it's likely in some agent_memory
    content too)."""
    from sqlalchemy import text as sql_text

    rows = db.execute(
        sql_text(
            """
            SELECT id::text
            FROM agent_memories
            WHERE tenant_id = :tid
              AND content ILIKE :pattern
            ORDER BY created_at DESC
            LIMIT :lim
            """
        ),
        {
            "tid": tenant_id,
            "pattern": f"%{entity}%",
            "lim": limit,
        },
    ).all()
    return [r[0] for r in rows]


def _resolve_synthesis_agent_id(db, tenant_id: str) -> Optional[str]:
    """Anchor reflections on the tenant's Luna persona agent. Falls
    back to any other agent if no Luna-named one exists — same
    graceful degradation as habits.py."""
    from app.models.agent import Agent

    luna = (
        db.query(Agent)
        .filter(
            Agent.tenant_id == uuid.UUID(tenant_id),
            Agent.name.ilike("%luna%"),
        )
        .first()
    )
    if luna is None:
        luna = (
            db.query(Agent)
            .filter(Agent.tenant_id == uuid.UUID(tenant_id))
            .first()
        )
    return str(luna.id) if luna is not None else None


def _truncate_content(text: str) -> str:
    """Trim to <= 500 chars at a word boundary so NightlyReflection
    construction doesn't raise on long synthesis output."""
    if len(text) <= _MAX_CONTENT_CHARS:
        return text
    head = text[: _MAX_CONTENT_CHARS - 1]
    space = head.rfind(" ")
    if space > _MAX_CONTENT_CHARS // 2:
        head = head[:space]
    return head + "…"


def _consolidation_summary(entity: str, episodes_in_cluster: List[dict]) -> str:
    """One-paragraph 'multiple conversations touched on X' summary.

    Trims to 500 chars via _truncate_content. Phase 3 may swap the
    join-summary heuristic for an LLM rewrite."""
    n = len(episodes_in_cluster)
    moods = sorted({e.get("mood", "") for e in episodes_in_cluster if e.get("mood")})
    outcomes = sorted({e.get("outcome", "") for e in episodes_in_cluster if e.get("outcome")})

    parts = [
        f"Multiple recent conversations touched on '{entity}' "
        f"({n} episodes).",
    ]
    if moods:
        parts.append(f"Moods observed: {', '.join(moods[:5])}.")
    if outcomes:
        parts.append(f"Outcomes: {', '.join(outcomes[:5])}.")

    snippets: list[str] = []
    for ep in episodes_in_cluster[:3]:
        s = (ep.get("summary") or "").strip()
        if s:
            first_sentence = s.split(".")[0].strip()
            if first_sentence:
                snippets.append(first_sentence + ".")
    if snippets:
        parts.append("Highlights: " + " ".join(snippets))

    return _truncate_content(" ".join(parts))


def _classify_kind(episodes_in_cluster: List[dict]) -> str:
    """Pick a NightlyReflection.kind for a memory-consolidation cluster.

    Heuristic:
      - 'tension' when outcomes diverge across episodes (some
        positive, some negative) — those are the conversations
        worth re-litigating.
      - 'idea' as the default — the cluster is a thread worth
        revisiting.
    """
    outcomes = [e.get("outcome", "").lower() for e in episodes_in_cluster]
    has_pos = any(o in ("success", "resolved", "completed") for o in outcomes)
    has_neg = any(o in ("failed", "error", "blocked", "abandoned") for o in outcomes)
    if has_pos and has_neg:
        return "tension"
    return "idea"


@activity.defn(name="reflection.synthesize_reflections")
async def synthesize_reflections(
    tenant_id: str,
    day: str,
    episodes: List[dict],
    clusters: List[dict],
) -> List[dict]:
    """Run the dream-mechanism syntheses and return NightlyReflection
    payloads as plain dicts.

    Phase 2 body: memory-consolidation mechanism only. For each
    shared-entity cluster, find agent_memory rows mentioning the
    entity (citation chain), build a consolidation summary, classify
    the kind (idea vs tension), and emit the payload. Capped at
    ``_MAX_REFLECTIONS_PER_RUN``.

    TODO Phase 2b: counterfactual_replay(clusters), affect_recalibration(
    episodes), policy_rehearsal(tenant_id). Each is a separate
    function call in this activity; the workflow shape doesn't change.

    Each payload is shaped like ``NightlyReflection.to_dict()`` and
    passes through the O3 validator chain in ``write_reflections``
    before any DB write — so a synthesis bug here can't corrupt
    agent_memory, it just fails validation and gets logged.
    """
    if not clusters:
        log.info(
            "synthesize_reflections tenant=%s day=%s: no clusters, no output",
            tenant_id, day,
        )
        return []

    from app.db.session import SessionLocal

    episodes_by_id = {ep.get("id"): ep for ep in episodes if ep.get("id")}
    ts = datetime.now(timezone.utc).isoformat()
    payloads: List[dict] = []

    db = SessionLocal()
    try:
        agent_id = _resolve_synthesis_agent_id(db, tenant_id)
        if agent_id is None:
            log.warning(
                "synthesize_reflections tenant=%s: no agent to anchor "
                "reflections; skipping",
                tenant_id,
            )
            return []

        for cluster in clusters:
            if len(payloads) >= _MAX_REFLECTIONS_PER_RUN:
                break
            entity = cluster.get("shared_entity", "")
            ep_ids = cluster.get("episode_ids", [])
            episodes_in_cluster = [
                episodes_by_id[eid] for eid in ep_ids
                if eid in episodes_by_id
            ]
            if len(episodes_in_cluster) < 2:
                continue

            citation_ids = _fetch_citation_memories(db, tenant_id, entity)
            if not citation_ids:
                # No agent_memory rows mention this entity — the O3
                # citation validator would reject anyway. Skip cleanly.
                log.info(
                    "synthesize_reflections: no citation memories for "
                    "entity=%r tenant=%s; skipping cluster",
                    entity, tenant_id,
                )
                continue

            payload = {
                "tenant_id": tenant_id,
                "agent_id": agent_id,
                "day": day,
                "kind": _classify_kind(episodes_in_cluster),
                "content": _consolidation_summary(entity, episodes_in_cluster),
                "source_memory_ids": citation_ids,
                "confidence": 0.5,
                "ts": ts,
            }
            payloads.append(payload)
    finally:
        db.close()

    log.info(
        "synthesize_reflections tenant=%s day=%s clusters=%s "
        "reflections_emitted=%s mechanism=memory_consolidation",
        tenant_id, day, len(clusters), len(payloads),
    )
    return payloads


@activity.defn(name="reflection.write_reflections")
async def write_reflections(
    tenant_id: str,
    reflections: List[dict],
) -> int:
    """Persist NightlyReflection payloads via reflection_io, gated by
    the O3 validator chain. Returns count of writes that survived
    validation + persistence."""
    if not reflections:
        log.info("write_reflections tenant=%s n=0", tenant_id)
        return 0

    from app.db.session import SessionLocal
    from app.models.agent_memory import AgentMemory
    from app.schemas.reflection import NightlyReflection
    from app.services import reflection_io
    from app.services.reflection_validators import validate_reflection

    tenant_uuid = uuid.UUID(tenant_id)
    written = 0
    rejected = 0
    db = SessionLocal()
    try:
        for payload in reflections:
            try:
                reflection = NightlyReflection(**payload)
            except (TypeError, ValueError) as exc:
                log.warning(
                    "write_reflections: malformed payload, skipping. "
                    "tenant=%s err=%s",
                    tenant_id, exc,
                )
                rejected += 1
                continue

            source_contents: list[str] = []
            try:
                cited_uuids = [uuid.UUID(s) for s in reflection.source_memory_ids]
                source_rows = (
                    db.query(AgentMemory.content)
                    .filter(
                        AgentMemory.tenant_id == str(tenant_uuid),
                        AgentMemory.id.in_([str(u) for u in cited_uuids]),
                    )
                    .all()
                )
                source_contents = [str(r[0]) for r in source_rows if r[0]]
            except Exception as exc:  # noqa: BLE001
                log.warning(
                    "write_reflections: failed to fetch source memories "
                    "for grounding check (rejecting reflection). tenant=%s err=%s",
                    tenant_id, exc,
                )
                rejected += 1
                continue

            verdict = validate_reflection(
                reflection,
                db=db,
                current_tenant_id=tenant_uuid,
                source_memory_contents=source_contents,
            )
            if not verdict.ok:
                log.warning(
                    "write_reflections: validator rejected reflection. "
                    "tenant=%s kind=%s reason=%s",
                    tenant_id, reflection.kind, verdict.reason,
                )
                rejected += 1
                continue

            new_id: Optional[uuid.UUID] = reflection_io.write_reflection(
                db,
                reflection=reflection,
                current_tenant_id=tenant_uuid,
            )
            if new_id is not None:
                written += 1
            else:
                rejected += 1
    finally:
        db.close()

    log.info(
        "write_reflections tenant=%s wrote=%s rejected=%s of total=%s",
        tenant_id, written, rejected, len(reflections),
    )
    return written


@activity.defn(name="reflection.check_killswitch")
async def check_killswitch(tenant_id: str) -> bool:
    """Read the per-tenant kill-switch outside the workflow's main
    thread. Workflow code can't open DB sessions; activities can.
    Returns ``True`` when synthesis is allowed."""
    from app.db.session import SessionLocal
    from app.services.reflection_killswitch import (
        is_nightly_reflection_enabled,
    )

    db = SessionLocal()
    try:
        return is_nightly_reflection_enabled(db, tenant_id)
    finally:
        db.close()


__all__ = [
    "gather_episodes",
    "cluster_episodes",
    "synthesize_reflections",
    "write_reflections",
    "check_killswitch",
]
