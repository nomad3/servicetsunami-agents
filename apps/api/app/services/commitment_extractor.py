"""
Commitment extraction and stakes context — Gap 3 (Learning: Stakes).

Automatically extracts commitments/predictions from Luna's responses:
- "I'll send you a draft by tomorrow"
- "This should solve the problem"
- "Let me follow up with John this week"

Stores as CommitmentRecord → tracks whether Luna follows through.
Feeds back into system prompt: "You made 5 commitments, 4 fulfilled, 1 broken"
"""

import re
import uuid
import logging
from datetime import datetime, timedelta
from typing import List, Tuple, Optional
from sqlalchemy.orm import Session

from app.models.commitment_record import CommitmentRecord

logger = logging.getLogger(__name__)

# Patterns that indicate Luna is making a commitment/prediction
_COMMITMENT_PATTERNS = [
    # Explicit promises
    (r"i'll (?:send|draft|write|schedule|create|follow up|reach out)", "follow_up_action"),
    (r"i will (?:send|draft|schedule|create|follow up|reach out)", "follow_up_action"),
    (r"let me (?:send|draft|schedule|create|follow up)", "follow_up_action"),
    (r"(?:i|we) should (?:follow up|check in|reconnect)", "follow_up_action"),

    # Predictions/claims about impact
    (r"this (?:should|will|might) (?:solve|fix|help|improve|address|resolve)", "prediction"),
    (r"that (?:should|will|might) (?:work|help|improve)", "prediction"),
    (r"this is likely to (?:help|improve|work)", "prediction"),
    (r"(?:you|they) (?:should|will|might) see (?:improvement|results|change)", "prediction"),

    # Specific timeframes (stakes)
    (r"(?:by|in|within|before|after) (?:tomorrow|today|next|this (?:week|month|friday))", "timed_commitment"),
    (r"(?:in|within) \d+ (?:hours?|days?|weeks?)", "timed_commitment"),
]


def extract_commitments_from_response(
    db: Session,
    tenant_id: uuid.UUID,
    response_text: str,
    message_id: Optional[uuid.UUID] = None,
    agent_slug: str = "luna",
) -> List[CommitmentRecord]:
    """Parse Luna's response for commitments/predictions and store as CommitmentRecord."""
    commitments = _parse_commitments(response_text)
    if not commitments:
        return []

    records = []
    for text, ctype in commitments:
        due_at = _extract_due_date(text)

        commitment = CommitmentRecord(
            tenant_id=tenant_id,
            owner_agent_slug=agent_slug,
            title=_make_title(text),
            description=text,
            commitment_type=ctype,
            state="open",
            priority="normal",
            source_type="chat_response",
            source_ref={"message_id": str(message_id)} if message_id else {},
            due_at=due_at,
        )
        db.add(commitment)
        db.flush()
        records.append(commitment)

    db.commit()
    logger.info(f"Extracted {len(records)} commitments for tenant {tenant_id}")
    return records


def build_stakes_context(db: Session, tenant_id: uuid.UUID) -> str:
    """Build stakes context for system prompt."""
    cutoff = datetime.utcnow() - timedelta(days=30)

    open_count = db.query(CommitmentRecord).filter(
        CommitmentRecord.tenant_id == tenant_id,
        CommitmentRecord.state == "open",
    ).count()

    fulfilled_count = db.query(CommitmentRecord).filter(
        CommitmentRecord.tenant_id == tenant_id,
        CommitmentRecord.state == "fulfilled",
        CommitmentRecord.fulfilled_at >= cutoff,
    ).count()

    broken_count = db.query(CommitmentRecord).filter(
        CommitmentRecord.tenant_id == tenant_id,
        CommitmentRecord.state == "broken",
        CommitmentRecord.broken_at >= cutoff,
    ).count()

    overdue_count = db.query(CommitmentRecord).filter(
        CommitmentRecord.tenant_id == tenant_id,
        CommitmentRecord.state == "open",
        CommitmentRecord.due_at < datetime.utcnow(),
    ).count()

    if open_count == 0 and fulfilled_count == 0 and broken_count == 0:
        return ""

    lines = ["## Your Commitments & Stakes"]

    if open_count > 0:
        lines.append(f"**{open_count} open** commitment{'s' if open_count != 1 else ''}")
        if overdue_count > 0:
            lines.append(f"  ⚠️ {overdue_count} overdue — address these first")

    if fulfilled_count > 0 or broken_count > 0:
        total_resolved = fulfilled_count + broken_count
        rate = round(100 * fulfilled_count / total_resolved) if total_resolved else 0
        lines.append(f"**{rate}% follow-through** ({fulfilled_count} fulfilled, {broken_count} broken in last 30d)")

    if broken_count > 0:
        lines.append(f"  ⚠️ {broken_count} broken → be more cautious with future commitments")

    lines.append("\nMention upcoming commitments naturally in conversation. Check back when due.")

    return "\n".join(lines)


def get_open_commitments(
    db: Session,
    tenant_id: uuid.UUID,
    agent_slug: str = "luna",
    limit: int = 10,
) -> List[CommitmentRecord]:
    """Get open commitments for Luna's morning briefing."""
    return db.query(CommitmentRecord).filter(
        CommitmentRecord.tenant_id == tenant_id,
        CommitmentRecord.owner_agent_slug == agent_slug,
        CommitmentRecord.state == "open",
    ).order_by(CommitmentRecord.due_at).limit(limit).all()


def get_overdue_commitments(
    db: Session,
    tenant_id: uuid.UUID,
    agent_slug: str = "luna",
) -> List[CommitmentRecord]:
    """Get overdue commitments that need urgent attention."""
    return db.query(CommitmentRecord).filter(
        CommitmentRecord.tenant_id == tenant_id,
        CommitmentRecord.owner_agent_slug == agent_slug,
        CommitmentRecord.state == "open",
        CommitmentRecord.due_at < datetime.utcnow(),
    ).all()


# --- Helpers ---

def _parse_commitments(text: str) -> List[Tuple[str, str]]:
    """Extract commitment sentences from response text."""
    sentences = re.split(r'(?<=[.!?])\s+', text)
    results = []
    seen = set()

    for sentence in sentences:
        sentence = sentence.strip()
        if len(sentence) < 10 or len(sentence) > 300:
            continue

        lower = sentence.lower()
        for pattern, ctype in _COMMITMENT_PATTERNS:
            if re.search(pattern, lower):
                key = sentence[:80]
                if key not in seen:
                    seen.add(key)
                    results.append((sentence, ctype))
                break

    return results


def _extract_due_date(text: str) -> Optional[datetime]:
    """Try to extract a due date from commitment text."""
    lower = text.lower()
    
    # Simple pattern matching for common time expressions
    if "tomorrow" in lower:
        return (datetime.utcnow() + timedelta(days=1)).replace(hour=17, minute=0, second=0)
    if "today" in lower:
        return datetime.utcnow().replace(hour=17, minute=0, second=0)
    if "this week" in lower:
        days_left = 7 - datetime.utcnow().weekday()
        return (datetime.utcnow() + timedelta(days=days_left)).replace(hour=17, minute=0, second=0)
    
    # Parse "in N hours/days"
    match = re.search(r'in (\d+) hours?', lower)
    if match:
        return datetime.utcnow() + timedelta(hours=int(match.group(1)))
    
    match = re.search(r'in (\d+) days?', lower)
    if match:
        return datetime.utcnow() + timedelta(days=int(match.group(1)))

    return None


def _make_title(text: str) -> str:
    """Create a short title from commitment text."""
    words = text.split()[:10]
    title = " ".join(words).rstrip(".?!,")
    return title[:100]
