"""
Commitment extractor — parse Luna's responses for predictions & stakes (Gap 3: Learning).

Extracts three types of commitments:
1. **Predictions**: "I think X will happen", "This should work", "You'll see improvement"
2. **Promises**: "I'll do X", "I will send that", "I'll follow up"
3. **Stakes**: "Let's check back in 3 days", "I'm confident this will...", "If X doesn't happen..."

Flow:
  1. Luna response processed → extract_commitments_from_response()
  2. Creates CommitmentRecord entries with due_at inferred from context
  3. Stats aggregated in build_stakes_context() for system prompt injection
"""

import re
import uuid
import logging
from datetime import datetime, timedelta
from typing import List, Optional, Tuple
from sqlalchemy.orm import Session

from app.models.commitment_record import CommitmentRecord

logger = logging.getLogger(__name__)

# Patterns for extracting commitments.
# commitment_type values MUST match CommitmentType enum:
#   action, followup, delivery, notification, prediction
_COMMITMENT_PATTERNS = [
    # Predictions: "I think/believe", "should", "will likely"
    (
        r"(?:i think|i believe|i expect|my guess is|likely|probably|i'm confident|should|will likely).*?(?:[.!?]|$)",
        "prediction",
        None,  # No fixed due date for predictions
    ),
    # Near-term action promises: "I'll", "I will", "I'm going to"
    (
        r"(?:i'?ll|i will|i'm going to|i'm gonna|let me|want me to).*?(?:send|draft|write|create|schedule|set up|follow up|reach out).*?(?:[.!?]|$)",
        "action",
        1,  # 1 day default
    ),
    # Follow-ups: "check back", "let's revisit", "in X days"
    (
        r"(?:check back|let'?s revisit|let'?s follow up|in \d+ (?:day|week|hour)s?).*?(?:[.!?]|$)",
        "followup",
        None,
    ),
    # High-confidence / delivery commitments
    (
        r"(?:i'm betting|i'm confident|i guarantee|if this doesn't|i'll deliver|i'll have it ready).*?(?:[.!?]|$)",
        "delivery",
        None,
    ),
    # Notification: "I'll let you know", "I'll keep you posted"
    (
        r"(?:i'?ll|i will) (?:let you know|keep you posted|update you|notify you).*?(?:[.!?]|$)",
        "notification",
        None,
    ),
]

# Keywords that indicate implicit due dates
_DUE_DATE_KEYWORDS = {
    "today": 0,
    "tomorrow": 1,
    "next week": 7,
    "next month": 30,
    "soon": 3,
    "asap": 0,
    "immediately": 0,
    "in 2 days": 2,
    "in 3 days": 3,
    "in a week": 7,
    "in a month": 30,
}


def extract_commitments_from_response(
    db: Session,
    tenant_id: uuid.UUID,
    response_text: str,
    user_id: Optional[uuid.UUID] = None,
    message_id: Optional[uuid.UUID] = None,
    session_id: Optional[uuid.UUID] = None,
) -> List[CommitmentRecord]:
    """Parse Luna's response for commitments and create CommitmentRecord entries."""
    commitments = _parse_commitments(response_text)
    if not commitments:
        return []

    records = []
    for text, ctype, implicit_due_days in commitments:
        # Calculate due_at if implicit due date found
        due_at = None
        if implicit_due_days is not None:
            due_at = datetime.utcnow() + timedelta(days=implicit_due_days)

        commitment = CommitmentRecord(
            tenant_id=tenant_id,
            owner_agent_slug="luna",
            created_by=user_id,
            title=_make_title(text),
            description=text,
            commitment_type=ctype,
            state="open",
            priority="normal",
            source_type="chat",
            source_ref={
                "message_id": str(message_id) if message_id else None,
                "session_id": str(session_id) if session_id else None,
            },
            due_at=due_at,
        )
        db.add(commitment)
        records.append(commitment)

    if records:
        try:
            db.commit()
        except Exception:
            db.rollback()
            raise
        logger.info("Extracted %d commitments for tenant %s", len(records), str(tenant_id)[:8])

    return records


