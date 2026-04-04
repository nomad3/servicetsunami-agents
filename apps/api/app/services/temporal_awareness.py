"""
Temporal Awareness — Gap 5 (Temporal Self-Awareness + Presence & Rhythm Detection).

Luna analyzes when the user is active, infers their timezone and work patterns,
and uses that context to calibrate timing, greetings, and suggestions.

Data sources:
  - UserActivity events (native client sends app_switch, file_open, etc.)
  - ChatMessage timestamps (any channel: web, WhatsApp, etc.)
  - UserPresence state transitions

Outputs:
  - Inferred timezone offset (hours from UTC)
  - Daily active window (e.g. "09:00–18:00")
  - Weekly rhythm (e.g. "Mon–Fri")
  - Session cadence (e.g. "checks in 3x/day, ~30 min each")
  - Time-of-day greeting label (morning / afternoon / evening / night)
  - Temporal context string for system prompt injection
"""

import uuid
import logging
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

# Greeting windows (server UTC hour → label, adjusted by inferred tz offset)
_GREETING_WINDOWS = [
    (5, 12, "morning"),
    (12, 17, "afternoon"),
    (17, 21, "evening"),
    (21, 24, "night"),
    (0, 5, "night"),
]

_WEEKDAY_NAMES = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]


def get_temporal_context(
    db: Session,
    tenant_id: uuid.UUID,
    user_id: Optional[uuid.UUID] = None,
    lookback_days: int = 14,
) -> dict:
    """
    Analyze recent activity to derive temporal profile.

    Returns a dict with:
      - tz_offset_hours: inferred timezone as UTC offset (float)
      - active_window_start: earliest usual active hour (local)
      - active_window_end: latest usual active hour (local)
      - active_days: list of weekday names typically active
      - sessions_per_day: average daily session count
      - avg_session_minutes: average session length
      - greeting: time-appropriate label for right now
      - last_seen_hours_ago: hours since last activity
    """
    cutoff = datetime.utcnow() - timedelta(days=lookback_days)

    # Gather timestamps from both sources: UserActivity + ChatMessage
    timestamps = _collect_activity_timestamps(db, tenant_id, user_id, cutoff)

    if len(timestamps) < 5:
        # Not enough data — return neutral defaults
        return _default_context()

    tz_offset = _infer_timezone_offset(timestamps)
    local_hours = [(ts + timedelta(hours=tz_offset)).hour for ts in timestamps]
    local_weekdays = [(ts + timedelta(hours=tz_offset)).weekday() for ts in timestamps]

    active_window_start, active_window_end = _compute_active_window(local_hours)
    active_days = _compute_active_days(local_weekdays)
    sessions_per_day, avg_session_minutes = _compute_session_cadence(timestamps, tz_offset)
    last_seen = _compute_last_seen(timestamps)

    now_local_hour = (datetime.utcnow() + timedelta(hours=tz_offset)).hour
    greeting = _get_greeting(now_local_hour)

    return {
        "tz_offset_hours": round(tz_offset, 1),
        "active_window_start": active_window_start,
        "active_window_end": active_window_end,
        "active_days": active_days,
        "sessions_per_day": round(sessions_per_day, 1),
        "avg_session_minutes": round(avg_session_minutes, 0),
        "greeting": greeting,
        "last_seen_hours_ago": last_seen,
        "data_points": len(timestamps),
    }


def build_temporal_system_context(
    db: Session,
    tenant_id: uuid.UUID,
    user_id: Optional[uuid.UUID] = None,
) -> str:
    """
    Build a compact temporal context block for Luna's system prompt.

    Tells Luna:
    - What time of day it is for the user right now
    - Their typical active hours and days
    - How long since they last checked in
    """
    profile = get_temporal_context(db, tenant_id, user_id)

    if not profile.get("active_window_start"):
        return ""

    now_utc = datetime.utcnow()
    tz_offset = profile["tz_offset_hours"]
    now_local = now_utc + timedelta(hours=tz_offset)
    now_str = now_local.strftime("%A %H:%M")

    tz_label = _format_tz_label(tz_offset)
    window = f"{profile['active_window_start']:02d}:00–{profile['active_window_end']:02d}:00"
    days = ", ".join(profile["active_days"]) if profile["active_days"] else "most days"

    lines = [
        "## Temporal Context",
        f"- Local time: {now_str} ({tz_label}) — greet as {profile['greeting'].upper()}",
        f"- Typical active hours: {window} on {days}",
    ]

    if profile["sessions_per_day"] > 0:
        lines.append(
            f"- Session pattern: ~{profile['sessions_per_day']}x/day, "
            f"~{int(profile['avg_session_minutes'])} min each"
        )

    last_seen = profile.get("last_seen_hours_ago")
    if last_seen is not None:
        if last_seen < 1:
            lines.append("- Last seen: just now — they're active right now")
        elif last_seen < 24:
            lines.append(f"- Last seen: {int(last_seen)}h ago")
        else:
            days_ago = int(last_seen / 24)
            lines.append(f"- Last seen: {days_ago} day(s) ago — open with a catch-up")

    lines.append(
        "Use this to calibrate your tone. Don't send long reports at 11pm local time. "
        "Adjust greetings to time of day."
    )

    return "\n".join(lines)


# ── Private helpers ──────────────────────────────────────────────────────────

