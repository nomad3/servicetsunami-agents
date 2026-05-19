"""I/O layer for the Emotion Engine.

Phase 1 PR B — bridges the pure-functional `emotion_engine` service to
the database. Pure-functional appraisal lives in `emotion_engine.py`;
this module wraps the JSONB read/write to `conversation_episodes` and
`agent_memories`.

Kept separate from `emotion_engine.py` to keep the appraisal math
testable without a DB.

Best-effort writes: write paths swallow SQLAlchemy errors and roll
back. The emotion layer must NEVER crash the RL/chat hot path; a stale
or missing affect_vector degrades to neutral, which is acceptable.
"""
from __future__ import annotations

import logging
import uuid
from typing import Optional

from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.models.agent_memory import AgentMemory
from app.models.conversation_episode import ConversationEpisode
from app.schemas.emotion import PADVector
from app.services.emotion_engine import (
    appraise_event,
    format_affect_addendum,
)

logger = logging.getLogger(__name__)


# ── Conversation episode ──────────────────────────────────────────────


def record_affect_on_episode(
    db: Session,
    *,
    episode_id: uuid.UUID,
    tenant_id: uuid.UUID,
    vector: PADVector,
) -> Optional[ConversationEpisode]:
    """Persist a PAD vector onto a conversation_episode row.

    Tenant-scoped: returns None (no-op) if the episode doesn't exist or
    belongs to another tenant. Mirrors the safety pattern used in
    memories.py.
    """
    episode = (
        db.query(ConversationEpisode)
        .filter(
            ConversationEpisode.id == episode_id,
            ConversationEpisode.tenant_id == tenant_id,
        )
        .first()
    )
    if episode is None:
        return None
    episode.affect_vector = vector.to_dict()
    try:
        db.commit()
        db.refresh(episode)
    except SQLAlchemyError as exc:
        logger.warning(
            "emotion_engine_io.record_affect_on_episode: commit failed, "
            "rolling back. episode_id=%s tenant_id=%s err=%s",
            episode_id, tenant_id, exc,
        )
        db.rollback()
        return None
    return episode


def get_affect_trace(
    db: Session,
    *,
    session_id: uuid.UUID,
    tenant_id: uuid.UUID,
    limit: int = 100,
) -> list[dict]:
    """Return the chronological PAD trajectory for a session.

    Tenant-scoped: only returns episodes whose `tenant_id` matches the
    caller. Foreign-tenant queries return [] (the endpoint translates to
    404).

    Each element: {"episode_id": str, "created_at": iso, "affect_vector": {...} | None,
    "mood": str | None}. Episodes WITHOUT an affect_vector are still
    included so the trace shows gaps — the consumer decides whether to
    interpolate or ignore.
    """
    episodes = (
        db.query(ConversationEpisode)
        .filter(
            ConversationEpisode.session_id == session_id,
            ConversationEpisode.tenant_id == tenant_id,
        )
        .order_by(ConversationEpisode.created_at.asc())
        .limit(limit)
        .all()
    )
    return [
        {
            "episode_id": str(ep.id),
            "created_at": ep.created_at.isoformat() if ep.created_at else None,
            "affect_vector": ep.affect_vector,
            "mood": ep.mood,
        }
        for ep in episodes
    ]


def session_belongs_to_tenant(
    db: Session,
    *,
    session_id: uuid.UUID,
    tenant_id: uuid.UUID,
) -> bool:
    """Did the given session ever produce an episode for this tenant?

    We don't query `chat_sessions` directly because the FK is `SET NULL
    on delete` — a session might be deleted but its episodes preserved.
    The episodes are the authoritative tenant-scope marker.

    Returns True if AT LEAST ONE episode for this session belongs to the
    tenant. False otherwise (including: session doesn't exist, session
    exists but for another tenant).
    """
    return (
        db.query(ConversationEpisode.id)
        .filter(
            ConversationEpisode.session_id == session_id,
            ConversationEpisode.tenant_id == tenant_id,
        )
        .limit(1)
        .first()
        is not None
    )


# ── Agent baseline ────────────────────────────────────────────────────


