"""Luna-impact baseline aggregator (task #327).

Layer-1 measurable signals for the operator dashboard. Pulls from
existing substrates only — no new tables, no migrations. SQL aggregates
are the preferred shape; log-only metrics use a best-effort reader that
degrades to null + `_unavailable_metrics` rather than crashing the
endpoint.

Reference: docs/plans/2026-05-20-luna-metacognition-and-dreams-canonical.md §6.

The endpoint (apps/api/app/api/v1/luna_impact.py) calls
`compute_impact(db, tenant_id=..., window_days=...)` which returns the
full JSON-ready dict.
"""
from __future__ import annotations

import logging
import os
import re
import time
import uuid
from collections import Counter
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models.agent_memory import AgentMemory
from app.models.chat import ChatMessage, ChatSession
from app.models.conversation_episode import ConversationEpisode
from app.services.metacog import (
    OBSERVATION_MEMORY_TYPE,
    PREDICTION_MEMORY_TYPE,
)
from app.services.metacog_io import list_traces
from app.services.team_engine import ROLE_CONTRACT_MEMORY_TYPE


logger = logging.getLogger(__name__)


# ── Constants ─────────────────────────────────────────────────────────

# Bound for window_days param (mirrors the spec; endpoint enforces too).
MAX_WINDOW_DAYS = 90
DEFAULT_WINDOW_DAYS = 7

# Grace period from #618 — surfaced verbatim for the operator UI.
POST_REDEPLOY_GRACE_PERIOD_SECONDS = 180

# Window (seconds) for the "estimated lost chats" heuristic. A chat
# whose last message lands within this many seconds of a known restart
# is treated as a likely Luna-died-mid-response casualty.
LOST_CHAT_PROXIMITY_WINDOW_SECONDS = 90


# ── Process start tracking ────────────────────────────────────────────

_PROCESS_START_MONOTONIC: Optional[float] = None


def mark_process_start() -> None:
    """Record the api process start time. Called from main.py at import.

    Idempotent: subsequent calls during the same process are no-ops so
    a reload doesn't reset the clock.
    """
    global _PROCESS_START_MONOTONIC
    if _PROCESS_START_MONOTONIC is None:
        _PROCESS_START_MONOTONIC = time.monotonic()


def get_api_uptime_seconds() -> Optional[int]:
    """Return seconds since the api process started, or None if unknown."""
    if _PROCESS_START_MONOTONIC is None:
        return None
    return int(time.monotonic() - _PROCESS_START_MONOTONIC)


# ── Log-reading helpers (best-effort) ─────────────────────────────────

# Log file the api writes to (if any). The container streams to stdout
# by default; if a log file path is exposed via env we read from it.
_LOG_FILE_ENV = "LUNA_IMPACT_LOG_FILE"
# Hard cap on bytes read per metric — we don't want to OOM the api.
_LOG_READ_BYTE_CAP = 5 * 1024 * 1024  # 5 MiB


def _read_log_tail() -> Optional[str]:
    """Best-effort tail of the api log file. Returns None when no log
    file is configured or any IO error occurs. Capped at 5 MiB.

    Patterns covered by the canonical doc §6:
      - `TerminalQuotaError`            → gemini_quota_failures
      - `CLI chain resolved`            → fallback_chain_invocations
      - `filtered by quota-aware`       → preemptive_cooldown_skips
      - `chain length=<int>`            → mean_attempted_chain_length
    """
    path = os.environ.get(_LOG_FILE_ENV)
    if not path:
        return None
    try:
        size = os.path.getsize(path)
        with open(path, "rb") as fh:
            if size > _LOG_READ_BYTE_CAP:
                fh.seek(size - _LOG_READ_BYTE_CAP)
            data = fh.read()
        return data.decode("utf-8", errors="replace")
    except (OSError, IOError) as exc:  # noqa: BLE001
        logger.warning("luna_impact: log tail read failed — %s", exc)
        raise


_CHAIN_LEN_RE = re.compile(r"chain[_ ]length\s*[=:]\s*(\d+)")


def _scrape_routing_log_metrics() -> Dict[str, Any]:
    """Return the four log-derived routing metrics + a flag listing
    which (if any) couldn't be measured.

    On a successful log read every value is populated; on failure all
    four go to None and "routing_log_metrics" is added to the unavailable
    list (the caller flattens that into the response).
    """
    out: Dict[str, Any] = {
        "gemini_quota_failures": None,
        "fallback_chain_invocations": None,
        "mean_attempted_chain_length": None,
        "preemptive_cooldown_skips": None,
        "_log_available": False,
    }
    try:
        text = _read_log_tail()
    except Exception:  # noqa: BLE001
        text = None
    if text is None:
        return out

    out["gemini_quota_failures"] = text.count("TerminalQuotaError")
    out["fallback_chain_invocations"] = text.count("CLI chain resolved")
    out["preemptive_cooldown_skips"] = text.count("filtered by quota-aware")

    lengths = [int(m.group(1)) for m in _CHAIN_LEN_RE.finditer(text)]
    out["mean_attempted_chain_length"] = (
        round(sum(lengths) / len(lengths), 2) if lengths else 0.0
    )
    out["_log_available"] = True
    return out