def _collect_activity_timestamps(
    db: Session,
    tenant_id: uuid.UUID,
    user_id: Optional[uuid.UUID],
    cutoff: datetime,
) -> list:
    """Collect recent activity timestamps from UserActivity and ChatMessage."""
    from app.models.user_activity import UserActivity
    from app.models.chat import ChatMessage, ChatSession

    timestamps = []

    # UserActivity events
    try:
        query = db.query(UserActivity.created_at).filter(
            UserActivity.tenant_id == tenant_id,
            UserActivity.created_at >= cutoff,
        )
        if user_id:
            query = query.filter(UserActivity.user_id == user_id)
        for (ts,) in query.limit(500).all():
            if ts:
                timestamps.append(ts)
    except Exception as e:
        logger.debug("UserActivity query failed: %s", e)

    # ChatMessage timestamps (user messages only = real presence signal)
    try:
        session_query = db.query(ChatSession.id).filter(
            ChatSession.tenant_id == tenant_id,
        )
        session_ids = [row[0] for row in session_query.all()]

        if session_ids:
            msg_query = (
                db.query(ChatMessage.created_at)
                .filter(
                    ChatMessage.session_id.in_(session_ids),
                    ChatMessage.role == "user",
                    ChatMessage.created_at >= cutoff,
                )
                .limit(500)
            )
            for (ts,) in msg_query.all():
                if ts:
                    timestamps.append(ts)
    except Exception as e:
        logger.debug("ChatMessage timestamp query failed: %s", e)

    return sorted(timestamps)


def _infer_timezone_offset(timestamps: list) -> float:
    """
    Infer timezone offset from activity timestamps.

    Heuristic: find the UTC hour that, when subtracted, places the peak
    activity window between 09:00 and 13:00 local time (typical morning work).
    """
    if not timestamps:
        return 0.0

    hour_counts = Counter(ts.hour for ts in timestamps)
    peak_utc_hour = max(hour_counts, key=hour_counts.get)

    # Try to place peak at ~10:00 local (middle of morning work window)
    target_local_hour = 10
    offset = target_local_hour - peak_utc_hour

    # Clamp to realistic timezone range: -12 to +14
    return max(-12.0, min(14.0, float(offset)))


def _compute_active_window(local_hours: list) -> tuple:
    """Find the typical active hour range (5th–95th percentile)."""
    if not local_hours:
        return (9, 18)

    sorted_hours = sorted(local_hours)
    p5 = sorted_hours[max(0, int(len(sorted_hours) * 0.05))]
    p95 = sorted_hours[min(len(sorted_hours) - 1, int(len(sorted_hours) * 0.95))]

    # Round to nearest hour, enforce sane bounds
    start = max(0, min(p5, 23))
    end = max(start + 1, min(p95, 23))
    return (start, end)


def _compute_active_days(weekdays: list) -> list:
    """Return list of weekday name strings where activity > 10% of peak day."""
    if not weekdays:
        return list(_WEEKDAY_NAMES[:5])  # Default Mon-Fri

    counts = Counter(weekdays)
    peak = max(counts.values())
    threshold = peak * 0.1

    return [_WEEKDAY_NAMES[day] for day in sorted(counts) if counts[day] >= threshold]


def _compute_session_cadence(timestamps: list, tz_offset: float) -> tuple:
    """Estimate average sessions per day and average session duration."""
    if not timestamps:
        return (1.0, 30.0)

    # Group timestamps into sessions (gap > 30 min = new session)
    sessions = []
    session_start = timestamps[0]
    prev = timestamps[0]

    for ts in timestamps[1:]:
        gap = (ts - prev).total_seconds() / 60
        if gap > 30:
            sessions.append((session_start, prev))
            session_start = ts
        prev = ts
    sessions.append((session_start, prev))

    if not sessions:
        return (1.0, 30.0)

    # Days spanned
    span_days = max(1, (timestamps[-1] - timestamps[0]).days + 1)
    sessions_per_day = len(sessions) / span_days

    durations = [(end - start).total_seconds() / 60 for start, end in sessions]
    avg_duration = sum(durations) / len(durations) if durations else 30.0

    # Cap unrealistic values
    avg_duration = min(avg_duration, 480)  # max 8h session
    return (sessions_per_day, avg_duration)


def _compute_last_seen(timestamps: list) -> Optional[float]:
    """Return hours since most recent activity timestamp."""
    if not timestamps:
        return None
    most_recent = max(timestamps)
    delta = datetime.utcnow() - most_recent
    return delta.total_seconds() / 3600


def _get_greeting(local_hour: int) -> str:
    for start, end, label in _GREETING_WINDOWS:
        if start <= local_hour < end:
            return label
    return "morning"


def _format_tz_label(offset: float) -> str:
    sign = "+" if offset >= 0 else ""
    if offset == int(offset):
        return f"UTC{sign}{int(offset)}"
    h = int(offset)
    m = int(abs(offset - h) * 60)
    return f"UTC{sign}{h}:{m:02d}"


def _default_context() -> dict:
    """Return neutral defaults when there's not enough data."""
    now = datetime.utcnow()
    greeting = _get_greeting(now.hour)
    return {
        "tz_offset_hours": 0.0,
        "active_window_start": 9,
        "active_window_end": 18,
        "active_days": list(_WEEKDAY_NAMES[:5]),
        "sessions_per_day": 1.0,
        "avg_session_minutes": 30.0,
        "greeting": greeting,
        "last_seen_hours_ago": None,
        "data_points": 0,
    }
