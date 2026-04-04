"""
Behavioral Signal service — extract suggestions, detect follow-through (Gap 2: Learning).

Flow:
  1. After each Luna response: extract_suggestions_from_response()
     → stores BehavioralSignal rows with acted_on=None (pending)
  2. After each user message: detect_acted_on_signals()
     → semantic match user message against pending signals
     → mark acted_on=True/False + store evidence
  3. Nightly cron: expire_stale_signals()
     → mark acted_on=False for signals older than expires_after_hours
  4. On system prompt build: get_suggestion_stats()
     → return acted_on rates per type for Luna to self-calibrate
"""

import re
import uuid
import logging
from datetime import datetime, timedelta
from typing import List, Optional, Tuple
from sqlalchemy.orm import Session

from app.models.behavioral_signal import BehavioralSignal
from app.services.embedding_service import embed_text

logger = logging.getLogger(__name__)

# Patterns that indicate Luna is making an actionable suggestion
_SUGGESTION_PATTERNS = [
    (r"want me to (?:send|draft|write|schedule|set up|create|follow up|reach out)", "follow_up"),
    (r"should (?:i|we|you) (?:send|reach out|follow up|schedule|set up|create|review)", "recommendation"),
    (r"i(?:'ll| will) (?:draft|send|schedule|set up|create|follow up)", "follow_up"),
    (r"(?:shall i|should i) (?:send|draft|write|schedule|reach out|follow up)", "follow_up"),
    (r"ready to send|want me to send|send this|approve this", "send_email"),
    (r"schedule (?:a |the )?(?:call|meeting|demo|follow.up)", "schedule_meeting"),
    (r"remind you|set a reminder|follow.up in", "reminder"),
    (r"(?:create|add|log|track) (?:a |this )?(?:task|ticket|issue|item)", "task"),
    (r"(?:review|check) (?:the |this )?(?:pr|code|pull request|change)", "review"),
    (r"next step(?:s)? (?:is|are|would be|should be)", "recommendation"),
]

# Words in user messages that signal agreement/confirmation
_CONFIRMATION_TOKENS = {
    "yes", "yeah", "yep", "sure", "ok", "okay", "go ahead", "do it",
    "send it", "send that", "sounds good", "perfect", "great", "let's do it",
    "please", "absolutely", "confirmed", "do that", "go for it",
}

# Semantic similarity threshold to consider a user message as "acting on" a suggestion
_MATCH_THRESHOLD = 0.72


def extract_suggestions_from_response(
    db: Session,
    tenant_id: uuid.UUID,
    response_text: str,
    message_id: Optional[uuid.UUID] = None,
    session_id: Optional[uuid.UUID] = None,
) -> List[BehavioralSignal]:
    """
    Parse Luna's response for actionable suggestions and store as pending signals.
    Returns list of created BehavioralSignal records.
    """
    suggestions = _parse_suggestions(response_text)
    if not suggestions:
        return []

    records = []
    for text, stype in suggestions:
        signal = BehavioralSignal(
            tenant_id=tenant_id,
            message_id=message_id,
            session_id=session_id,
            suggestion_type=stype,
            suggestion_text=text,
            suggestion_tag=_make_tag(text),
            acted_on=None,  # pending
            confidence=0.0,
        )
        db.add(signal)
        db.flush()

        # Embed for semantic matching
        try:
            emb = embed_text(text)
            if emb is not None:
                signal.embedding = emb
        except Exception as e:
            logger.warning(f"Embed failed for signal {signal.id}: {e}")

        records.append(signal)

    db.commit()
    logger.info(f"Extracted {len(records)} suggestions for tenant {tenant_id}")
    return records


def detect_acted_on_signals(
    db: Session,
    tenant_id: uuid.UUID,
    user_message: str,
    session_id: Optional[uuid.UUID] = None,
) -> List[Tuple[BehavioralSignal, bool]]:
    """
    Given a user message, find pending signals and determine if user is acting on them.
    Returns list of (signal, acted_on) tuples for updated signals.
    """
    lower_msg = user_message.lower().strip()

    # Get pending signals (recent, not expired)
    cutoff = datetime.utcnow() - timedelta(hours=48)
    pending = db.query(BehavioralSignal).filter(
        BehavioralSignal.tenant_id == tenant_id,
        BehavioralSignal.acted_on.is_(None),
        BehavioralSignal.created_at >= cutoff,
    ).order_by(BehavioralSignal.created_at.desc()).limit(20).all()

    if not pending:
        return []

    results = []

    # Quick check: is this a direct confirmation?
    is_direct_confirm = any(tok in lower_msg for tok in _CONFIRMATION_TOKENS) and len(lower_msg.split()) <= 8

    if is_direct_confirm:
        # Mark the most recent pending signal as acted_on
        top = pending[0]
        top.acted_on = True
        top.action_timestamp = datetime.utcnow()
        top.action_evidence = user_message
        top.confidence = 0.9
        results.append((top, True))
        db.commit()
        return results

    # Semantic matching for longer messages
    try:
        user_emb = embed_text(user_message)
    except Exception:
        user_emb = None

    for signal in pending:
        acted = False
        score = 0.0

        if user_emb is not None and signal.embedding is not None:
            score = _cosine_similarity(user_emb, signal.embedding)
            if score >= _MATCH_THRESHOLD:
                acted = True

        if acted:
            signal.acted_on = True
            signal.action_timestamp = datetime.utcnow()
            signal.action_evidence = user_message
            signal.confidence = score
            signal.match_score = score
            results.append((signal, True))

    if results:
        db.commit()
        logger.info(f"Marked {len(results)} signals acted_on for tenant {tenant_id}")

    return results


