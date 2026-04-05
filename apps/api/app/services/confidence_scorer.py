"""
Confidence Scorer — Gap 4 (Uncertainty Signaling).

Scores Luna's responses for confidence level and injects hedging
language when confidence is low. Prevents false certainty.

The scorer uses lightweight heuristics (no LLM call) to estimate
confidence based on:
  - Presence of uncertainty language in the response
  - Whether assertions can be backed by knowledge graph
  - Whether response refers to time-sensitive data
  - Whether the question domain is ambiguous or speculative

When confidence < threshold, the system prompt instructs Luna to
signal uncertainty naturally ("I think...", "You may want to verify...",
"I'm not 100% certain but...").
"""

import re
import logging
from typing import Optional

logger = logging.getLogger(__name__)

# Phrases Luna uses when she IS confident (calibration anchors)
_CONFIDENT_PHRASES = [
    r"based on (?:the|your|our)",
    r"according to",
    r"i (?:can see|found|checked|confirmed|verified)",
    r"the (?:data|records?|history) (?:show|indicate|confirm)",
    r"i just (?:ran|checked|searched|looked)",
]

# Phrases that flag the response is speculative/uncertain
_UNCERTAIN_PHRASES = [
    r"i(?:'m| am) not (?:sure|certain|100%)",
    r"i think|i believe|i suspect|i assume",
    r"(?:might|may|could|should) be",
    r"(?:probably|likely|possibly|perhaps|maybe)",
    r"(?:not sure|unclear|hard to say|difficult to tell)",
    r"(?:my guess|my understanding|as far as i know)",
    r"(?:i don't have|i lack|without (?:more|further))",
    r"(?:check|verify|confirm) (?:with|that|if|whether)",
    r"you (?:may|might|should) want to (?:verify|check|confirm)",
]

# Topics that are inherently uncertain (time-sensitive or speculative)
_UNCERTAIN_TOPICS = [
    r"(?:stock|price|market|rate|exchange)",
    r"(?:weather|forecast|predict)",
    r"(?:will happen|in the future|going forward)",
    r"(?:i haven't|haven't checked|haven't looked)",
]

# Threshold: responses below this get a hedging instruction added
CONFIDENCE_THRESHOLD = 0.55


def score_response_confidence(response_text: str, question: str = "") -> float:
    """
    Estimate confidence of a response using heuristic pattern matching.
    Returns a float 0.0–1.0.

    High confidence (>0.7): factual, data-backed, confirmed
    Medium (0.55–0.7): reasonable but not verified
    Low (<0.55): speculative, uncertain, or time-sensitive
    """
    text_lower = response_text.lower()
    question_lower = question.lower()
    combined = text_lower + " " + question_lower

    score = 0.65  # Default: neutral-optimistic

    # Boost for confident anchors
    confident_hits = sum(
        1 for p in _CONFIDENT_PHRASES if re.search(p, text_lower)
    )
    score += confident_hits * 0.08

    # Penalise for uncertain phrases
    uncertain_hits = sum(
        1 for p in _UNCERTAIN_PHRASES if re.search(p, text_lower)
    )
    score -= uncertain_hits * 0.10

    # Penalise for uncertain topics
    topic_hits = sum(
        1 for p in _UNCERTAIN_TOPICS if re.search(p, combined)
    )
    score -= topic_hits * 0.12

    # Penalise for very short responses to complex questions (likely guessing)
    if len(question) > 80 and len(response_text) < 100:
        score -= 0.15

    # Clamp to [0.0, 1.0]
    return max(0.0, min(1.0, score))


def build_uncertainty_instruction(confidence: float) -> str:
    """
    Build a system prompt instruction based on confidence level.
    Only returns content when confidence is below threshold.
    """
    if confidence >= CONFIDENCE_THRESHOLD:
        return ""

    if confidence < 0.3:
        return (
            "## Confidence Advisory\n"
            "Your confidence on this topic is LOW. Be explicit about uncertainty — "
            "say 'I'm not sure about this, but...' or 'You may want to verify this.' "
            "Don't present guesses as facts."
        )
    else:
        return (
            "## Confidence Advisory\n"
            "You're moderately uncertain here. Use natural hedging language like "
            "'I think...', 'This might...', or 'Worth double-checking, but...'. "
            "Don't over-hedge — one signal is enough."
        )


def inject_uncertainty_context(system_prompt: str, confidence: float) -> str:
    """Append uncertainty instruction to system prompt if confidence is low."""
    instruction = build_uncertainty_instruction(confidence)
    if not instruction:
        return system_prompt
    return system_prompt + f"\n\n{instruction}\n"
