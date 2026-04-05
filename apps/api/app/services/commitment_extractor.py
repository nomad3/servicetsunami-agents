"""
Commitment extractor — Gap 3 (Stakes).

Parses Luna's responses for explicit commitments and predictions,
stores them as CommitmentRecord rows, and builds stakes context
for system prompt injection so Luna always knows what she owes.

Commitment types extracted:
  - "promise"     → "I'll send that", "I'll follow up", "I'll create..."
  - "prediction"  → "I think X will...", "This should...", "That will..."
  - "reminder"    → "Don't forget to...", "Remember to..."
  - "deadline"    → "by end of day", "by tomorrow", "by Friday"
"""

import re
import uuid
import logging
from datetime import datetime, timedelta
from typing import List, Optional, Tuple
from sqlalchemy.orm import Session

from app.models.commitment_record import CommitmentRecord

logger = logging.getLogger(__name__)

# (pattern, commitment_type, priority)
_COMMITMENT_PATTERNS = [
    (r"i(?:'ll| will) (?:send|draft|write|create|set up|follow up|reach out|check|look into|get back)", "promise", "normal"),
    (r"(?:let me|i(?:'ll| will)) (?:make sure|ensure|confirm|verify)", "promise", "normal"),
    (r"i(?:'ll| will) (?:remind you|set a reminder|schedule|book)", "promise", "normal"),
    (r"(?:i'll|i will) (?:have|get) (?:that|this|it) (?:done|ready|sent|to you)", "promise", "high"),
    (r"i(?:'ll| will) (?:keep|watch|monitor|track) (?:that|this|it)", "promise", "low"),
    (r"(?:i think|i believe|i expect|i predict) (?:this|that|it) (?:will|should|might|could)", "prediction", "low"),
    (r"this should (?:work|fix|resolve|help|improve)", "prediction", "low"),
    (r"that (?:will|should) (?:work|fix|resolve|help|improve|take care)", "prediction", "low"),
    (r"(?:don't forget|remember) to", "reminder", "normal"),
    (r"by (?:end of (?:day|week|month)|tomorrow|friday|monday|next week)", "deadline", "high"),
]

# Time-relative phrases → timedelta offsets for due_at calculation
_DUE_AT_PATTERNS = [
    (r"by end of day|by tonight|today", timedelta(hours=8)),
    (r"by tomorrow", timedelta(days=1)),
    (r"by end of week|by friday", timedelta(days=5)),
    (r"by next week|by monday", timedelta(days=7)),
    (r"by end of month", timedelta(days=30)),
    (r"in (\d+) (?:hour|hours)", None),   # handled separately
    (r"in (\d+) (?:day|days)", None),
]


def extract_commitments_from_response(
    db: Session,
    tenant_id: uuid.UUID,
    response_text: str,
    message_id: Optional[uuid.UUID] = None,
    session_id: Optional[uuid.UUID] = None,
) -> List[CommitmentRecord]:
    """
    Parse Luna's response for commitments/predictions and store as CommitmentRecord rows.
    Returns created records.
    """
    items = _parse_commitments(response_text)
    if not items:
        return []

    records = []
    for title, ctype, priority, due_at in items:
        try:
            # Write directly to model — CommitmentType enum doesn't include
            # "prediction"/"promise" but the DB column is String(50).
            record = CommitmentRecord(
                tenant_id=tenant_id,
                owner_agent_slug="luna",
                title=title,
                description=f"Auto-extracted from response at {datetime.utcnow().isoformat()}",
                commitment_type=ctype,
                state="open",
                priority=priority,
                source_type="chat_response",
                source_ref={
                    "message_id": str(message_id) if message_id else None,
                    "session_id": str(session_id) if session_id else None,
                },
                due_at=due_at,
                related_entity_ids=[],
            )
            db.add(record)
            records.append(record)
        except Exception as e:
            logger.warning(f"Failed to create commitment record: {e}")

    if records:
        db.commit()

    logger.info(f"Extracted {len(records)} commitments for tenant {tenant_id}")
    return records


