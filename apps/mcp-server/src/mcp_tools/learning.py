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


# ── Tool registry ──────────────────────────────────────────────────────
# Populated by T1.2 (skeleton) and T2.x (real implementations). Kept as
# an empty dict here so the HTTP shim can import it without circular-
# dependency risk and so tests for the shim can patch entries in.
TOOLS: Dict[str, Callable[..., Awaitable]] = {}


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
    "TOOLS",
]
