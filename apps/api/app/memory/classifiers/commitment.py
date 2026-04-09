"""Gemma4-based commitment classifier.

Replaces the regex-based commitment_extractor.py (which was disabled
because regex couldn't distinguish "I'll send the report" from
"the report-sending feature"). Uses structured output to force a
JSON response and parse it deterministically.
"""
import json
import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Optional, Literal

from app.services.local_inference import generate_sync, QUALITY_MODEL

logger = logging.getLogger(__name__)


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
        logger.warning("classify_commitment ollama failure: %s", e)
        return CommitmentClassification(is_commitment=False, confidence=0.0)

    if not raw:
        return CommitmentClassification(is_commitment=False, confidence=0.0)

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
