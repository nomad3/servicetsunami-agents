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
import re
import uuid
from pathlib import Path
from typing import Awaitable, Callable, Dict

import httpx

from .learning_prompts import SYNTHESIS_SYSTEM, SYNTHESIS_USER


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


# ── T2.3: synthesize_skill_draft ───────────────────────────────────────
# Patterns the synthesis prompt explicitly forbids inside python-engine
# skills. The intent is: external IO goes through MCP tools, not through
# subprocess.run() bound into a skill body. We check the body (post-
# frontmatter) only when the draft selected ``engine: python`` —
# markdown bodies are prose for another agent to read, so a transcript
# that mentions "yt-dlp" verbatim shouldn't be quarantined as a
# forbidden shellout.
#
# Ordering note: the regex list is scanned in order and the first match
# wins for the raised error message; keep the binary names ahead of the
# more general subprocess+curl/wget pattern so error messages are
# precise about what tripped.
_FORBIDDEN_PATTERNS = [
    r"\byt[-_]?dlp\b",
    r"\bffmpeg\b",
    r"\bffprobe\b",
    r"subprocess\.run.*\b(curl|wget)\b",
]


def _slugify(name: str) -> str:
    """Kebab-case a frontmatter ``name`` into a slug for the skills library.

    Lowercased, non-alphanumeric runs collapse to a single hyphen, max
    60 chars (matches the skills library slug column). Falls back to
    ``learned-skill`` when the input has no alphanumeric content so the
    workflow never sees an empty slug (T2.6's install path requires a
    non-empty slug to seed its collision-resolution loop).
    """
    s = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    return s[:60] or "learned-skill"


async def _llm_synthesize(
    transcript: str,
    source_url: str,
    hints: list[str],
) -> tuple[str, dict]:
    """Single Claude Sonnet call returning ``(skill_md, synthetic_test)``.

    Model id is configurable via ``LUNA_LEARN_SYNTHESIS_MODEL`` so we
    can flip to a newer Sonnet without a redeploy. The LLM is instructed
    (system prompt) to return a single JSON object with ``skill_md`` and
    ``synthetic_test`` keys; we don't tolerate any other shape because
    Temporal's revise loop needs a clean parse failure → ``DraftInvalid``
    signal, not a silent best-effort recovery.

    Split out as its own helper so unit tests can patch it without
    touching the anthropic SDK (which isn't a hard runtime dep of the
    test harness). T3.1's activity wrapper layers retry policy on top.
    """
    import anthropic  # local import: SDK isn't loaded for non-synth paths

    model = os.environ.get("LUNA_LEARN_SYNTHESIS_MODEL", "claude-sonnet-4-6")
    hints_block = (
        "Reviewer feedback to address:\n" + "\n".join(f"- {h}" for h in hints)
        if hints
        else ""
    )
    client = anthropic.AsyncAnthropic()
    resp = await client.messages.create(
        model=model,
        max_tokens=4096,
        system=SYNTHESIS_SYSTEM,
        messages=[
            {
                "role": "user",
                "content": SYNTHESIS_USER.format(
                    transcript=transcript,
                    source_url=source_url,
                    hints_block=hints_block,
                ),
            }
        ],
    )
    payload = json.loads(resp.content[0].text)
    return payload["skill_md"], payload["synthetic_test"]


async def synthesize_skill_draft(
    transcript: str,
    source_url: str,
    hints: list[str] | None = None,
) -> dict:
    """T2.3 — LLM-synthesize a SKILL.md draft from a transcript.

    Spec §1.5 (engine selection) + §1.6 (frontmatter schema). Single
    Claude Sonnet call via ``_llm_synthesize``; the prompt embeds the
    rubric, PII-scrub instruction, and the FORBIDDEN-shellout list. On
    return we validate the draft structurally:

    1. Parse YAML frontmatter via the leading ``---\\n…\\n---`` block.
       Missing or malformed → ``DraftInvalid`` (workflow re-cycles as a
       revise iteration).
    2. Require ``name`` AND ``engine`` keys in the frontmatter.
    3. If ``engine == "python"`` scan the body for any
       ``_FORBIDDEN_PATTERNS`` match → ``DraftForbiddenShellout``
       (workflow treats as a hard quarantine, not a revise).

    The returned dict matches the contract the Temporal workflow (T1.3)
    consumes:

        {
            "skill_md": str,
            "slug": str,            # kebab-case, ≤60 chars
            "engine": "markdown" | "python",
            "synthetic_test_input": dict,
            "synthetic_test_expected": dict,
        }

    ``hints`` carries the prior reviewer's findings on revise cycles and
    is empty on the first synthesis pass.
    """
    skill_md, test = await _llm_synthesize(transcript, source_url, hints or [])

    # Frontmatter must be the very first block; partial matches against a
    # body-embedded ``---`` are not enough. Anchored to start-of-string
    # with DOTALL so the YAML block can wrap multiple lines.
    fm_match = re.match(r"^---\n(.+?)\n---", skill_md, re.DOTALL)
    if not fm_match:
        raise DraftInvalid("missing frontmatter")

    import yaml  # local import to keep top-of-module light

    try:
        fm = yaml.safe_load(fm_match.group(1))
    except yaml.YAMLError as exc:
        raise DraftInvalid(f"YAML parse: {exc}") from exc

    if not isinstance(fm, dict) or "name" not in fm or "engine" not in fm:
        raise DraftInvalid("frontmatter missing name or engine")

    # Forbidden-shellout check applies only to python skills (markdown
    # bodies are prose for an agent to read, not executable code).
    if fm["engine"] == "python":
        body = skill_md[fm_match.end():]
        for pat in _FORBIDDEN_PATTERNS:
            if re.search(pat, body, re.IGNORECASE):
                raise DraftForbiddenShellout(f"forbidden shellout: {pat}")

    return {
        "skill_md": skill_md,
        "slug": _slugify(fm["name"]),
        "engine": fm["engine"],
        "synthetic_test_input": test.get("input", {}),
        "synthetic_test_expected": test.get("expected", {}),
    }