def build_stakes_context(db: Session, tenant_id: uuid.UUID) -> str:
    """
    Build a system prompt section showing Luna's open commitments and overdue items.
    This gives Luna 'stakes' — she knows what she owes and can reference it naturally.
    """
    from app.services.commitment_service import list_overdue_commitments

    # Open commitments (luna-owned, not yet resolved)
    open_records = (
        db.query(CommitmentRecord)
        .filter(
            CommitmentRecord.tenant_id == tenant_id,
            CommitmentRecord.owner_agent_slug == "luna",
            CommitmentRecord.state.in_(["open", "in_progress"]),
        )
        .order_by(CommitmentRecord.due_at.asc().nullslast())
        .limit(10)
        .all()
    )

    overdue = [r for r in open_records if r.due_at and r.due_at < datetime.utcnow()]
    upcoming = [r for r in open_records if r not in overdue]

    if not open_records:
        return ""

    lines = ["## Your Open Commitments"]

    if overdue:
        lines.append(f"\n⚠️ Overdue ({len(overdue)}):")
        for r in overdue[:3]:
            delta = datetime.utcnow() - r.due_at
            age_h = int(delta.total_seconds() / 3600)
            age_str = f"{age_h // 24}d" if age_h >= 24 else f"{age_h}h"
            lines.append(f"  - [{r.commitment_type}] {r.title} (overdue {age_str})")

    if upcoming:
        lines.append(f"\n📋 Open ({len(upcoming)}):")
        for r in upcoming[:5]:
            due_str = f" — due {r.due_at.strftime('%b %d')}" if r.due_at else ""
            lines.append(f"  - [{r.commitment_type}] {r.title}{due_str}")

    lines.append(
        "\nReference these naturally. If you've completed one, say so and it will be marked fulfilled."
    )

    return "\n".join(lines)


def resolve_commitment_from_message(
    db: Session,
    tenant_id: uuid.UUID,
    user_message: str,
) -> List[CommitmentRecord]:
    """
    Check if a user message resolves any open commitments (e.g. 'Done', 'I sent it').
    Marks matched commitments fulfilled. Returns updated records.
    """
    _RESOLUTION_TOKENS = {
        "done", "completed", "finished", "sent it", "i sent", "i did", "did it",
        "i followed up", "already", "taken care", "resolved", "fixed", "it worked",
    }
    lower = user_message.lower()
    if not any(tok in lower for tok in _RESOLUTION_TOKENS):
        return []

    open_records = (
        db.query(CommitmentRecord)
        .filter(
            CommitmentRecord.tenant_id == tenant_id,
            CommitmentRecord.owner_agent_slug == "luna",
            CommitmentRecord.state.in_(["open", "in_progress"]),
        )
        .order_by(CommitmentRecord.created_at.desc())
        .limit(5)
        .all()
    )

    resolved = []
    # Simple heuristic: if user confirms resolution + there's an open commitment, mark the newest
    if open_records:
        record = open_records[0]
        record.state = "fulfilled"
        record.fulfilled_at = datetime.utcnow()
        resolved.append(record)
        db.commit()
        logger.info(f"Marked commitment {record.id} fulfilled for tenant {tenant_id}")

    return resolved


# ── Helpers ──────────────────────────────────────────────────────────────────

def _parse_commitments(text: str) -> List[Tuple[str, str, str, Optional[datetime]]]:
    """Extract (title, type, priority, due_at) tuples from response text."""
    sentences = re.split(r'(?<=[.!?])\s+', text)
    results = []
    seen = set()

    for sentence in sentences:
        sentence = sentence.strip()
        if len(sentence) < 10 or len(sentence) > 400:
            continue
        lower = sentence.lower()

        for pattern, ctype, priority in _COMMITMENT_PATTERNS:
            if re.search(pattern, lower):
                key = sentence[:60]
                if key in seen:
                    break
                seen.add(key)
                due_at = _extract_due_at(lower)
                title = sentence[:200].rstrip(".!?")
                results.append((title, ctype, priority, due_at))
                break

    return results


def _extract_due_at(text: str) -> Optional[datetime]:
    """Try to extract a due date from time-relative phrases in text."""
    now = datetime.utcnow()

    for pattern, delta in _DUE_AT_PATTERNS:
        if delta is None:
            # Handle "in N hours/days"
            m = re.search(pattern, text)
            if m:
                n = int(m.group(1))
                if "hour" in pattern:
                    return now + timedelta(hours=n)
                else:
                    return now + timedelta(days=n)
        else:
            if re.search(pattern, text):
                return now + delta

    return None