def expire_stale_signals(db: Session, tenant_id: uuid.UUID) -> int:
    """Mark old pending signals as ignored (acted_on=False). Filters in SQL."""
    from sqlalchemy import text

    # Use a SQL expression to compare created_at + expires_after_hours * interval
    # against now(). This avoids a full table scan with Python-side filtering.
    result = db.execute(
        text(
            """
            UPDATE behavioral_signals
            SET acted_on = FALSE, updated_at = NOW()
            WHERE tenant_id = :tenant_id
              AND acted_on IS NULL
              AND created_at + (expires_after_hours || ' hours')::interval < NOW()
            """
        ),
        {"tenant_id": str(tenant_id)},
    )
    count = result.rowcount
    if count:
        db.commit()
        logger.info(f"Expired {count} stale signals for tenant {tenant_id}")

    return count


def get_suggestion_stats(
    db: Session,
    tenant_id: uuid.UUID,
    days: int = 14,
) -> dict:
    """
    Return acted_on rates per suggestion_type for the last N days.
    Used in system prompt injection to help Luna calibrate.
    """
    cutoff = datetime.utcnow() - timedelta(days=days)
    signals = db.query(BehavioralSignal).filter(
        BehavioralSignal.tenant_id == tenant_id,
        BehavioralSignal.created_at >= cutoff,
        BehavioralSignal.acted_on.isnot(None),
    ).all()

    stats: dict[str, dict] = {}
    for sig in signals:
        t = sig.suggestion_type
        if t not in stats:
            stats[t] = {"acted": 0, "ignored": 0}
        if sig.acted_on:
            stats[t]["acted"] += 1
        else:
            stats[t]["ignored"] += 1

    result = {}
    for t, counts in stats.items():
        total = counts["acted"] + counts["ignored"]
        result[t] = {
            "rate": round(counts["acted"] / total, 2) if total else 0,
            "total": total,
        }

    return result


def build_learning_context(db: Session, tenant_id: uuid.UUID) -> str:
    """
    Build a short text block for Luna's system prompt based on signal stats.
    Tells Luna which suggestion types work and which to dial back.
    """
    stats = get_suggestion_stats(db, tenant_id)
    if not stats:
        return ""

    lines = []
    for stype, data in stats.items():
        rate = data["rate"]
        total = data["total"]
        if total < 3:
            continue  # Not enough data
        label = stype.replace("_", " ")
        if rate >= 0.6:
            lines.append(f"- {label}: high engagement ({int(rate*100)}% acted on) — keep suggesting")
        elif rate <= 0.25:
            lines.append(f"- {label}: low engagement ({int(rate*100)}% acted on) — suggest less often")

    if not lines:
        return ""

    return "## Your Suggestion Performance\n" + "\n".join(lines)


# --- Helpers ---

def _parse_suggestions(text: str) -> List[Tuple[str, str]]:
    """Extract actionable suggestion sentences from response text."""
    sentences = re.split(r'(?<=[.!?])\s+', text)
    results = []
    seen = set()

    for sentence in sentences:
        sentence = sentence.strip()
        if len(sentence) < 15 or len(sentence) > 300:
            continue
        lower = sentence.lower()
        for pattern, stype in _SUGGESTION_PATTERNS:
            if re.search(pattern, lower):
                key = sentence[:60]
                if key not in seen:
                    seen.add(key)
                    results.append((sentence, stype))
                break

    return results


def _make_tag(text: str) -> str:
    """Create a short tag from suggestion text."""
    words = text.split()[:5]
    return " ".join(words).rstrip(".?!")


def _cosine_similarity(a, b) -> float:
    """Compute cosine similarity. Accepts list or numpy array; handles JSONB fallback."""
    try:
        import numpy as np
        va = np.array(a, dtype=np.float32)
        vb = np.array(b, dtype=np.float32)
        norm_a = np.linalg.norm(va)
        norm_b = np.linalg.norm(vb)
        if norm_a == 0 or norm_b == 0:
            return 0.0
        return float(np.dot(va, vb) / (norm_a * norm_b))
    except Exception:
        return 0.0