def build_stakes_context(db: Session, tenant_id: uuid.UUID) -> str:
    """Build stakes context for system prompt (Gap 3: Stakes/Accountability)."""
    from app.services import commitment_service
    open_commitments = commitment_service.list_open_commitments_for_agent(
        db, tenant_id=tenant_id, agent_slug="luna"
    )

    if not open_commitments:
        return ""

    now = datetime.utcnow()
    overdue = [c for c in open_commitments if c.due_at and c.due_at < now]
    due_soon = [c for c in open_commitments if c.due_at and now <= c.due_at < now + timedelta(days=2)]

    lines = []
    lines.append("## Your Open Commitments (Stakes)")
    lines.append(f"You have {len(open_commitments)} open commitment(s) to track:")

    if overdue:
        lines.append(f"\n**⚠️ OVERDUE ({len(overdue)}):**")
        for c in overdue[:3]:
            days_past = (now - c.due_at).days
            lines.append(f"  - {c.title} (due {days_past} days ago)")

    if due_soon:
        lines.append(f"\n**📍 DUE SOON ({len(due_soon)}):**")
        for c in due_soon:
            days_left = (c.due_at - now).days
            lines.append(f"  - {c.title} (due in {days_left} days)")

    other = [c for c in open_commitments if c not in overdue and c not in due_soon]
    if other:
        lines.append(f"\n**📋 OPEN ({len(other)}):**")
        for c in other[:3]:
            if c.due_at:
                days_left = (c.due_at - now).days
                lines.append(f"  - {c.title} (due in {days_left} days)")
            else:
                lines.append(f"  - {c.title}")

    lines.append("\nRemember: follow through. It matters.")
    return "\n".join(lines)


def _parse_commitments(text: str) -> List[Tuple[str, str, Optional[int]]]:
    """Extract commitment sentences from response."""
    sentences = re.split(r'(?<=[.!?])\s+', text)
    results = []
    seen = set()

    for sentence in sentences:
        sentence = sentence.strip()
        if len(sentence) < 10 or len(sentence) > 300:
            continue

        lower = sentence.lower()

        for pattern, ctype, default_due in _COMMITMENT_PATTERNS:
            if re.search(pattern, lower, re.IGNORECASE):
                key = sentence[:60]
                if key not in seen:
                    seen.add(key)
                    implicit_due = default_due
                    for keyword, days in _DUE_DATE_KEYWORDS.items():
                        if keyword in lower:
                            implicit_due = days
                            break
                    results.append((sentence, ctype, implicit_due))
                break

    return results


def maybe_resolve_commitments(
    db: Session,
    tenant_id: uuid.UUID,
    user_message: str,
) -> int:
    """
    Scan open commitments — if user message implies resolution, mark fulfilled.
    Returns count of resolved commitments.

    Heuristic: looks for explicit confirmation language ("done", "sent it",
    "followed up", "created", etc.) near the commitment keyword.
    """
    _RESOLVED_TOKENS = {
        "done", "sent", "sent it", "sent that", "followed up", "created",
        "scheduled", "set up", "finished", "complete", "completed", "fixed",
        "resolved", "closed", "shipped", "deployed", "written", "drafted",
        "just did", "just sent", "just created", "just scheduled",
    }
    lower_msg = user_message.lower()
    if not any(tok in lower_msg for tok in _RESOLVED_TOKENS):
        return 0

    open_records = db.query(CommitmentRecord).filter(
        CommitmentRecord.tenant_id == tenant_id,
        CommitmentRecord.state == "open",
        CommitmentRecord.commitment_type != "prediction",
    ).order_by(CommitmentRecord.created_at.desc()).limit(10).all()

    if not open_records:
        return 0

    count = 0
    for record in open_records:
        # Check if any key word from the commitment title appears in the user message
        title_words = set(record.title.lower().split())
        significant = {w for w in title_words if len(w) > 4}
        if significant and significant & set(lower_msg.split()):
            record.state = "fulfilled"
            record.fulfilled_at = datetime.utcnow()
            count += 1

    if count:
        db.commit()
        logger.info("Resolved %d commitment(s) for tenant %s", count, str(tenant_id)[:8])

    return count


def _make_title(text: str) -> str:
    """Create a short title from commitment text."""
    text = re.sub(r'[*_`]', '', text)
    text = re.sub(r'\s+', ' ', text).strip()
    if len(text) > 100:
        text = text[:97] + "..."
    return text