# ── SQL aggregators (tenant-scoped) ───────────────────────────────────


def _count_chat_turns_in_window(
    db: Session, *, tenant_id: uuid.UUID, since: datetime,
) -> int:
    """Total chat turns (any role) in the tenant's sessions within window."""
    return (
        db.query(func.count(ChatMessage.id))
        .join(ChatSession, ChatMessage.session_id == ChatSession.id)
        .filter(
            ChatSession.tenant_id == tenant_id,
            ChatMessage.created_at >= since,
        )
        .scalar()
        or 0
    )


def _count_affect_episodes(
    db: Session, *, tenant_id: uuid.UUID, since: datetime,
) -> int:
    return (
        db.query(func.count(ConversationEpisode.id))
        .filter(
            ConversationEpisode.tenant_id == tenant_id,
            ConversationEpisode.affect_vector.isnot(None),
            ConversationEpisode.created_at >= since,
        )
        .scalar()
        or 0
    )


def _affect_summary(
    db: Session, *, tenant_id: uuid.UUID, since: datetime,
) -> Dict[str, Any]:
    """Aggregate PAD means + dominant label over the window.

    Iterates rows in Python because affect_vector is JSONB and the
    cross-dialect SQL (postgres jsonb_path vs. sqlite json_extract) is
    not worth the complexity for the row volumes we expect (≤ tens of
    thousands per tenant per week). Falls back to zeros if no rows.
    """
    rows = (
        db.query(ConversationEpisode.affect_vector)
        .filter(
            ConversationEpisode.tenant_id == tenant_id,
            ConversationEpisode.affect_vector.isnot(None),
            ConversationEpisode.created_at >= since,
        )
        .all()
    )
    pleasures: List[float] = []
    arousals: List[float] = []
    dominances: List[float] = []
    labels: List[str] = []
    for (vec,) in rows:
        if not isinstance(vec, dict):
            continue
        p = vec.get("pleasure")
        a = vec.get("arousal")
        d = vec.get("dominance")
        if isinstance(p, (int, float)):
            pleasures.append(float(p))
        if isinstance(a, (int, float)):
            arousals.append(float(a))
        if isinstance(d, (int, float)):
            dominances.append(float(d))
        lab = vec.get("label")
        if isinstance(lab, str) and lab:
            labels.append(lab)

    if not pleasures and not arousals and not dominances:
        return {
            "sessions_with_affect_vector": len(rows),
            "mean_pleasure": 0.0,
            "mean_arousal": 0.0,
            "mean_dominance": 0.0,
            "dominant_label": None,
        }

    def _mean(xs: List[float]) -> float:
        return round(sum(xs) / len(xs), 4) if xs else 0.0

    dominant = Counter(labels).most_common(1)[0][0] if labels else None
    return {
        "sessions_with_affect_vector": len(rows),
        "mean_pleasure": _mean(pleasures),
        "mean_arousal": _mean(arousals),
        "mean_dominance": _mean(dominances),
        "dominant_label": dominant,
    }


def _count_agent_memory_type(
    db: Session,
    *,
    tenant_id: uuid.UUID,
    memory_type: str,
    since: Optional[datetime] = None,
) -> int:
    q = db.query(func.count(AgentMemory.id)).filter(
        AgentMemory.tenant_id == tenant_id,
        AgentMemory.memory_type == memory_type,
    )
    if since is not None:
        q = q.filter(AgentMemory.created_at >= since)
    return q.scalar() or 0


def _estimate_lost_chats(
    db: Session, *, tenant_id: uuid.UUID, since: datetime,
) -> int:
    """Proxy for "Luna died mid-response": count chat_sessions whose
    last message timestamp sits within LOST_CHAT_PROXIMITY_WINDOW_SECONDS
    *before* a detected api restart.

    Restarts are detected by looking at the current api uptime: if the
    process started during the window, that start time is a known
    restart point. Cross-restart history isn't persisted (no new
    tables) so older restarts in the window are not counted — the
    metric is an underestimate, not a fabrication. Returns 0 when
    uptime is unknown or the process started before the window.
    """
    uptime = get_api_uptime_seconds()
    if uptime is None:
        return 0
    restart_at = datetime.utcnow() - timedelta(seconds=uptime)
    if restart_at < since:
        # Process has been up longer than the window — no detectable
        # restart inside the analyser's horizon. Underestimate, not a lie.
        return 0

    proximity_floor = restart_at - timedelta(
        seconds=LOST_CHAT_PROXIMITY_WINDOW_SECONDS
    )

    # Sessions whose MAX(ChatMessage.created_at) falls in
    # [proximity_floor, restart_at] are flagged. We use a sub-query that
    # works on both postgres and sqlite (the test backend).
    sub = (
        db.query(
            ChatMessage.session_id.label("sid"),
            func.max(ChatMessage.created_at).label("last_msg"),
        )
        .join(ChatSession, ChatMessage.session_id == ChatSession.id)
        .filter(ChatSession.tenant_id == tenant_id)
        .group_by(ChatMessage.session_id)
        .subquery()
    )
    return (
        db.query(func.count(sub.c.sid))
        .filter(sub.c.last_msg >= proximity_floor, sub.c.last_msg <= restart_at)
        .scalar()
        or 0
    )


