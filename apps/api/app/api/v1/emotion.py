"""Affect observability endpoints — Phase 1 PR B + Phase 2.

Read-only endpoints that surface affect state per session AND per
agent. Tenant-isolated: 404 on foreign-tenant access (the design doc's
safety pattern — see § Risks § "Emotion-state pollution across tenants").

Wired into the API router with empty prefix so the paths read
naturally:
  - GET /api/v1/sessions/{session_id}/affect-trace  (Phase 1)
  - GET /api/v1/affect/agents/{agent_id}            (Phase 2, this commit)

Per-agent path lives under /affect/agents/... rather than /agents/...
because agents.router is mounted with a catch-all GET /{agent_id} that
would consume any /agents/<uuid>/* sub-path before this router gets a
chance. The /affect/ prefix keeps the resolution clean.
"""
from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, get_db
from app.models.agent import Agent
from app.models.user import User
from app.schemas.emotion import PADVector
from app.services.emotion_engine_io import (
    get_affect_baseline,
    get_affect_trace,
    get_latest_session_affect,
    session_belongs_to_tenant,
)


router = APIRouter()


@router.get("/sessions/{session_id}/affect-trace")
def get_session_affect_trace(
    session_id: uuid.UUID,
    limit: int = Query(default=100, ge=1, le=500),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Return the PAD vector trajectory for a chat session.

    Foreign-tenant access returns 404 (not 403) — same convention as
    memories.py to avoid revealing whether a session exists.

    Response shape:
        {
            "session_id": "<uuid>",
            "trace": [
                {
                    "episode_id": "<uuid>",
                    "created_at": "<iso8601>",
                    "affect_vector": {"pleasure": .., "arousal": .., "dominance": .., "label": .., "updated_at": ..} | null,
                    "mood": "<string>" | null
                },
                ...
            ]
        }
    """
    if not session_belongs_to_tenant(
        db,
        session_id=session_id,
        tenant_id=current_user.tenant_id,
    ):
        raise HTTPException(status_code=404, detail="session not found")

    trace = get_affect_trace(
        db,
        session_id=session_id,
        tenant_id=current_user.tenant_id,
        limit=limit,
    )

    return {
        "session_id": str(session_id),
        "trace": trace,
    }


@router.get("/affect/agents/{agent_id}")
def get_agent_affect(
    agent_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Per-agent affect view — Phase 2 of the emotions engine.

    Returns:
      - The agent's stable `affect_baseline` (or `neutral` if not seeded).
      - A snapshot of the most recent live `affect_vector` across the
        agent's sessions in this tenant (or null if none recorded yet).
      - The agent's display name + id (so the dashboard can render
        without a separate /agents lookup).

    Tenant isolation: foreign-tenant access returns 404. Same pattern
    as the existing /sessions/{id}/affect-trace endpoint.

    Response shape:
        {
            "agent_id": "<uuid>",
            "agent_name": "<string>",
            "baseline": {"pleasure":..,"arousal":..,"dominance":..,"label":..,"updated_at":..},
            "current": {same shape} | null,
            "has_live_state": <bool>
        }
    """
    # Tenant-scope guard: load the agent and verify ownership.
    agent = db.query(Agent).filter(Agent.id == agent_id).first()
    if agent is None or str(agent.tenant_id) != str(current_user.tenant_id):
        # Foreign tenant or non-existent agent → 404 (same convention).
        raise HTTPException(status_code=404, detail="agent not found")

    baseline = get_affect_baseline(
        db, agent_id=agent_id, tenant_id=current_user.tenant_id,
    )

    # Phase 2 caveat: "current affect" today is the most recent
    # non-null affect_vector across ANY session for this tenant.
    # Per-agent attribution is a Phase 3 TODO (see the docstring on
    # record_session_tool_failure). For now, surface what we have +
    # set `has_live_state=False` when no episode has affect_vector
    # yet so consumers can degrade gracefully.
    current: PADVector | None = _latest_affect_for_agent_tenant(
        db, agent_id=agent_id, tenant_id=current_user.tenant_id,
    )

    return {
        "agent_id": str(agent_id),
        "agent_name": agent.name,
        "baseline": baseline.to_dict(),
        "current": current.to_dict() if current else None,
        "has_live_state": current is not None,
    }


def _latest_affect_for_agent_tenant(
    db: Session,
    *,
    agent_id: uuid.UUID,
    tenant_id: uuid.UUID,
) -> PADVector | None:
    """Return the agent's 'current affect' by reading the most recent
    non-null affect_vector across conversation_episodes whose session
    belongs to this agent.

    2026-05-19 Luna review fix: previously this ignored agent_id and
    returned the tenant-wide latest affect, so every per-agent endpoint
    call returned the same value within a tenant. Now joins through
    ChatSession.agent_id; falls back to tenant-wide latest only when
    that agent has no affect-bearing episodes yet (to preserve the
    'has_live_state' soft-degradation contract).

    Best-effort: tenant-scoped, returns None on read failure or when no
    episode has affect_vector yet.
    """
    from app.models.chat import ChatSession
    from app.models.conversation_episode import ConversationEpisode

    try:
        episode = (
            db.query(ConversationEpisode)
            .join(
                ChatSession,
                ConversationEpisode.session_id == ChatSession.id,
            )
            .filter(
                ConversationEpisode.tenant_id == tenant_id,
                ChatSession.agent_id == agent_id,
                ConversationEpisode.affect_vector.isnot(None),
            )
            .order_by(ConversationEpisode.created_at.desc())
            .first()
        )
    except Exception:  # noqa: BLE001
        return None
    if episode is None or episode.affect_vector is None:
        return None
    return PADVector.from_dict(episode.affect_vector)
