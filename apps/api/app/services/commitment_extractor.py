"""
Commitment extractor — auto-detect predictions & promises in Luna responses (Gap 3: Stakes).

Gap 3 creates accountability:
  1. Luna makes a prediction/commitment (e.g., "This should solve it", "I believe X")
  2. Extract and store as CommitmentRecord with due_at
  3. When due, check back: "Did that fix work?" or "Any progress on X?"
  4. Track fulfillment rate → inject into system prompt as "stakes"

This drives Luna toward making only commitments she can keep, and following through.
"""

import re
import uuid
import logging
from datetime import datetime, timedelta
from typing import List, Optional, Tuple
from sqlalchemy.orm import Session

from app.models.commitment_record import CommitmentRecord

logger = logging.getLogger(__name__)

# Patterns that indicate Luna is making a prediction or commitment
_COMMITMENT_PATTERNS = [
    # Explicit commitments
    (r"i(?:'ll| will) (?:send|draft|write|schedule|set up|create|follow up|reach out|check on|monitor)", "action_promised"),
    (r"(?:i'll|i will|let me|i'm going to) (?:help|assist|work on|focus on|tackle)", "action_promised"),

    # Predictions/confidence statements
    (r"(?:this|that|it) should (?:solve|fix|help|work|improve|resolve)", "prediction"),
    (r"(?:this|that|it) will (?:likely|probably|definitely) (?:work|help|improve|solve|fix)", "prediction"),
    (r"i (?:think|believe|expect|predict|anticipate) (?:this|that|it) (?:will|would|should)", "prediction"),
    (r"(?:my|the) prediction is|(?:i|we) should see", "prediction"),

    # Time-bound claims
    (r"(?:within|in|by) (?:a few|the next|this) (?:days?|weeks?|hours?|minutes?)", "time_bound"),
    (r"(?:today|tomorrow|this week|next week|within \d+ days?)", "time_bound"),
]

# Default due date if Luna doesn't specify
_DEFAULT_DUE_HOURS = 72  # 3 days


def extract_commitments_from_response(
    db: Session,
    tenant_id: uuid.UUID,
    response_text: str,
    message_id: Optional[uuid.UUID] = None,
    session_id: Optional[uuid.UUID] = None,
) -> List[CommitmentRecord]:
    """
    Parse Luna's response for commitments/predictions and auto-create records.
    Returns list of created CommitmentRecord entries.
    """
    commitments = _parse_commitments(response_text)
    if not commitments:
        return []

    records = []
    for text, ctype, due_delta_hours in commitments:
        due_at = datetime.utcnow() + timedelta(hours=due_delta_hours)

        record = CommitmentRecord(
            tenant_id=tenant_id,
            owner_agent_slug="luna",
            title=_make_title(text),
            description=text,
            commitment_type=ctype,
            state="open",
            priority="normal",
            source_type="chat_response",
            source_ref={
                "message_id": str(message_id) if message_id else None,
                "session_id": str(session_id) if session_id else None,
            },
            due_at=due_at,
        )
        db.add(record)
        db.flush()
        records.append(record)

    if records:
        db.commit()
        logger.info(f"Extracted {len(records)} commitments for tenant {tenant_id}")

    return records


def build_stakes_context(db: Session, tenant_id: uuid.UUID) -> str:
    """
    Build context about Luna's open commitments for system prompt.
    Tells Luna how many promises are open/overdue and her fulfillment rate.
    """
    # Count commitments by state
    open_count = db.query(CommitmentRecord).filter(
        CommitmentRecord.tenant_id == tenant_id,
        CommitmentRecord.state == "open",
    ).count()

    overdue = db.query(CommitmentRecord).filter(
        CommitmentRecord.tenant_id == tenant_id,
        CommitmentRecord.state == "open",
        CommitmentRecord.due_at < datetime.utcnow(),
    ).count()

    # Fulfillment rate (last 30 days)
    cutoff = datetime.utcnow() - timedelta(days=30)
    recent = db.query(CommitmentRecord).filter(
        CommitmentRecord.tenant_id == tenant_id,
        CommitmentRecord.created_at >= cutoff,
    ).all()

    if not recent:
        return ""

    fulfilled = sum(1 for c in recent if c.state == "fulfilled")
    broken = sum(1 for c in recent if c.state == "broken")
    total = len(recent)

    if total == 0:
        return ""

    fulfillment_rate = fulfilled / total if total else 0
    broken_rate = broken / total if total else 0

    lines = []
    if open_count > 0:
        lines.append(f"You have {open_count} open commitments")
        if overdue > 0:
            lines.append(f"  ⚠ {overdue} are overdue — check on them")

    if total >= 3:  # Only show if enough data
        lines.append(f"\nYour track record (last 30 days):")
        lines.append(f"  Fulfilled: {fulfilled}/{total} ({int(fulfillment_rate*100)}%)")
        if broken > 0:
            lines.append(f"  Broken: {broken}/{total} ({int(broken_rate*100)}%)")

    if lines:
        return "## Your Commitments & Stakes\n" + "\n".join(lines) + "\n\nOnly make commitments you can keep. Follow up proactively."
    return ""


