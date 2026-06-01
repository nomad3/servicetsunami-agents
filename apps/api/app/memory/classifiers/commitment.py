"""Gemma4-based commitment classifier.

Replaces the regex-based commitment_extractor.py (which was disabled
because regex couldn't distinguish "I'll send the report" from
"the report-sending feature"). Uses structured output to force a
JSON response and parse it deterministically.
"""
import json
import logging
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Optional, Literal

from app.services.local_inference import generate_sync, QUALITY_MODEL, is_available_sync

logger = logging.getLogger(__name__)

# First-person commitment phrasing for the deterministic fallback (when the LLM
# classifier is unavailable/empty). Intentionally conservative — only obvious
# self-commitments, captured at LOW confidence for the user to confirm/correct.
_COMMIT_RE = re.compile(
    r"\b(i'?ll|i will|i'?m going to|i am going to|i commit to|i promise to|i'?ll send|let me)\b",
    re.IGNORECASE,
)


@dataclass
class CommitmentClassification:
    is_commitment: bool
    title: Optional[str] = None
    due_at: Optional[datetime] = None
    type: Optional[Literal["action", "delivery", "response", "meeting"]] = None
    confidence: float = 0.0
    raw_response: Optional[str] = None


SYSTEM_PROMPT = """You are a binary classifier. Decide whether a single chat message contains a COMMITMENT — a statement where the speaker (user or assistant) commits THEMSELVES OR THE OTHER PARTY to a future action, or confirms a scheduled task/meeting, or issues a DIRECTIVE.

A commitment is:
- "I'll send the report Friday" — first-person promise
- "We need to finalize the budget" — collective obligation
- "Confirmed for 3 PM this Thursday" — schedule confirmation
- "Tengo que terminar el informe" — Spanish obligation
- "Los resultados estarán listos el miércoles" — Spanish promise
- "Can you schedule a call?" — request for action
- "Luna, follow up with Ray" — directive
- "Done I merged the PR, check it" — directive/request for verification
- "We are committed to delivering" — explicit commitment

NOT a commitment:
- "Ray usually sends reports on Fridays" — third-person description
- "The commitment-tracking feature has 47 records" — meta/data/statement of fact
- "I sent the report yesterday" — past tense (already done)
- "What if we shipped on Friday?" — hypothetical/question
- "Would be nice if Luna can send updates" — wishful thinking/non-directive preference

Respond with JSON only:
{"is_commitment": true|false, "title": "<short title or null>", "due_at_iso": "<ISO datetime or null>", "type": "action|delivery|response|meeting|null", "confidence": 0.0-1.0}"""


def classify_commitment(text: str, role: str = "user") -> CommitmentClassification:
    """Run Gemma4 against a single message. Returns a parsed classification."""
    user_prompt = f"[role={role}] {text}"
    # Resilience (2026-06-01, Codex-reviewed): under concurrent load the sync
    # Ollama lock made this return empty -> commitment silently dropped (the E2E
    # gap: "send James the demo script by Wednesday" was never structured). The
    # naive 3×retry is unsafe (each holds the lock ~20s + the activity timeout is
    # 60s and detect_commitment may call this twice/turn). So: ONE bounded retry,
    # only if Ollama is actually UP (transient empty, not an outage).
    raw = None
    for _attempt in (1, 2):
        try:
            raw = generate_sync(
                prompt=user_prompt,
                model=QUALITY_MODEL,  # gemma4 by default
                system=SYSTEM_PROMPT,
                temperature=0.0,
                max_tokens=200,
                timeout=20.0,
                response_format="json",
            )
        except Exception as e:
            logger.warning("classify_commitment ollama failure (attempt %s): %s", _attempt, e)
            raw = None
        if raw or _attempt == 2:
            break
        if not is_available_sync():
            break  # model is down, not contention — don't burn a retry
        logger.info("classify_commitment: empty but Ollama up — one retry")

    if not raw:
        # Deterministic fallback (Luna): rather than silently drop, capture a
        # low-confidence rule-based candidate for obvious first-person commitment
        # phrasing so the durable record exists for the user to confirm/correct.
        return _rule_based_commitment_fallback(text, role)

    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        logger.warning("classify_commitment got non-JSON: %r", raw[:200])
        return CommitmentClassification(is_commitment=False, confidence=0.0, raw_response=raw)

    due_at = None
    if parsed.get("due_at_iso"):
        try:
            # Simple ISO parse, handle Z
            val = parsed["due_at_iso"]
            if val.endswith('Z'):
                val = val[:-1] + '+00:00'
            due_at = datetime.fromisoformat(val)
        except ValueError:
            pass

    return CommitmentClassification(
        is_commitment=bool(parsed.get("is_commitment", False)),
        title=parsed.get("title"),
        due_at=due_at,
        type=parsed.get("type") if parsed.get("type") not in (None, "null") else None,
        confidence=float(parsed.get("confidence", 0.5)),
        raw_response=raw,
    )


def _rule_based_commitment_fallback(text: str, role: str) -> CommitmentClassification:
    """Deterministic, LOW-confidence commitment capture when the LLM classifier
    is unavailable/empty (Luna's review: don't silently drop — persist a candidate
    the user can confirm/correct). Conservative: only obvious first-person
    self-commitments from the USER, no due-date parsing (that's the LLM's job).
    Confidence is capped low so it never outranks a real classification."""
    if role != "user" or not _COMMIT_RE.search(text or ""):
        return CommitmentClassification(is_commitment=False, confidence=0.0)
    snippet = " ".join((text or "").split())[:120]
    logger.info("classify_commitment: LLM unavailable — rule-based fallback captured a candidate")
    return CommitmentClassification(
        is_commitment=True,
        title=snippet,
        due_at=None,
        type="action",
        confidence=0.35,  # low — flagged for user confirmation, never auto-trusted
        raw_response="rule_based_fallback",
    )