def get_affect_baseline(
    db: Session,
    *,
    agent_id: uuid.UUID,
    tenant_id: uuid.UUID,
) -> PADVector:
    """Read the agent's stable affect baseline. Returns neutral if the
    agent has no baseline yet OR the agent doesn't exist for this
    tenant — callers MUST NOT use this for permission checks, only for
    PAD math.

    We aggregate across all AgentMemory rows belonging to this agent in
    this tenant; in Phase 1 we expect zero or one row to carry the
    baseline. Phase 2 may introduce a dedicated `agent_affect_baselines`
    table; for now we piggyback on the existing memory layer.

    Deterministic selection: when multiple non-null baseline rows exist
    (DB doesn't enforce uniqueness), pick the most-recently-updated
    row. This way callers see the agent's freshest baseline rather than
    an arbitrary one.
    """
    row = (
        db.query(AgentMemory.affect_baseline)
        .filter(
            AgentMemory.agent_id == agent_id,
            AgentMemory.tenant_id == tenant_id,
            AgentMemory.affect_baseline.isnot(None),
        )
        .order_by(AgentMemory.updated_at.desc().nullslast())
        .first()
    )
    if row is None:
        return PADVector.neutral()
    return PADVector.from_dict(row[0])


# ── RL wire-in helper ─────────────────────────────────────────────────


def get_latest_session_affect(
    db: Session,
    *,
    session_id: uuid.UUID,
    tenant_id: uuid.UUID,
) -> Optional[PADVector]:
    """Return the most recent PAD vector for a session, or None if no
    episode in the session has an affect_vector yet.

    Phase 1 PR C: read path for the prompt-side style injection.
    Tenant-scoped — foreign sessions return None.
    """
    episode = (
        db.query(ConversationEpisode)
        .filter(
            ConversationEpisode.session_id == session_id,
            ConversationEpisode.tenant_id == tenant_id,
            ConversationEpisode.affect_vector.isnot(None),
        )
        .order_by(ConversationEpisode.created_at.desc())
        .first()
    )
    if episode is None or episode.affect_vector is None:
        return None
    return PADVector.from_dict(episode.affect_vector)


def build_affect_addendum_for_session(
    db: Session,
    *,
    session_id: Optional[uuid.UUID],
    tenant_id: uuid.UUID,
) -> str:
    """Return the system-prompt addendum string for the session's
    current affective state, or empty string if no affect recorded /
    state is neutral.

    This is the THIN seam used by cli_session_manager: it can
    unconditionally concatenate the return value into the assembled
    prompt without checking whether emotion is enabled.

    None session_id is tolerated (returns "") so callers don't need
    upstream guards for ad-hoc / one-off prompts.
    """
    if session_id is None:
        return ""
    try:
        vector = get_latest_session_affect(
            db,
            session_id=session_id,
            tenant_id=tenant_id,
        )
    except Exception:
        # Best-effort: never let an emotion-layer read failure crash
        # prompt assembly. The CLI gets a slightly stale (neutral)
        # prompt instead of a 500.
        return ""
    return format_affect_addendum(vector)


def appraise_and_record_tool_failure(
    db: Session,
    *,
    episode_id: uuid.UUID,
    tenant_id: uuid.UUID,
    agent_id: uuid.UUID,
    severity: float = 0.5,
) -> Optional[PADVector]:
    """The error-path wire-in: when a tool call fails, run failure
    appraisal and persist. Mirrors appraise_and_record_tool_outcome
    but for the tool_failure event type — pleasure down + arousal UP
    (Luna's temperature-flip correction).

    Args:
        severity: float in [0, 1]. Caller-derived intensity of the
            failure. 1.0 = hard exception / unrecoverable. 0.3 =
            retryable. Defaults to 0.5 (moderate). Values outside
            [0, 1] are clamped by the underlying appraise_event.

    Returns the post-appraisal PAD vector or None if the episode
    doesn't exist / is tenant-foreign.
    """
    episode = (
        db.query(ConversationEpisode)
        .filter(
            ConversationEpisode.id == episode_id,
            ConversationEpisode.tenant_id == tenant_id,
        )
        .first()
    )
    if episode is None:
        return None

    baseline = get_affect_baseline(db, agent_id=agent_id, tenant_id=tenant_id)
    current = (
        PADVector.from_dict(episode.affect_vector)
        if episode.affect_vector
        else baseline
    )

    new_vector = appraise_event(
        "tool_failure",
        {"severity": severity},
        current=current,
        baseline=baseline,
    )
    episode.affect_vector = new_vector.to_dict()
    db.commit()
    db.refresh(episode)
    return new_vector