def resolve_commitment_from_message(db: Session, tenant_id: uuid.UUID, user_message: str) -> int:
    """Alias for maybe_resolve_commitments — preferred name for chat.py wiring."""
    return maybe_resolve_commitments(db, tenant_id, user_message)


def maybe_resolve_commitments(db: Session, tenant_id: uuid.UUID, user_message: str) -> int:
    """
    Check if user message resolves any open commitments.
    For example, if Luna said "I'll fix the auth bug", and user says "it works now",
    mark the commitment as fulfilled.

    Returns count of commitments marked as fulfilled.
    """
    # Get recent open commitments
    cutoff = datetime.utcnow() - timedelta(days=14)
    open_commits = db.query(CommitmentRecord).filter(
        CommitmentRecord.tenant_id == tenant_id,
        CommitmentRecord.state == "open",
        CommitmentRecord.created_at >= cutoff,
    ).order_by(CommitmentRecord.created_at.desc()).limit(10).all()

    if not open_commits:
        return 0

    lower_msg = user_message.lower()

    # Token-based quick check: "works", "fixed", "done", "solved", "thanks", etc.
    resolution_tokens = {"works", "fixed", "done", "solved", "thanks", "great", "perfect", "fantastic"}
    if any(tok in lower_msg for tok in resolution_tokens):
        # Optimistically mark the most recent commitment as fulfilled
        if open_commits:
            top = open_commits[0]
            top.state = "fulfilled"
            top.fulfilled_at = datetime.utcnow()
            db.commit()
            logger.info(f"Marked commitment {top.id} as fulfilled")
            return 1

    return 0


def get_commitment_stats(db: Session, tenant_id: uuid.UUID, days: int = 30) -> dict:
    """Get fulfillment stats for a tenant."""
    cutoff = datetime.utcnow() - timedelta(days=days)
    all_commits = db.query(CommitmentRecord).filter(
        CommitmentRecord.tenant_id == tenant_id,
        CommitmentRecord.created_at >= cutoff,
    ).all()

    if not all_commits:
        return {}

    fulfilled = sum(1 for c in all_commits if c.state == "fulfilled")
    broken = sum(1 for c in all_commits if c.state == "broken")
    open_count = sum(1 for c in all_commits if c.state == "open")
    total = len(all_commits)

    return {
        "total": total,
        "fulfilled": fulfilled,
        "broken": broken,
        "open": open_count,
        "fulfillment_rate": round(fulfilled / total, 2) if total else 0,
        "broken_rate": round(broken / total, 2) if total else 0,
    }


# --- Helpers ---

def _parse_commitments(text: str) -> List[Tuple[str, str, int]]:
    """
    Extract commitment sentences from response text.
    Returns list of (text, commitment_type, due_hours) tuples.
    """
    sentences = re.split(r'(?<=[.!?])\s+', text)
    results = []
    seen = set()

    for sentence in sentences:
        sentence = sentence.strip()
        if len(sentence) < 15 or len(sentence) > 300:
            continue

        lower = sentence.lower()
        matched_type = None
        due_hours = _DEFAULT_DUE_HOURS

        for pattern, ctype in _COMMITMENT_PATTERNS:
            if re.search(pattern, lower):
                matched_type = ctype
                break

        if not matched_type:
            continue

        # Extract time from sentence if present
        time_match = re.search(
            r'(?:in|within|by|this|next|within the next|in the next)\s+(\d+)\s+(hours?|days?|weeks?)',
            lower
        )
        if time_match:
            value = int(time_match.group(1))
            unit = time_match.group(2).lower()
            if 'hour' in unit:
                due_hours = value
            elif 'day' in unit:
                due_hours = value * 24
            elif 'week' in unit:
                due_hours = value * 24 * 7

        key = sentence[:60]
        if key not in seen:
            seen.add(key)
            results.append((sentence, matched_type, due_hours))

    return results


def _make_title(text: str) -> str:
    """Create a short title from commitment text."""
    words = text.split()[:8]
    title = " ".join(words).rstrip(".?!")
    return title[:100]
