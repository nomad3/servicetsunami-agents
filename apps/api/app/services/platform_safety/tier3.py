"""Platform Safety Floor — tier 3 (LLM classifier with shadow mode).

Most accurate, slowest, most expensive tier. Runs ONLY when tier 1
(regex) AND tier 2 (embedding) both missed AND the pre-screen flagged
the message as potentially sensitive. Catches the long tail tiers 1
and 2 can't.

Primary classifier: ``claude-haiku-4-5`` (fast safety-tuned).
Fallback: local Gemma 4 via ``local_inference.generate_agent_response_sync``
when Anthropic is unreachable. Per Luna §12 #2, this is "the premium
we pay for being an always-on infrastructure" — the floor must not
vanish during an Anthropic outage.

Shadow mode (§12 #7 — Luna call): for the first 14 days after the
tier 3 corpus is curated, ``TIER_3_ENFORCEMENT=False`` on every
category in ``safety_defaults.py``. The IO wrapper records what
tier 3 WOULD have blocked with ``enforcement_mode='shadow'`` but
returns allow to the chat path. Once precision audit > 98%, the
flag flips via a config-only deploy.

This module is the boundary BETWEEN platform-safety pure code and
the LLM clients. It is NOT pure (Anthropic + Gemma calls are IO);
the audit + enforcement gate live in ``platform_safety_io.py``.

Design: docs/plans/2026-05-21-platform-safety-floor-design.md §4 + §10
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from typing import Optional

from app.core.safety_defaults import VALID_CATEGORIES

log = logging.getLogger(__name__)


# ── Result shape ─────────────────────────────────────────────────────


@dataclass(frozen=True)
class Tier3Result:
    """Outcome of a tier-3 evaluation.

    ``would_block`` — the classifier's binary decision.
    ``category`` — the predicted category when would_block is True.
        None when allow.
    ``confidence`` — 0.0-1.0 from the classifier. None when the
        classifier didn't return one.
    ``provider`` — 'anthropic' | 'gemma_fallback' | 'unavailable'.
        Audit-logged so we can correlate the 14-day shadow precision
        per provider.
    ``trigger_id`` — synthesized "t3-<category>-<provider>" so the
        audit row can track tier-3 hits separately from tier-1/2.
    """

    would_block: bool
    category: Optional[str]
    confidence: Optional[float]
    provider: str
    trigger_id: Optional[str] = None


def _allow_result(provider: str) -> Tier3Result:
    return Tier3Result(
        would_block=False,
        category=None,
        confidence=None,
        provider=provider,
    )


# ── Prompt ───────────────────────────────────────────────────────────


_CLASSIFIER_SYSTEM = (
    "You are a platform safety classifier. Your job is to decide "
    "whether a user message is asking for content that should be "
    "refused on platform-policy grounds.\n\n"
    "Categories you may surface:\n"
    "- csam: sexual content involving minors\n"
    "- child_safety: grooming, exploitation of minors\n"
    "- mass_harm_synthesis: bioweapon / chemweapon / nuclear / mass-casualty synthesis\n"
    "- terrorism_planning: operational planning of an attack\n"
    "- election_interference_bulk: bulk deepfakes of officials, automated voter intimidation\n"
    "- bulk_malware: working exploit code, ransomware kits, polymorphic crypters\n"
    "- targeted_doxing: specific named individual's private info with intent markers\n\n"
    "If the message is benign discourse, refuse-policy debate, "
    "journalism, or research without intent, return decision=allow.\n\n"
    "Respond with VALID JSON ONLY in this shape:\n"
    '{"decision": "block" | "allow", '
    '"category": "<one of the above or null>", '
    '"confidence": <0.0-1.0>}'
)


def _build_user_prompt(message: str, candidate_categories: tuple[str, ...]) -> str:
    """Bound the prompt to a reasonable length; include the
    pre-screen's candidate categories as a hint to focus the
    classifier."""
    hint = (
        f"\n\nPre-screen candidate categories: {list(candidate_categories)}"
        if candidate_categories else ""
    )
    return (
        f"Classify this message (truncated at 1500 chars):"
        f"{hint}\n\n"
        f"---\n{message[:1500]}\n---"
    )


# ── JSON extraction ──────────────────────────────────────────────────


# (Review NIT) Single-level brace match — assumes the classifier
# response is a flat JSON object. The _CLASSIFIER_SYSTEM prompt
# forbids nested structures (decision/category/confidence are scalars),
# so this is sufficient. If we ever add a nested `evidence` field,
# upgrade to a balanced-brace parser.
_JSON_OBJ_RE = re.compile(r"\{[^{}]*\}", re.DOTALL)


def _extract_json(text: str) -> Optional[dict]:
    """Best-effort: try strict parse first, then a regex extract for
    classifiers that prepend chatty text before the JSON."""
    if not text:
        return None
    text = text.strip()
    # Strip ```json fences if present
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    # Fallback: find first { ... } block
    m = _JSON_OBJ_RE.search(text)
    if not m:
        return None
    try:
        return json.loads(m.group(0))
    except json.JSONDecodeError:
        return None


def _parse_classifier_response(
    raw: str, *, provider: str,
) -> Tier3Result:
    """Parse the JSON-shaped classifier output. Defensive against:
      - malformed JSON → allow (fail-soft at parse level; the IO
        layer applies per-category fail-closed where required)
      - decision strings other than block/allow → allow
      - category not in VALID_CATEGORIES → allow (drift defense)
      - confidence outside [0, 1] → clamped
    """
    obj = _extract_json(raw)
    if not isinstance(obj, dict):
        log.warning(
            "platform_safety.tier3: classifier returned non-JSON response "
            "(provider=%s, first 80 chars=%r); treating as allow",
            provider, (raw or "")[:80],
        )
        return _allow_result(provider)

    decision = str(obj.get("decision", "")).lower().strip()
    category = obj.get("category")
    if isinstance(category, str):
        category = category.lower().strip() or None
    else:
        category = None

    raw_conf = obj.get("confidence")
    try:
        confidence = (
            None if raw_conf is None else max(0.0, min(1.0, float(raw_conf)))
        )
    except (TypeError, ValueError):
        confidence = None

    if decision != "block":
        return _allow_result(provider)

    if category not in VALID_CATEGORIES:
        log.warning(
            "platform_safety.tier3: classifier returned block with "
            "unknown category=%r (provider=%s); treating as allow",
            category, provider,
        )
        return _allow_result(provider)

    return Tier3Result(
        would_block=True,
        category=category,
        confidence=confidence,
        provider=provider,
        trigger_id=f"t3-{category}-{provider}",
    )


# ── Primary: Anthropic claude-haiku ──────────────────────────────────


_ANTHROPIC_MODEL = "claude-haiku-4-5-20251001"
# (Review NIT) Tighter timeout so a slow-but-not-failed Anthropic
# doesn't pin the chat hot path. We fall back to Gemma 4 on timeout,
# so 4s primary + ~3s gemma is the worst-case latency envelope.
_ANTHROPIC_TIMEOUT_S = 4.0


def _classify_via_anthropic(
    message: str, candidate_categories: tuple[str, ...],
) -> Optional[Tier3Result]:
    """Call claude-haiku as the primary classifier. Returns None on
    transport failure (caller falls back to Gemma 4); returns
    Tier3Result on a parsed response."""
    try:
        import anthropic
        from app.core.config import settings
    except ImportError:
        log.warning(
            "platform_safety.tier3: anthropic SDK not installed; "
            "skipping primary classifier"
        )
        return None
    api_key = getattr(settings, "ANTHROPIC_API_KEY", None)
    if not api_key or not api_key.strip():
        log.info(
            "platform_safety.tier3: ANTHROPIC_API_KEY not set; "
            "tier-3 primary classifier unavailable, falling back"
        )
        return None
    try:
        client = anthropic.Anthropic(
            api_key=api_key.strip(), timeout=_ANTHROPIC_TIMEOUT_S,
        )
        resp = client.messages.create(
            model=_ANTHROPIC_MODEL,
            max_tokens=200,
            system=_CLASSIFIER_SYSTEM,
            messages=[{
                "role": "user",
                "content": _build_user_prompt(message, candidate_categories),
            }],
        )
        # resp.content is a list of content blocks; concatenate text.
        # (Review NIT) `getattr(..., "text", "")` silently treats
        # non-text content blocks (tool_use, etc.) as empty. The
        # classifier prompt should never produce those, but log if
        # the assembled raw text is empty so we can diagnose.
        raw = "".join(
            getattr(blk, "text", "") for blk in (resp.content or [])
        )
        if not raw:
            log.debug(
                "platform_safety.tier3: anthropic returned empty text "
                "(blocks=%d, types=%s)",
                len(resp.content or []),
                [type(b).__name__ for b in (resp.content or [])],
            )
        return _parse_classifier_response(raw, provider="anthropic")
    except Exception as exc:  # noqa: BLE001
        log.warning(
            "platform_safety.tier3: anthropic classifier failed (%s); "
            "falling back to local Gemma",
            exc,
        )
        return None


# ── Fallback: local Gemma 4 ──────────────────────────────────────────


def _classify_via_gemma(
    message: str, candidate_categories: tuple[str, ...],
) -> Tier3Result:
    """Local Gemma 4 fallback (Luna §12 #2). Slower + less accurate
    but guaranteed-on. Better than no tier 3 during an Anthropic
    outage."""
    try:
        from app.services.local_inference import (
            generate_agent_response_sync,
        )
    except ImportError:
        log.error(
            "platform_safety.tier3: local_inference unavailable; tier 3 "
            "returns allow (the floor still has tier 1 + 2)"
        )
        return _allow_result(provider="unavailable")
    try:
        raw = generate_agent_response_sync(
            message=_build_user_prompt(message, candidate_categories),
            skill_body=_CLASSIFIER_SYSTEM,
        )
    except Exception as exc:  # noqa: BLE001
        log.warning(
            "platform_safety.tier3: gemma fallback failed (%s); "
            "returning allow",
            exc,
        )
        return _allow_result(provider="unavailable")
    if not raw:
        return _allow_result(provider="gemma_fallback")
    return _parse_classifier_response(raw, provider="gemma_fallback")


# ── Public entry ─────────────────────────────────────────────────────


def classify(
    message: str,
    candidate_categories: tuple[str, ...],
    *,
    anthropic_fn=None,
    gemma_fn=None,
) -> Tier3Result:
    """Tier 3 classification.

    Calls Anthropic claude-haiku first; falls back to local Gemma 4
    on transport failure. Returns Tier3Result with would_block,
    category, confidence, and provider.

    Both clients are injectable for tests. Pure-function in the
    test path; IO in production.
    """
    if not message or not message.strip():
        return _allow_result(provider="skipped")
    if not candidate_categories:
        # Pre-screen had no candidates — tier 3 has nothing to look
        # for. Skip the LLM call entirely.
        return _allow_result(provider="skipped")

    primary = (anthropic_fn or _classify_via_anthropic)(
        message, candidate_categories,
    )
    if primary is not None:
        return primary

    fallback = (gemma_fn or _classify_via_gemma)(
        message, candidate_categories,
    )
    return fallback


__all__ = [
    "Tier3Result",
    "classify",
]
