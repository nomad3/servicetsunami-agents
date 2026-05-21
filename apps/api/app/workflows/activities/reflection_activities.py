"""Temporal activities for NightlyReflectionWorkflow (O2 scaffolding).

Phase 1 (this PR): stubs that return empty results. The workflow shape
is the load-bearing piece — synthesis bodies land in O2b/c follow-up
PRs once the kill-switch + scaffold are deployed.

Four activities matching the four dream mechanisms in canonical
design §3.2-§3.5:

  1. ``gather_episodes``          — pull the day's conversation_episodes
                                    for the tenant
  2. ``cluster_episodes``         — group them into hard-case clusters
                                    that feed counterfactual replay
  3. ``synthesize_reflections``   — run the per-mechanism syntheses
                                    (counterfactual / affect-recalibration
                                    / memory-consolidation / policy-
                                    rehearsal) and emit NightlyReflection
                                    payloads
  4. ``write_reflections``        — persist payloads via reflection_io

Each activity is a separate Temporal activity so the workflow can
checkpoint between them and retry the failing leg without re-running
the LLM-heavy synthesis step. Activities must be pure of the workflow
runtime — they open their own DB session via SessionLocal.
"""
from __future__ import annotations

import logging
import uuid
from typing import List, Optional

from temporalio import activity

log = logging.getLogger(__name__)


@activity.defn(name="reflection.gather_episodes")
async def gather_episodes(
    tenant_id: str,
    day: str,
) -> List[dict]:
    """Return the tenant's conversation_episodes for ``day`` (YYYY-MM-DD UTC).

    Phase 1 stub: returns []. The Phase 2 body will query
    conversation_episodes joined to chat_sessions by tenant_id +
    DATE(created_at)=day and emit a flat dict per row that the
    cluster + synthesis legs can consume without holding the DB
    session open across activity boundaries.
    """
    log.info("gather_episodes stub tenant=%s day=%s", tenant_id, day)
    return []


@activity.defn(name="reflection.cluster_episodes")
async def cluster_episodes(
    episodes: List[dict],
) -> List[dict]:
    """Cluster episodes into hard-case groups for counterfactual replay.

    Phase 1 stub: returns []. The Phase 2 body will cluster by
    (low predicted_confidence, high actual_reward) or (high predicted,
    low actual) pairs — the cases where the metacog signal disagreed
    with the realized outcome and there's something worth replaying.
    """
    log.info("cluster_episodes stub n=%s", len(episodes))
    return []


@activity.defn(name="reflection.synthesize_reflections")
async def synthesize_reflections(
    tenant_id: str,
    day: str,
    episodes: List[dict],
    clusters: List[dict],
) -> List[dict]:
    """Run the four dream-mechanism syntheses and return
    NightlyReflection payloads as plain dicts (Temporal-friendly).

    Phase 1 stub: returns []. Phase 2 will invoke:
      - counterfactual_replay(clusters)
      - affect_recalibration(episodes)
      - memory_consolidation(tenant_id, day)
      - policy_rehearsal(tenant_id)

    Each emits dicts shaped like ``NightlyReflection.to_dict()`` with
    ≥1 source_memory_id (canonical §3.6 citation discipline). The
    O3 validator (next PR) hard-rejects rows that don't satisfy this
    before write_reflections gets called.
    """
    log.info(
        "synthesize_reflections stub tenant=%s day=%s episodes=%s clusters=%s",
        tenant_id, day, len(episodes), len(clusters),
    )
    return []


@activity.defn(name="reflection.write_reflections")
async def write_reflections(
    tenant_id: str,
    reflections: List[dict],
) -> int:
    """Persist NightlyReflection payloads via reflection_io.

    Returns the number of reflections written. Phase 1 stub keeps the
    write path off the hot path — when ``reflections`` is non-empty
    we'll still call reflection_io to validate the dict shape, but
    the upstream stub returns [] today.
    """
    if not reflections:
        log.info("write_reflections stub tenant=%s n=0", tenant_id)
        return 0

    # Phase 2 will reconstruct NightlyReflection from each dict and
    # call reflection_io.write_reflection in a loop. Today, no caller
    # produces non-empty input so we never reach this branch — but
    # we keep the import lazy so the activity can be registered on a
    # worker that doesn't have the schemas loaded.
    from app.db.session import SessionLocal
    from app.schemas.reflection import NightlyReflection
    from app.services import reflection_io

    written = 0
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
                continue
            new_id: Optional[uuid.UUID] = reflection_io.write_reflection(
                db,
                reflection=reflection,
                current_tenant_id=uuid.UUID(tenant_id),
            )
            if new_id is not None:
                written += 1
    finally:
        db.close()

    log.info(
        "write_reflections wrote=%s/%s tenant=%s",
        written, len(reflections), tenant_id,
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