def record_session_tool_failure(
    db: Session,
    *,
    session_id: Optional[uuid.UUID],
    tenant_id: uuid.UUID,
    agent_id: Optional[uuid.UUID] = None,
    severity: float = 0.5,
) -> Optional[PADVector]:
    """Phase 2 wire-in for tool_failure events from `cli_session_manager`.

    Finds the most recent conversation_episode for this session and applies
    `appraise_and_record_tool_failure` to it. Graceful no-op when:
      - session_id is None (defensive — call site passes whatever is in
        db_session_memory),
      - no episode exists yet for this session (first failure of a fresh
        session — PostChatMemoryWorkflow hasn't created an episode yet),
      - the most-recent episode is foreign-tenant (the IO helper's
        existing safety pattern).

    Wraps the existing pure-function appraisal so cli_session_manager
    callers don't have to know about episode_id resolution. This sits
    between the chat hot path and the emotion engine; it MUST NOT raise
    — the caller is in an exception handler already.

    AGENT ATTRIBUTION CAVEAT (Phase 3 follow-up):
    When `agent_id` is None we fall back to a random UUID so the baseline
    lookup returns neutral. That keeps the appraisal correct but the
    persisted `affect_vector` has no agent-of-record. Phase 3 should
    resolve agent_id from db_session_memory or the most-recent
    ExecutionTrace before persisting; without it, per-agent affect
    analytics will be blind to failures originating from
    `cli_session_manager`. TODO(phase-3): plumb agent_id through.
    """
    if session_id is None:
        return None
    try:
        episode = (
            db.query(ConversationEpisode)
            .filter(
                ConversationEpisode.session_id == session_id,
                ConversationEpisode.tenant_id == tenant_id,
            )
            .order_by(ConversationEpisode.created_at.desc())
            .first()
        )
    except SQLAlchemyError as exc:
        logger.warning(
            "emotion_engine_io.record_session_tool_failure: episode "
            "lookup failed. session_id=%s tenant_id=%s err=%s",
            session_id, tenant_id, exc,
        )
        return None
    if episode is None:
        # First failure of a fresh session — no episode to attach to yet.
        # Phase 3 can buffer these in Redis for PostChatMemoryWorkflow to
        # apply at episode creation; Phase 2 accepts the miss.
        return None
    return appraise_and_record_tool_failure(
        db,
        episode_id=episode.id,
        tenant_id=tenant_id,
        agent_id=agent_id or uuid.uuid4(),
        severity=severity,
    )


def appraise_and_record_tool_outcome(
    db: Session,
    *,
    episode_id: uuid.UUID,
    tenant_id: uuid.UUID,
    agent_id: uuid.UUID,
    reward: float,
) -> Optional[PADVector]:
    """The RL wire-in path: when an RL experience is rewarded, run
    appraisal and persist the new affect on the episode.

    Returns the post-appraisal PAD vector, or None if the episode
    doesn't exist / is tenant-foreign.

    Phase 1 simplifying assumption: we read the *most recent* episode's
    affect_vector as `current`, fall back to baseline if missing. Phase
    2/3 can plumb in a richer "live affect" tracking mechanism.
    """
    episode = (
        db.query(ConversationEpisode)
        .filter(
            ConversationEpisode.id == episode_id,
            ConversationEpisode.tenant_id == tenant_id,
        )
        .first()
    )
    if episode is None:
        return None

    baseline = get_affect_baseline(db, agent_id=agent_id, tenant_id=tenant_id)
    current = (
        PADVector.from_dict(episode.affect_vector)
        if episode.affect_vector
        else baseline
    )

    new_vector = appraise_event(
        "tool_outcome",
        {"reward": reward},
        current=current,
        baseline=baseline,
    )
    episode.affect_vector = new_vector.to_dict()
    try:
        db.commit()
        db.refresh(episode)
    except SQLAlchemyError as exc:
        logger.warning(
            "emotion_engine_io.appraise_and_record_tool_outcome: commit "
            "failed, rolling back. episode_id=%s tenant_id=%s err=%s",
            episode_id, tenant_id, exc,
        )
        db.rollback()
        return None
    return new_vector
