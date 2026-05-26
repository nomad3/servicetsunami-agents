"""Luna Learn — MCP primitives.

T1.2 + T2.x populate the actual tool callables (extract_media,
transcribe_url, synthesize_skill_draft, dispatch_skill_review,
run_synthetic_test, install_skill, diffuse_learning) and the ``TOOLS``
registry. T2.1 (this file) ships the real body of ``extract_media``
plus its yt-dlp subprocess helpers; T2.2–T2.7 remain stubs.

The typed-exception hierarchy below is what the HTTP shim in
``server.py`` maps to HTTP status codes so Temporal activities in T3.1
can branch on ``error_type`` without parsing free-form 500s.

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

import asyncio
import json
import os
import uuid
from pathlib import Path
from typing import Awaitable, Callable, Dict

import httpx


# ── Internal API client config ─────────────────────────────────────────
# transcribe_url (T2.2) and the later T2.4/T2.5 wrappers all POST back to
# the api process. We default the base URL to the in-cluster service name
# so this works out-of-the-box in docker-compose; tests mock the helpers
# above this layer so the env vars never matter under pytest. Key default
# mirrors ``mcp_auth.INTERNAL_KEY`` so a missing env doesn't crash imports
# (the dev key only opens internal-tier routes, not real tenant data).
_API_BASE = os.environ.get("AGENTPROVISION_API_BASE", "http://api:8000")
_API_INTERNAL_KEY = os.environ.get("MCP_API_KEY", "dev_mcp_key")


# ── Filesystem layout ──────────────────────────────────────────────────
# T2.1 writes downloaded audio under this dir so transcribe_url (T2.2)
# and the cleanup step in the Temporal workflow (T1.3) can find it
# without coordinating paths through the activity inputs. Created on
# first use rather than at import-time so unit tests don't need to mock
# the FS — yt-dlp is mocked out in tests, so the dir is only ever
# created in the real container path.
_LEARNING_DIR = Path("/var/agentprovision/workspaces/_learning")


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
async def _probe_duration(url: str) -> int:
    """Probe source duration via ``yt-dlp --get-duration``.

    Returns the total duration in seconds. Raises ``RuntimeError`` with
    yt-dlp's stderr when the probe fails; ``extract_media`` translates
    that into the right ``LearningToolError`` subclass via
    ``_map_ytdlp_error``.

    Output format from yt-dlp is ``HH:MM:SS`` or ``MM:SS`` or ``SS``
    depending on length; we fold the colon-separated parts into seconds
    in one pass.
    """
    proc = await asyncio.create_subprocess_exec(
        "yt-dlp",
        "--get-duration",
        url,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    out, err = await proc.communicate()
    if proc.returncode != 0:
        raise RuntimeError(err.decode() or "yt-dlp probe failed")
    parts = out.decode().strip().split(":")
    secs = 0
    for part in parts:
        secs = secs * 60 + int(part)
    return secs


async def _run_yt_dlp(url: str, output_path: str) -> dict:
    """Download audio + return yt-dlp's JSON metadata dict.

    ``output_path`` is a yt-dlp template (e.g. ``…/<job>.%(ext)s``); the
    resolved file path is reported back in the returned dict's
    ``_filename`` field. ``--print-json`` prints one JSON object per
    downloaded item; we take the last line so any progress chatter
    yt-dlp may emit on stdout doesn't trip the parse.
    """
    proc = await asyncio.create_subprocess_exec(
        "yt-dlp",
        "-x",
        "--audio-format",
        "m4a",
        "-o",
        output_path,
        "--print-json",
        url,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    out, err = await proc.communicate()
    if proc.returncode != 0:
        raise RuntimeError(err.decode() or "yt-dlp failed")
    return json.loads(out.decode().splitlines()[-1])


def _map_ytdlp_error(stderr: str) -> type[LearningToolError]:
    """Translate yt-dlp stderr text into the typed exception class the
    HTTP shim (T1.2a) maps to a status code.

    Ordering matters: ``429`` and "rate" must be checked before the
    generic ``unavailable`` keyword, since rate-limit responses
    sometimes phrase themselves as "video unavailable due to ...".
    Geo-block also takes precedence over the generic NotFound for the
    same reason — "not available in your country" contains
    "unavailable" as a substring in some locales.
    """
    s = stderr.lower()
    if "429" in s or "rate" in s or "blocked" in s or "captcha" in s:
        return MediaAntiScrape
    if "not available in your country" in s or "geo" in s:
        return MediaGeoBlocked
    if "private" in s or "sign in" in s or "members-only" in s:
        return MediaPrivate
    if "unavailable" in s or "removed" in s or "404" in s or "does not exist" in s:
        return MediaNotFound
    return LearningToolError


async def extract_media(url: str, max_duration_s: int = 900) -> dict:
    """T2.1 — download audio from a public URL (yt-dlp + ffmpeg).

    Spec §1.1. Probes duration first to fail fast on long-form media
    (the synthesis budget assumes ~15min ceiling). On success returns::

        {
            "audio_path": "/var/.../<job_id>.m4a",
            "metadata": {
                "title": str | None,
                "duration_s": int | None,
                "uploader": str | None,
                "source_platform": str | None,  # yt-dlp "extractor"
            },
        }

    Subprocess errors are translated into the typed ``Media*`` classes
    so Temporal activities (T3.1) can branch on ``error_type`` without
    parsing free-form 500s. See ``_map_ytdlp_error``.
    """
    try:
        _LEARNING_DIR.mkdir(parents=True, exist_ok=True)
    except (PermissionError, OSError):
        # In container the dir is writable; in unit tests yt-dlp is
        # mocked so the dir is never actually used. Swallow filesystem
        # errors here rather than forcing tests to patch the path.
        pass

    job_id = uuid.uuid4().hex
    output_path = str(_LEARNING_DIR / f"{job_id}.%(ext)s")

    try:
        duration_s = await _probe_duration(url)
    except RuntimeError as exc:
        raise _map_ytdlp_error(str(exc))(str(exc)) from exc

    if duration_s > max_duration_s:
        raise MediaTooLong(
            f"duration {duration_s}s exceeds cap {max_duration_s}s"
        )

    try:
        meta = await _run_yt_dlp(url, output_path)
    except RuntimeError as exc:
        raise _map_ytdlp_error(str(exc))(str(exc)) from exc

    return {
        "audio_path": meta.get("_filename") or str(_LEARNING_DIR / f"{job_id}.m4a"),
        "metadata": {
            "title": meta.get("title"),
            "duration_s": meta.get("duration"),
            "uploader": meta.get("uploader"),
            "source_platform": meta.get("extractor"),
        },
    }


async def _transcribe_bytes_async(audio_bytes: bytes) -> dict:
    """POST audio bytes to the api's existing transcription endpoint.

    Wraps the same code-path the web client uses (``POST
    /api/v1/media/transcribe``); the api hands the bytes to the
    ``TranscribeAudioWorkflow`` on the code-worker Temporal queue and
    returns either an inline transcript or a ``{status: "pending",
    job_id: ...}`` envelope for long-form audio. Either way we return
    whatever the api returned verbatim — the workflow layer (T3.x)
    decides how to handle the pending case.

    Split out so unit tests can mock the network hop without mocking
    httpx itself; T3.1's activity wrapper layers retry/timeout policy on
    top of this.
    """
    async with httpx.AsyncClient(timeout=60.0) as client:
        files = {"file": ("audio.m4a", audio_bytes, "audio/mp4")}
        response = await client.post(
            f"{_API_BASE}/api/v1/media/transcribe",
            files=files,
            headers={"X-Internal-Key": _API_INTERNAL_KEY},
        )
        response.raise_for_status()
        return response.json()


async def transcribe_url(audio_path: str) -> dict:
    """T2.2 — transcribe a local audio file to text + segments.

    Spec §1.1. Reads the file produced by ``extract_media`` and hands
    the bytes to the api's existing transcription endpoint (which routes
    through the code-worker whisper workflow). Raises ``FileNotFoundError``
    if the path doesn't exist so the Temporal activity layer can surface
    a precise error (rather than a generic httpx upload failure).
    """
    path = Path(audio_path)
    if not path.exists():
        raise FileNotFoundError(audio_path)
    return await _transcribe_bytes_async(path.read_bytes())


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
