"""Skill description optimizer — Phase 4 cut of the skill-creator
framework (#301).

Takes a raw skill description (typically the operator's first draft
when creating a new skill) and produces a refined version that:

  - Leads with the user-action keyword (the SKILL.md description
    field's load-bearing prefix Claude Code matches against).
  - Compresses verbose prose to the < 1024-char limit.
  - Includes the trigger conditions explicitly (so the upstream
    skill-router has the right context vector).

This is the small platform-side piece of the larger Phase 4 effort
(UI editor + packaging pipeline + marketplace surface). The model
call goes through ``local_inference.generate`` (Ollama gemma4 by
default) — same backend as the user_signal_classifier shipped in
#634, with the same brace-escape lesson applied to the prompt
template (don't ship a template containing literal ``{...}`` that
``.format()`` will mis-parse).

The full Phase 4 UI editor lives in apps/web (separate effort).
This service is the contract the editor's "Optimize" button will
call.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class OptimizedDescription:
    """The refined description + provenance.

    ``original`` is the raw input; ``optimized`` is the rewritten
    output. ``model`` carries the backend identifier so re-runs
    against a different model can be A/B compared.
    """

    original: str
    optimized: str
    model: str
    backend: str  # 'ollama' | 'heuristic'

    def to_dict(self) -> dict:
        return {
            "original": self.original,
            "optimized": self.optimized,
            "model": self.model,
            "backend": self.backend,
        }


# Max length per Claude Code's SKILL.md description spec.
MAX_DESCRIPTION_LEN = 1024


# ── Heuristic backend (zero-dependency fallback) ──────────────────────


def _optimize_heuristic(description: str) -> OptimizedDescription:
    """Trivial cleanup: trim whitespace, collapse runs of spaces,
    truncate at sentence boundary if over MAX_DESCRIPTION_LEN.

    No LLM call — used as the safety-net when Ollama is unavailable
    AND as the deterministic test path. The "optimization" is
    minimal; the value is consistency."""
    cleaned = " ".join(description.split())
    if len(cleaned) <= MAX_DESCRIPTION_LEN:
        return OptimizedDescription(
            original=description,
            optimized=cleaned,
            model="(none)",
            backend="heuristic",
        )

    # Truncate at the last sentence boundary that fits.
    head = cleaned[:MAX_DESCRIPTION_LEN]
    last_period = head.rfind(". ")
    if last_period > MAX_DESCRIPTION_LEN // 2:
        head = head[:last_period + 1]
    return OptimizedDescription(
        original=description,
        optimized=head,
        model="(none)",
        backend="heuristic",
    )


# ── Ollama backend ────────────────────────────────────────────────────

_SYSTEM_PROMPT = (
    "You rewrite skill descriptions for an AI agent's skill registry. "
    "Output a single paragraph (no markdown, no quotes, no lists) "
    "that:\n"
    "  1. STARTS with the user-action keyword (e.g. 'Searches', "
    "'Generates', 'Refactors').\n"
    "  2. Names the inputs and outputs.\n"
    "  3. Names the trigger conditions ('when the user asks about X').\n"
    "  4. Is under 1024 characters.\n"
    "Output ONLY the rewritten paragraph — no preamble, no fences."
)


# Brace-escaped — str.format treats {{ }} as literal braces. The
# lesson is from PR #635 (user_signal_classifier had the same bug).
_USER_TEMPLATE = (
    "Original description:\n"
    "{description}\n\n"
    "Skill name: {skill_name}\n\n"
    "Rewritten description:"
)


async def optimize_ollama(
    description: str,
    *,
    skill_name: str = "",
    model: Optional[str] = None,
    timeout: float = 60.0,
) -> OptimizedDescription:
    """Call Ollama to rewrite the description. Falls back to the
    heuristic on any LLM failure mode — empty response, timeout,
    transport error.

    Default timeout 60s matches the cold-load envelope on gemma4
    (PR #638 lesson)."""
    try:
        from app.services import local_inference
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "description_optimizer: local_inference unavailable, "
            "falling back to heuristic. err=%s", exc,
        )
        return _optimize_heuristic(description)

    prompt = _USER_TEMPLATE.format(
        description=description[:4000],
        skill_name=skill_name or "(unspecified)",
    )
    try:
        raw = await local_inference.generate(
            prompt=prompt,
            model=model,
            system=_SYSTEM_PROMPT,
            temperature=0.2,
            max_tokens=400,
            timeout=timeout,
            priority="background",
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "description_optimizer.ollama: generate raised, "
            "falling back to heuristic. err=%s", exc,
        )
        return _optimize_heuristic(description)

    if not raw or not raw.strip():
        return _optimize_heuristic(description)

    optimized = raw.strip()
    # Trim trailing markdown / quote artifacts if present.
    if optimized.startswith('"') and optimized.endswith('"'):
        optimized = optimized[1:-1]
    optimized = optimized[:MAX_DESCRIPTION_LEN]

    return OptimizedDescription(
        original=description,
        optimized=optimized,
        model=model or "(auto)",
        backend="ollama",
    )


def optimize_description_sync(
    description: str,
    *,
    skill_name: str = "",
    backend: str = "heuristic",
) -> OptimizedDescription:
    """Synchronous entry-point for non-async callers (e.g. unit
    tests or a future synchronous API handler). Only the heuristic
    backend is reachable from sync code — the Ollama path requires
    asyncio.run, which we won't nest inside a possibly-running loop.
    """
    if backend != "heuristic":
        raise ValueError(
            "optimize_description_sync only supports backend='heuristic'; "
            "for Ollama use await optimize_ollama(...) directly."
        )
    return _optimize_heuristic(description)


__all__ = [
    "OptimizedDescription",
    "MAX_DESCRIPTION_LEN",
    "optimize_ollama",
    "optimize_description_sync",
]