# ── Top-level aggregator ──────────────────────────────────────────────


def compute_impact(
    db: Session,
    *,
    tenant_id: uuid.UUID,
    window_days: int = DEFAULT_WINDOW_DAYS,
) -> Dict[str, Any]:
    """Build the impact payload for a tenant.

    window_days is bounds-checked here too so direct callers (workers,
    cron snapshots) get the same guarantees as HTTP callers.
    """
    if window_days < 1:
        window_days = 1
    if window_days > MAX_WINDOW_DAYS:
        window_days = MAX_WINDOW_DAYS

    unavailable: List[str] = []
    since = datetime.utcnow() - timedelta(days=window_days)
    now_iso = datetime.now(timezone.utc).isoformat()

    # ── Stability ────────────────────────────────────────────────────
    uptime = get_api_uptime_seconds()
    if uptime is None:
        unavailable.append("api_uptime_seconds")
    stability = {
        "api_uptime_seconds": uptime,
        "post_redeploy_grace_period_seconds": POST_REDEPLOY_GRACE_PERIOD_SECONDS,
        "estimated_lost_chats_to_redeploy": _estimate_lost_chats(
            db, tenant_id=tenant_id, since=since,
        ),
    }

    # ── Routing (mostly log-derived) ─────────────────────────────────
    log_metrics = _scrape_routing_log_metrics()
    if not log_metrics.pop("_log_available", False):
        for k in (
            "gemini_quota_failures",
            "fallback_chain_invocations",
            "mean_attempted_chain_length",
            "preemptive_cooldown_skips",
        ):
            unavailable.append(f"routing.{k}")

    routing = {
        "total_chat_turns": _count_chat_turns_in_window(
            db, tenant_id=tenant_id, since=since,
        ),
        **log_metrics,
    }

    # ── Affect ───────────────────────────────────────────────────────
    affect = _affect_summary(db, tenant_id=tenant_id, since=since)

    # ── Coordination (role contracts + log signal) ───────────────────
    active_contracts = _count_agent_memory_type(
        db,
        tenant_id=tenant_id,
        memory_type=ROLE_CONTRACT_MEMORY_TYPE,
    )
    coalition_contract_routing: Optional[int]
    try:
        text = _read_log_tail()
    except Exception:  # noqa: BLE001
        text = None
    if text is None:
        coalition_contract_routing = None
        unavailable.append("coordination.coalition_dispatches_with_contract_routing")
    else:
        coalition_contract_routing = text.count("team-role contracts shaped routing")
    coordination = {
        "active_role_contracts": active_contracts,
        "coalition_dispatches_with_contract_routing": coalition_contract_routing,
    }

    # ── Metacognition substrate ──────────────────────────────────────
    predictions_n = _count_agent_memory_type(
        db,
        tenant_id=tenant_id,
        memory_type=PREDICTION_MEMORY_TYPE,
        since=since,
    )
    observations_n = _count_agent_memory_type(
        db,
        tenant_id=tenant_id,
        memory_type=OBSERVATION_MEMORY_TYPE,
        since=since,
    )
    try:
        joined = list_traces(db, tenant_id=tenant_id)
        joined_n = len(joined)
    except Exception as exc:  # noqa: BLE001
        logger.warning("luna_impact: list_traces failed — %s", exc)
        joined_n = 0
        unavailable.append("metacog.joined_traces_available_for_ece")
    metacog = {
        "predictions_persisted": predictions_n,
        "observations_persisted": observations_n,
        "joined_traces_available_for_ece": joined_n,
    }

    payload: Dict[str, Any] = {
        "tenant_id": str(tenant_id),
        "window_days": window_days,
        "generated_at": now_iso,
        "stability": stability,
        "routing": routing,
        "affect": affect,
        "coordination": coordination,
        "metacog": metacog,
    }
    if unavailable:
        payload["_unavailable_metrics"] = unavailable
    return payload


__all__ = [
    "MAX_WINDOW_DAYS",
    "DEFAULT_WINDOW_DAYS",
    "POST_REDEPLOY_GRACE_PERIOD_SECONDS",
    "compute_impact",
    "get_api_uptime_seconds",
    "mark_process_start",
]