# ── T2.4: dispatch_skill_review ────────────────────────────────────────
# The Code Reviewer agent (per spec §0.3 cross-agent QC) lives behind
# the api's internal task-dispatch endpoint. The agent UUID is the
# tenant-Simon Code Reviewer seeded by migration 151 (the plan freezes
# this UUID — actual seeded id may differ in deploys; T2.5b reconciles).
# REVIEW_TIMEOUT_S caps the round trip so a stuck reviewer can't pin a
# Temporal activity past its activity-timeout budget; the workflow
# (T1.3 / T3.x) maps ReviewTimeout to cache+notify per §3.
CODE_REVIEWER_AGENT_ID = "755796a4-4cc4-4d1c-99e5-dd9c4f7d0f22"
REVIEW_TIMEOUT_S = 60


async def _dispatch_agent(agent_id: str, payload: dict) -> dict:
    """POST to the api's internal agent-dispatch endpoint.

    Auth is the same X-Internal-Key the other T2.x wrappers use. The
    path the plan documents (``/api/v1/agents/{agent_id}/dispatch``)
    doesn't exist on the api as a literal route — the actual internal
    sibling of the JWT-gated dispatch verb is
    ``POST /api/v1/tasks/internal/dispatch`` (Phase 4 C-FINAL-1). We
    POST there with a ``delegate`` task_type and pass the structured
    review payload through ``context`` so the reviewer agent receives
    the SKILL.md + transcript + synthetic test verbatim. Tests mock
    this helper, so the URL/contract drift is contained here; T3.x's
    activity wrapper layer covers the live-wire integration.

    The 60s client timeout matches ``REVIEW_TIMEOUT_S`` so the outer
    ``asyncio.wait_for`` is the authoritative deadline either way —
    whichever fires first raises a TimeoutError that maps to
    ``ReviewTimeout``.
    """
    async with httpx.AsyncClient(timeout=REVIEW_TIMEOUT_S) as client:
        r = await client.post(
            f"{_API_BASE}/api/v1/tasks/internal/dispatch",
            json={
                "task_type": "delegate",
                "target_agent_id": agent_id,
                "objective": payload.get("task", "review_synthesized_skill"),
                "context": payload,
            },
            headers={"X-Internal-Key": _API_INTERNAL_KEY},
        )
        r.raise_for_status()
        return r.json()


async def dispatch_skill_review(
    skill_md: str,
    transcript: str,
    source_url: str,
    synthetic_test_input: dict,
    synthetic_test_expected: dict,
) -> dict:
    """T2.4 — dispatch the draft to the Code Reviewer agent and await verdict.

    Spec §0.3 (cross-agent QC) + §1.10. The reviewer agent inspects:
    (a) the SKILL.md is idiomatic + safe (no shellout-via-prose), and
    (b) the synthetic test isn't a tautology of the synthesizer's own
    rationale (the synthesizer wrote the test, so a peer must vet that
    it actually probes the skill).

    Returns ``{"verdict": str, "findings": list, "reviewer_agent_id": str}``.
    Verdict values the workflow recognizes are ``"approved"``,
    ``"revise"``, ``"reject"`` — anything else defaults to ``"revise"``
    (re-cycle through synthesis with the findings as hints).

    Typed errors:
      - ``ReviewerNotProvisioned`` — registry returned 404; workflow
        caches the draft + notifies the operator (§3.2).
      - ``ReviewTimeout`` — reviewer didn't respond inside
        ``REVIEW_TIMEOUT_S``; same cache+notify path as above.
    """
    payload = {
        "task": "review_synthesized_skill",
        "skill_md": skill_md,
        "transcript": transcript,
        "source_url": source_url,
        "synthetic_test": {
            "input": synthetic_test_input,
            "expected": synthetic_test_expected,
        },
    }
    try:
        result = await asyncio.wait_for(
            _dispatch_agent(CODE_REVIEWER_AGENT_ID, payload),
            timeout=REVIEW_TIMEOUT_S,
        )
    except asyncio.TimeoutError as e:
        raise ReviewTimeout() from e
    except httpx.HTTPStatusError as e:
        if e.response.status_code == 404:
            raise ReviewerNotProvisioned() from e
        raise
    return {
        "verdict": result.get("verdict", "revise"),
        "findings": result.get("findings", []),
        "reviewer_agent_id": CODE_REVIEWER_AGENT_ID,
    }


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
