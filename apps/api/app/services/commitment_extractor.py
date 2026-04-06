"""
Commitment extraction — auto-capture Luna's predictions and promises (Gap 3: Stakes).

Identifies sentences where Luna makes implicit or explicit commitments:
- "I'll send you..." → follow_up commitment
- "This should solve..." → prediction commitment
- "Let me check..." → task commitment
- "I recommend..." → recommendation commitment with stakes

Creates CommitmentRecord entries with:
- type = prediction|follow_up|task|recommendation
- state = open (until resolved/broken)
- due_at = inferred or set to next morning briefing time
"""

import re
import uuid
import logging
from datetime import datetime, timedelta
from typing import List, Tuple, Optional
from sqlalchemy.orm import Session

from app.models.commitment_record import CommitmentRecord

logger = logging.getLogger(__name__)

# Patterns that indicate commitment/prediction
_COMMITMENT_PATTERNS = [
    # Direct promises
    (r"i(?:'ll| will) (?:send|draft|write|schedule|reach out|follow up|check|investigate)", "follow_up"),
    (r"(?:let me|let's) (?:send|draft|schedule|check|look into|investigate)", "follow_up"),
    (r"want me to (?:send|draft|schedule|create|follow up)", "follow_up"),

    # Predictions with stakes
    (r"this (?:should|will|ought to|might) (?:solve|fix|help|work|improve)", "prediction"),
    (r"i (?:think|expect|predict|believe) (?:this|it) (?:will|should)", "prediction"),
    (r"that (?:should|will|would) (?:address|resolve|help|improve)", "prediction"),

    # Recommendations
    (r"i (?:recommend|suggest|advise|propose) (?:you|we|that you)", "recommendation"),
    (r"best (?:approach|path|move|next step) (?:would|is) ", "recommendation"),

    # Tasks
    (r"(?:i'll|let me|i need to) (?:create|log|track|add|set up) (?:a|this) (?:task|ticket|reminder)", "task"),
    (r"(?:you should|remember to) (?:follow up|check in|review)", "task"),
]


def extract_commitments_from_response(
    db: Session,
    tenant_id: uuid.UUID,
    response_text: str,
    message_id: Optional[uuid.UUID] = None,
    session_id: Optional[uuid.UUID] = None,
    agent_slug: str = "luna",
) -> List[CommitmentRecord]:
    """Parse Luna's response for commitments/predictions and create CommitmentRecord entries."""
    commitments = _parse_commitments(response_text)
    if not commitments:
        return []

    records = []
    for text, ctype in commitments:
        now = datetime.utcnow()
        next_morning = (now + timedelta(days=1)).replace(hour=8, minute=0, second=0, microsecond=0)

        record = CommitmentRecord(
            tenant_id=tenant_id,
            owner_agent_slug=agent_slug,
            created_by=None,
            title=_extract_title(text),
            description=text,
            commitment_type=ctype,
            state="open",
            priority="normal",
            source_type="response_extraction",
            source_ref={"message_id": str(message_id), "session_id": str(session_id)} if message_id else {},
            due_at=next_morning,
        )
        db.add(record)
        db.flush()
        records.append(record)

    if records:
        db.commit()
        logger.info(f"Extracted {len(records)} commitments for tenant {tenant_id}")

    return records


def build_stakes_context(db: Session, tenant_id: uuid.UUID) -> str:
    """Build stakes context for system prompt. Gap 3 (Stakes) feature."""
    now = datetime.utcnow()
    open_records = db.query(CommitmentRecord).filter(
        CommitmentRecord.tenant_id == tenant_id,
        CommitmentRecord.state == "open",
    ).all()

    if not open_records:
        return ""

    total = len(open_records)
    overdue = sum(1 for r in open_records if r.due_at and r.due_at < now)

    lines = [f"## Your Open Commitments ({total})"]
    if overdue:
        lines.append(f"⚠️ **{overdue} overdue** — check back with user")

    by_type = {}
    for r in open_records:
        t = r.commitment_type or "task"
        by_type[t] = by_type.get(t, 0) + 1

    for ctype, count in sorted(by_type.items()):
        lines.append(f"- {count} {ctype}")

    lines.append("")
    lines.append("Remember: You made these commitments. Follow through. Your word matters.")

    return "\n".join(lines)


def _parse_commitments(text: str) -> List[Tuple[str, str]]:
    """Extract commitment sentences from response text."""
    sentences = re.split(r'(?<=[.!?])\s+', text)
    results = []
    seen = set()

    for sentence in sentences:
        sentence = sentence.strip()
        if len(sentence) < 15 or len(sentence) > 400:
            continue

        lower = sentence.lower()
        for pattern, ctype in _COMMITMENT_PATTERNS:
            if re.search(pattern, lower):
                key = sentence[:60]
                if key not in seen:
                    seen.add(key)
                    results.append((sentence, ctype))
                break

    return results


def _extract_title(text: str) -> str:
    """Extract a short title from commitment text."""
    return text[:100].rstrip(".!?")
