"""User-signal classifier — extracts a PAD impulse from a single user turn.

Per Luna's 2026-05-20 read on the affect-backfill design: the
classifier output lives behind a controlled boundary
(``appraise_user_signal`` in ``emotion_engine``) with hard-bounded
impulse magnitudes. The classifier itself just produces a PAD-shaped
estimate in [-1, 1]; the gain constants in ``emotion_engine.py`` then
scale that into a small per-event delta. This keeps the
constitutive-vs-performative defence the design doc § Open questions
§5 documents: raw user text never directly mutates PAD; only
``classifier_output × small_gain`` does.

Two backends, selected via the ``backend`` kwarg or
``USER_SIGNAL_BACKEND`` env var:

  - ``"ollama"`` (default): calls ``local_inference.generate`` with a
    JSON-structured prompt. Used for live + production backfills.
    Latency budget: ~1s on the M4 GPU per call. Backfill loops should
    expect 855 turns × ~1s = ~15 minutes.
  - ``"heuristic"`` (fallback): cheap text-feature math. Always
    available regardless of Ollama state. Used for tests, CI, and
    as the safety-net when the Ollama call times out / 5xx.

Both backends produce the same shape:

  PADClassifierResult(pleasure: float, arousal: float, dominance: float)

each component in [-1, 1]. Out-of-range outputs from the LLM are
clamped; malformed JSON falls back to the heuristic.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
from dataclasses import dataclass
from typing import Optional

logger = logging.getLogger(__name__)


# ── Result shape ──────────────────────────────────────────────────────


@dataclass(frozen=True)
class PADClassifierResult:
    """A PAD estimate of the user's emotional state in [-1, 1]^3.

    NOT a PADVector — those carry server-internal agent state. This is
    an *external* observation about a user turn that
    ``appraise_user_signal`` consumes via its gain constants.
    """

    pleasure: float
    arousal: float
    dominance: float

    def __post_init__(self) -> None:
        for axis, value in (
            ("pleasure", self.pleasure),
            ("arousal", self.arousal),
            ("dominance", self.dominance),
        ):
            if not isinstance(value, (int, float)):
                raise TypeError(f"{axis} must be numeric, got {type(value).__name__}")
            if not -1.0 <= float(value) <= 1.0:
                raise ValueError(
                    f"{axis} must be in [-1.0, 1.0], got {value}"
                )

    def to_dict(self) -> dict:
        return {
            "pleasure": self.pleasure,
            "arousal": self.arousal,
            "dominance": self.dominance,
        }


def _clamp(value: float) -> float:
    if value < -1.0:
        return -1.0
    if value > 1.0:
        return 1.0
    return float(value)


# ── Heuristic backend ─────────────────────────────────────────────────
#
# Cheap text-feature classifier. Used for tests + safety net when the
# LLM backend fails. Not high-accuracy — but consistent + deterministic
# + zero-latency.

# Phrase lists curated to fit the user-talking-to-an-AI register, not
# generic English sentiment. "It's not working" should land NEGATIVE
# (pleasure ↓, arousal ↑) even though it lacks profanity.
_NEG_PHRASES = (
    "doesn't work", "not working", "didn't work", "broken", "broke",
    "fail", "failed", "wrong", "error", "stuck", "hang",
    "frustrat", "annoying", "useless", "terrible", "awful",
    "no good", "garbage", "ugh", "wtf", "fuck", "shit", "damn",
    "why isn't", "why doesn't", "why won't", "why is",
    "still", "again",  # repetition usually means escalation
)
_POS_PHRASES = (
    "thank", "thx", "thanks", "ty", "appreciate",
    "great", "good", "nice", "perfect", "excellent",
    "love", "awesome", "amazing", "fantastic",
    "works", "working", "fixed", "solved", "got it",
    "exactly", "yes!", "yep", "cool",
)
_COMMAND_STARTS = (
    "do ", "go ", "fix ", "make ", "show ", "give ", "open ",
    "create ", "build ", "run ", "stop ", "start ", "delete ",
    "kill ", "pause ", "resume ", "deploy ", "merge ", "push ",
)
_HEDGE_PHRASES = (
    "maybe", "perhaps", "could you", "would you", "if possible",
    "sorry", "not sure", "any chance", "is it ok",
)


def classify_heuristic(text: str) -> PADClassifierResult:
    """Text-feature PAD estimator. Always returns a result."""
    if not text or not text.strip():
        return PADClassifierResult(0.0, 0.0, 0.0)

    lowered = text.lower()
    raw = text

    pos_hits = sum(1 for p in _POS_PHRASES if p in lowered)
    neg_hits = sum(1 for p in _NEG_PHRASES if p in lowered)
    # Pleasure: negative phrases pull down harder than positive pull up
    # (matches the design's failure-asymmetry — TOOL_FAILURE_PLEASURE_LOSS
    # > TOOL_OUTCOME_PLEASURE_GAIN).
    pleasure = _clamp(0.2 * pos_hits - 0.3 * neg_hits)

    exclaim = raw.count("!")
    qmark = raw.count("?")
    caps_ratio = (
        sum(1 for c in raw if c.isupper()) / max(1, sum(1 for c in raw if c.isalpha()))
    )
    # Arousal: punctuation + caps + repetition-marker words bump arousal.
    arousal = _clamp(
        0.25 * min(exclaim, 4)
        + 0.15 * min(qmark, 3)
        + 0.7 * (caps_ratio - 0.3 if caps_ratio > 0.3 else 0.0)
        + (0.2 if any(p in lowered for p in ("still", "again", "now", "asap")) else 0.0)
    )

    has_command = any(lowered.startswith(c) for c in _COMMAND_STARTS)
    hedge_hits = sum(1 for p in _HEDGE_PHRASES if p in lowered)
    # Dominance: imperative openings up; hedging and question marks down.
    dominance = _clamp(
        (0.4 if has_command else 0.0)
        - 0.15 * hedge_hits
        - 0.1 * min(qmark, 3)
    )

    return PADClassifierResult(pleasure, arousal, dominance)


# ── Ollama backend ────────────────────────────────────────────────────

_OLLAMA_SYSTEM_PROMPT = (
    "You are reading a single user message addressed to an AI agent. "
    "Estimate the user's emotional state along three axes, each in "
    "[-1.0, 1.0]:\n"
    "- pleasure: -1 very unhappy/upset, 0 neutral, +1 very happy/satisfied\n"
    "- arousal: -1 calm/bored, 0 neutral, +1 agitated/excited/urgent\n"
    "- dominance: -1 powerless/submissive, 0 neutral, +1 in control/commanding\n\n"
    "Output ONLY a JSON object with keys pleasure, arousal, dominance. "
    "No prose, no markdown fences."
)

_OLLAMA_USER_TEMPLATE = (
    "Examples:\n"
    "- \"thanks that worked\" → "
    "{\"pleasure\": 0.6, \"arousal\": -0.1, \"dominance\": 0.3}\n"
    "- \"why isn't this working?!\" → "
    "{\"pleasure\": -0.7, \"arousal\": 0.8, \"dominance\": -0.3}\n"
    "- \"show me the latest deploys\" → "
    "{\"pleasure\": 0.0, \"arousal\": 0.0, \"dominance\": 0.5}\n"
    "- \"could you maybe try again?\" → "
    "{\"pleasure\": -0.1, \"arousal\": 0.1, \"dominance\": -0.4}\n\n"
    "User message:\n{text}\n\n"
    "JSON:"
)


_JSON_FENCE_RE = re.compile(r"^```(?:json)?\s*|\s*```$", re.IGNORECASE | re.MULTILINE)


def _strip_fences(text: str) -> str:
    return _JSON_FENCE_RE.sub("", text or "").strip()


async def classify_ollama(text: str, *, timeout: float = 15.0) -> PADClassifierResult:
    """Call the local Ollama instance for a PAD estimate. Falls back
    to the heuristic on any failure mode — bad JSON, out-of-range,
    Ollama unreachable, timeout."""
    # Lazy import so tests that exercise only the heuristic don't need
    # httpx-Ollama plumbing.
    try:
        from app.services import local_inference
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "user_signal_classifier: local_inference unavailable, "
            "falling back to heuristic. err=%s",
            exc,
        )
        return classify_heuristic(text)

    prompt = _OLLAMA_USER_TEMPLATE.format(text=text[:2000])
    try:
        raw = await local_inference.generate(
            prompt=prompt,
            system=_OLLAMA_SYSTEM_PROMPT,
            temperature=0.1,
            max_tokens=80,
            timeout=timeout,
            priority="background",
            response_format="json",
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "user_signal_classifier.ollama: generate() raised, "
            "falling back to heuristic. err=%s",
            exc,
        )
        return classify_heuristic(text)

    if not raw:
        logger.debug(
            "user_signal_classifier.ollama: empty response, "
            "falling back to heuristic",
        )
        return classify_heuristic(text)

    cleaned = _strip_fences(raw)
    try:
        data = json.loads(cleaned)
    except (ValueError, TypeError) as exc:
        logger.warning(
            "user_signal_classifier.ollama: malformed JSON %r, "
            "falling back to heuristic. err=%s",
            cleaned[:200], exc,
        )
        return classify_heuristic(text)

    try:
        return PADClassifierResult(
            pleasure=_clamp(float(data.get("pleasure", 0.0))),
            arousal=_clamp(float(data.get("arousal", 0.0))),
            dominance=_clamp(float(data.get("dominance", 0.0))),
        )
    except (TypeError, ValueError) as exc:
        logger.warning(
            "user_signal_classifier.ollama: bad PAD values %r, "
            "falling back to heuristic. err=%s",
            data, exc,
        )
        return classify_heuristic(text)


# ── Public boundary ──────────────────────────────────────────────────


def get_default_backend() -> str:
    return os.environ.get("USER_SIGNAL_BACKEND", "ollama").strip().lower()


def classify_user_signal(
    text: str,
    *,
    backend: Optional[str] = None,
) -> PADClassifierResult:
    """Synchronous classifier entry point. Routes by backend and
    handles the async→sync bridge for callers like the backfill script."""
    backend = (backend or get_default_backend()).strip().lower()
    if backend == "heuristic":
        return classify_heuristic(text)

    try:
        return asyncio.run(classify_ollama(text))
    except RuntimeError:
        # Already inside an event loop (e.g. pytest-asyncio). Caller
        # should use `await classify_ollama(...)` directly in that
        # context; we don't try to nest loops.
        logger.warning(
            "user_signal_classifier.classify_user_signal: cannot use "
            "asyncio.run inside running loop; falling back to heuristic. "
            "Async callers should `await classify_ollama(text)` directly."
        )
        return classify_heuristic(text)


__all__ = [
    "PADClassifierResult",
    "classify_user_signal",
    "classify_heuristic",
    "classify_ollama",
    "get_default_backend",
]
