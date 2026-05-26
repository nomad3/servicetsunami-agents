"""Luna Learn — MCP primitives.

T1.2 + T2.x will populate the actual tool callables (extract_media,
transcribe_url, synthesize_skill_draft, dispatch_skill_review,
run_synthetic_test, install_skill, diffuse_learning) and the ``TOOLS``
registry.

This file is intentionally minimal for T1.2a — it only defines the
typed-exception hierarchy that the HTTP shim in ``server.py`` maps to
HTTP status codes so Temporal activities in T3.1 can branch on
``error_type`` without parsing free-form 500s.

Status-code mapping (authoritative table lives in ``server.py``):

    MediaTooLong               → 413
    MediaPrivate               → 451
    MediaNotFound              → 404
    MediaGeoBlocked            → 403
    MediaAntiScrape            → 429
    DraftInvalid               → 422
    DraftForbiddenShellout     → 424
    ReviewerNotProvisioned     → 503
    ReviewTimeout              → 504
    SlugExhausted              → 409
    (anything else)            → 500 + error_type="UnknownError"

The status codes are advisory; the ``error_type`` field in the response
body is authoritative for branching (see T1.2a / T3.1 in the Luna Learn
plan, doc ``docs/superpowers/plans/2026-05-25-luna-learn-from-media-plan.md``).
"""
from __future__ import annotations

from typing import Awaitable, Callable, Dict


# ── Exception hierarchy ────────────────────────────────────────────────
class LearningToolError(Exception):
    """Base for all Luna Learn typed errors. Catch-all for the shim's
    ``isinstance`` checks if more granular subclasses are added later."""


class MediaTooLong(LearningToolError):
    """Source media exceeds the configured per-job duration budget."""


class MediaPrivate(LearningToolError):
    """Source URL is private / requires auth the worker can't supply."""


class MediaNotFound(LearningToolError):
    """Source URL returns 404 or has been removed by the host."""


class MediaGeoBlocked(LearningToolError):
    """Source URL refuses the worker's egress region."""


class MediaAntiScrape(LearningToolError):
    """Host returned a bot-block / rate-limit response (CAPTCHA, 429, etc.)."""


class DraftInvalid(LearningToolError):
    """Synthesized skill draft failed structural validation (schema, frontmatter, etc.)."""


class DraftForbiddenShellout(LearningToolError):
    """Draft attempted to invoke a shell or other forbidden side-effect."""


class ReviewerNotProvisioned(LearningToolError):
    """No reviewer agent is configured for the tenant; review can't dispatch."""


class ReviewTimeout(LearningToolError):
    """Reviewer agent did not return a verdict within the budget."""


class SlugExhausted(LearningToolError):
    """All candidate slugs collided with existing skills; can't pick a fresh name."""


# ── Tool stubs ─────────────────────────────────────────────────────────
# Bodies land in T2.1–T2.7. Signatures are frozen here so the HTTP shim,
# Temporal activity wrappers (T3.x), and reviewer-agent contracts can be
# built against a stable surface in parallel. Each raises
# ``NotImplementedError("TX.Y")`` tagged to the task that fills it in.
async def extract_media(url: str, max_duration_s: int = 900) -> dict:
    """T2.1 — download audio from a public URL (yt-dlp + ffmpeg)."""
    raise NotImplementedError("T2.1")


async def transcribe_url(audio_path: str) -> dict:
    """T2.2 — transcribe a local audio file to text + segments."""
    raise NotImplementedError("T2.2")


async def synthesize_skill_draft(
    transcript: str,
    source_url: str,
    hints: list[str] | None = None,
) -> dict:
    """T2.3 — LLM-synthesize a SKILL.md draft from a transcript."""
    raise NotImplementedError("T2.3")


async def dispatch_skill_review(
    skill_md: str,
    transcript: str,
    source_url: str,
    synthetic_test_input: dict,
    synthetic_test_expected: dict,
) -> dict:
    """T2.4 — dispatch the draft to a reviewer agent and await verdict."""
    raise NotImplementedError("T2.4")


async def run_synthetic_test(
    skill_md: str,
    test_input: dict,
    test_expected: dict,
) -> dict:
    """T2.5 — execute the reviewer-provided synthetic test against the draft."""
    raise NotImplementedError("T2.5")


async def install_skill(
    skill_md: str,
    slug: str,
    tenant_id: str,
    source_url: str,
    reviewer_agent_id: str,
    transcript_sha256: str,
    learned_by_agent_id: str,
) -> dict:
    """T2.6 — persist an approved draft into the tenant skills library."""
    raise NotImplementedError("T2.6")


async def diffuse_learning(
    skill_id: str,
    source_url: str,
    capabilities: list[str],
) -> dict:
    """T2.7 — broadcast the new skill to peer agents (stigmergy event)."""
    raise NotImplementedError("T2.7")


# ── Tool registry ──────────────────────────────────────────────────────
# Populated by T1.2 (skeleton) and T2.x (real implementations). The HTTP
# shim in ``server.py`` imports this dict to dispatch
# ``POST /agentprovision/v1/tools/{tool_name}`` requests. Tests for the
# shim can patch entries in to stub network IO without touching the
# dispatch path itself.
TOOLS: Dict[str, Callable[..., Awaitable]] = {
    "extract_media": extract_media,
    "transcribe_url": transcribe_url,
    "synthesize_skill_draft": synthesize_skill_draft,
    "dispatch_skill_review": dispatch_skill_review,
    "run_synthetic_test": run_synthetic_test,
    "install_skill": install_skill,
    "diffuse_learning": diffuse_learning,
}


__all__ = [
    "LearningToolError",
    "MediaTooLong",
    "MediaPrivate",
    "MediaNotFound",
    "MediaGeoBlocked",
    "MediaAntiScrape",
    "DraftInvalid",
    "DraftForbiddenShellout",
    "ReviewerNotProvisioned",
    "ReviewTimeout",
    "SlugExhausted",
    "extract_media",
    "transcribe_url",
    "synthesize_skill_draft",
    "dispatch_skill_review",
    "run_synthetic_test",
    "install_skill",
    "diffuse_learning",
    "TOOLS",
]
