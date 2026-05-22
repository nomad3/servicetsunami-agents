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
from app.services.emotion_engine_metrics import (
    record_affect_write,
    record_appraise_event,
    record_clamp_events,
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
    # Observability — Prometheus instrumentation. Best-effort.
    record_affect_write(tenant_id=str(tenant_id))
    record_clamp_events(
        tenant_id=str(tenant_id),
        pleasure=vector.pleasure,
        arousal=vector.arousal,
        dominance=vector.dominance,
    )
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
    record_appraise_event(tenant_id=str(tenant_id), event_type="tool_failure")
    episode.affect_vector = new_vector.to_dict()
    try:
        db.commit()
        db.refresh(episode)
    except SQLAlchemyError as exc:
        logger.warning(
            "emotion_engine_io.appraise_and_record_tool_failure: commit "
            "failed, rolling back. episode_id=%s tenant_id=%s err=%s",
            episode_id, tenant_id, exc,
        )
        db.rollback()
        return None
    record_affect_write(tenant_id=str(tenant_id))
    record_clamp_events(
        tenant_id=str(tenant_id),
        pleasure=new_vector.pleasure,
        arousal=new_vector.arousal,
        dominance=new_vector.dominance,
    )
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

    AGENT ATTRIBUTION:
    `cli_session_manager._record_tool_failure_affect` now resolves the
    agent_id from `chat_session.agent_id` and passes it through (2026-
    05-20 Phase 3 plumbing). When agent_id is None (e.g. for orphan
    chat sessions or alternate call sites that don't yet plumb it),
    the fallback random UUID keeps the appraisal correct (baseline
    lookup returns neutral) but leaves affect_vector without an
    agent-of-record. The remaining call sites that still need
    plumbing: future caller sites that don't go through cli_session_manager.
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

    # Luna 2026-05-19 review IMPORTANT: silent agent_id=None fallback
    # was destroying attribution. Make the gap loud:
    # - WARN log carries enough context to grep for it
    # - try to record a Prometheus counter event_type so dashboards
    #   show the rate over time (best-effort; the metrics module ships
    #   in PR #607 — graceful no-op when not present yet).
    effective_agent_id = agent_id
    if effective_agent_id is None:
        effective_agent_id = uuid.uuid4()
        logger.warning(
            "emotion_engine_io.record_session_tool_failure: agent_id "
            "fallback triggered (caller didn't resolve a real agent_id). "
            "session_id=%s tenant_id=%s severity=%.2f → using random "
            "UUID %s. Appraisal will land on a neutral baseline; affect "
            "won't be attributable to a real agent.",
            session_id, tenant_id, severity, effective_agent_id,
        )
        try:
            from app.services.emotion_engine_metrics import (
                record_appraise_event,
            )
            record_appraise_event(
                tenant_id=str(tenant_id),
                event_type="agent_id_fallback",
            )
        except ImportError:
            pass  # metrics module ships in PR #607
        except Exception:  # noqa: BLE001
            pass  # never let metrics emission break appraisal

    return appraise_and_record_tool_failure(
        db,
        episode_id=episode.id,
        tenant_id=tenant_id,
        agent_id=effective_agent_id,
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
    record_appraise_event(tenant_id=str(tenant_id), event_type="tool_outcome")
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
    record_affect_write(tenant_id=str(tenant_id))
    record_clamp_events(
        tenant_id=str(tenant_id),
        pleasure=new_vector.pleasure,
        arousal=new_vector.arousal,
        dominance=new_vector.dominance,
    )
    return new_vector


# ── User-signal wire-in (PR 5 — value-layer aware) ────────────────────


# Pursue-match scale factor (design §4.2 Q3 round-1 resolution by Luna).
# When the value-layer consult surfaces a `pursue` match on this user
# turn, amplify the user_signal pleasure axis by this factor. The pure
# layer enforces the TOOL_OUTCOME_PLEASURE_GAIN cap, so even if this is
# bumped above ~2.0 in the future, a pursue user signal can never
# exceed a real tool success.
_PURSUE_GAIN_SCALE = 1.5


def appraise_and_record_user_signal(
    db: Session,
    *,
    episode_id: uuid.UUID,
    tenant_id: uuid.UUID,
    agent_id: uuid.UUID,
    payload: dict,
    user_text: str,
) -> Optional[PADVector]:
    """Value-layer aware user_signal wire-in (#647 PR 5).

    Args:
        payload: classifier output from ``classify_user_signal``. Shape
            {"pleasure": float, "arousal": float, "dominance": float}
            each in [-1, 1]. Raw user text NEVER reaches the pure
            ``_appraise_user_signal`` — only the bounded classifier
            output does (constitutive-vs-performative defence, design
            § Open questions §5).
        user_text: the original user message, passed to the value-layer
            consult (which has its OWN slug-match boundary) so a
            ``pursue`` slug match can scale the pleasure axis upward.

    Order of operations:
      1. Look up episode + baseline (same shape as tool_outcome path)
      2. Consult value layer with ``point='user_signal'``,
         ``intent='read'``. Fail-open on crash — emotion layer must
         never crash chat hot path.
      3. Derive scale: 1.5x if verdict.decision=='allow' AND
         reason startswith 'pursue_match' AND matched_item is set.
         Otherwise 1.0.
      4. Call pure ``appraise_event('user_signal', ...,
         pursue_gain_scale=scale)`` which enforces the cap.
      5. Persist new vector to episode + record metrics.

    Returns the post-appraisal PAD vector, or None if the episode
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

    # Value-layer consult — fail-open. A crash here MUST NOT block the
    # emotion update; we proceed with scale=1.0 (no pursue boost) and
    # log for ops. Mirrors the agent_router fail-open pattern from PR 3.
    pursue_gain_scale = 1.0
    try:
        from app.services.agent_value_set_io import (
            appraise_user_signal_with_values,
        )
        verdict = appraise_user_signal_with_values(
            db,
            tenant_id=tenant_id,
            agent_id=agent_id,
            user_text=user_text,
        )
        if (
            verdict.decision == "allow"
            and verdict.reason.startswith("pursue_match")
            and verdict.matched_item is not None
        ):
            pursue_gain_scale = _PURSUE_GAIN_SCALE
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "emotion_engine_io.appraise_and_record_user_signal: "
            "value-layer consult crashed, proceeding without pursue "
            "boost. tenant=%s agent=%s err=%s",
            tenant_id, agent_id, exc,
        )

    new_vector = appraise_event(
        "user_signal",
        payload,
        current=current,
        baseline=baseline,
        pursue_gain_scale=pursue_gain_scale,
    )
    record_appraise_event(tenant_id=str(tenant_id), event_type="user_signal")
    episode.affect_vector = new_vector.to_dict()
    try:
        db.commit()
        db.refresh(episode)
    except SQLAlchemyError as exc:
        logger.warning(
            "emotion_engine_io.appraise_and_record_user_signal: commit "
            "failed, rolling back. episode_id=%s tenant_id=%s err=%s",
            episode_id, tenant_id, exc,
        )
        db.rollback()
        return None
    record_affect_write(tenant_id=str(tenant_id))
    record_clamp_events(
        tenant_id=str(tenant_id),
        pleasure=new_vector.pleasure,
        arousal=new_vector.arousal,
        dominance=new_vector.dominance,
    )
    return new_vector


# ── Phase 1.5 user_signal session-level wire-in (task #336) ──────────
#
# PR #653 shipped ``appraise_and_record_user_signal`` (the contract).
# It was dead-wired upstream — no caller invoked it from production.
# Luna's 2026-05-22 audit P0 #1 flagged it as the biggest unfinished
# bit of the value-layer work. This is the missing caller.
#
# Mirrors ``record_session_tool_failure`` shape: takes a session_id,
# resolves to the most recent episode, classifies the user text via
# the heuristic backend (synchronous, microsecond cost — adequate for
# the pursue-match PAD scaling signal), then calls the existing
# ``appraise_and_record_user_signal``. Fail-open at every layer; the
# chat hot path NEVER blocks on this.


def record_session_user_signal(
    db: Session,
    *,
    session_id: Optional[uuid.UUID],
    tenant_id: uuid.UUID,
    agent_id: Optional[uuid.UUID] = None,
    user_text: str,
    backend: Optional[str] = None,
) -> Optional[PADVector]:
    """Session-level wire-in for user_signal events from the chat
    hot path. The companion to ``record_session_tool_failure``.

    Steps:
      1. Bail gracefully on empty session_id or empty user_text.
      2. Find the most recent conversation_episode for this session
         + tenant. If none exists (first turns of a fresh session
         before PostChatMemoryWorkflow has run), no-op.
      3. Classify the user text via ``user_signal_classifier``. Default
         backend is ``heuristic`` (synchronous, microsecond cost) so
         the chat hot path doesn't pay the ~1s ollama latency on every
         turn. Operators can promote to ollama via the
         ``USER_SIGNAL_CHAT_BACKEND`` env var when the precision is
         worth the latency.
      4. Forward (classifier_payload, user_text) to
         ``appraise_and_record_user_signal`` which handles the value-
         layer consult, pursue-match boost, and PAD vector commit.

    Returns the post-appraisal PAD vector, or None on any defensive
    no-op. NEVER raises — callers are typically inside a bare
    except.
    """
    import os as _os

    if session_id is None:
        return None
    if not user_text or not user_text.strip():
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
            "emotion_engine_io.record_session_user_signal: episode "
            "lookup failed. session_id=%s tenant_id=%s err=%s",
            session_id, tenant_id, exc,
        )
        return None
    if episode is None:
        return None

    # Default to heuristic backend on the chat hot path — synchronous
    # microsecond cost. Operators can opt in to ollama via env var
    # when the higher-accuracy classifier is worth the ~1s/turn cost.
    effective_backend = (
        backend
        or _os.environ.get("USER_SIGNAL_CHAT_BACKEND", "heuristic")
    )
    try:
        from app.services.user_signal_classifier import (
            classify_user_signal,
        )
        result = classify_user_signal(
            user_text, backend=effective_backend,
        )
        payload = result.to_dict()
    except Exception as exc:  # noqa: BLE001 — fail-open
        logger.warning(
            "emotion_engine_io.record_session_user_signal: "
            "classify_user_signal raised. session_id=%s tenant_id=%s "
            "err=%s — skipping appraisal",
            session_id, tenant_id, exc,
        )
        return None

    # Mirror the agent_id-fallback pattern from
    # ``record_session_tool_failure``: emit a WARN + use a random
    # UUID so the appraisal lands on a neutral baseline without
    # corrupting another agent's affect state.
    effective_agent_id = agent_id
    if effective_agent_id is None:
        effective_agent_id = uuid.uuid4()
        logger.warning(
            "emotion_engine_io.record_session_user_signal: agent_id "
            "fallback triggered. session_id=%s tenant_id=%s → using "
            "random UUID %s. Appraisal lands on neutral baseline; "
            "affect won't be attributable to a real agent.",
            session_id, tenant_id, effective_agent_id,
        )

    try:
        return appraise_and_record_user_signal(
            db,
            episode_id=episode.id,
            tenant_id=tenant_id,
            agent_id=effective_agent_id,
            payload=payload,
            user_text=user_text,
        )
    except Exception as exc:  # noqa: BLE001 — fail-open
        logger.warning(
            "emotion_engine_io.record_session_user_signal: "
            "appraise_and_record_user_signal raised. session_id=%s "
            "tenant_id=%s err=%s",
            session_id, tenant_id, exc,
        )
        return None
