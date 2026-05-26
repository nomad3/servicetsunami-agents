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
from datetime import datetime, timezone
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


# ── T2.5: run_synthetic_test ───────────────────────────────────────────
# The synthetic test is a structured {input, expected} pair the
# synthesizer (T2.3) attached to the draft and the reviewer (T2.4)
# vetted as substantive. We execute the draft against ``test_input``
# via a dedicated internal endpoint (``/api/v1/skills/execute-draft``
# — shipped separately as T4.4d) that knows how to run an unsaved
# skill_md in the code-worker without persisting it to the library.
# This task implements only the mcp-server caller; the endpoint itself
# doesn't exist yet, so unit tests mock ``_execute_draft`` directly to
# decouple T2.5 from T4.4d's rollout schedule.
#
# Result-shape contract: this function NEVER raises. The whole point
# of the synthetic test step is to surface drift between the draft's
# advertised behavior and what it actually does, so any failure mode —
# execution exception, value mismatch, network error — is recorded as
# structured data in the returned ``{passed, actual_output, error}``
# envelope. The Temporal workflow (T3.x) decides what to do with a
# ``passed=False`` result (revise, quarantine, or reject) based on the
# verdict + finding combination, not on whether this call threw.
_EXECUTE_DRAFT_TIMEOUT_S = 30.0


async def _execute_draft(skill_md: str, inputs: dict) -> dict:
    """POST a draft skill_md + inputs to the api's execute-draft endpoint.

    The endpoint (``POST /api/v1/skills/execute-draft``) is a T4.4d
    deliverable — it hands the unsaved skill_md to the code-worker's
    existing skill-execution path and returns the raw output dict
    without persisting anything to the skills library. We use the same
    X-Internal-Key auth as the other T2.x wrappers (see
    ``_API_INTERNAL_KEY`` at the top of this module).

    30s timeout: synthetic tests should be cheap by construction
    (single deterministic transformation, no external IO); a draft
    that needs longer than that to produce one output is almost
    certainly misbehaving and we want the test to fail fast rather
    than pin a Temporal activity slot.

    Split out as its own helper so the unit tests in T2.5 can patch
    this call without spinning up the (not-yet-shipped) endpoint, and
    so T3.x's activity wrapper can layer retry policy on top of a
    stable seam.
    """
    async with httpx.AsyncClient(timeout=_EXECUTE_DRAFT_TIMEOUT_S) as client:
        response = await client.post(
            f"{_API_BASE}/api/v1/skills/execute-draft",
            json={"skill_md": skill_md, "inputs": inputs},
            headers={"X-Internal-Key": _API_INTERNAL_KEY},
        )
        response.raise_for_status()
        return response.json()


def _subset_match(actual: dict, expected: dict) -> bool:
    """Return True iff every key in ``expected`` is present in ``actual``
    with an equal value.

    Subset semantics (not equality) so the synthesizer can specify the
    minimum contract the skill must honor without over-specifying — the
    skill is free to return additional fields. A draft printer-error
    skill might return ``{"resolved": True, "steps_taken": 3,
    "duration_s": 12}``; the synthetic test only pins ``resolved=True``
    and ignores the rest. An empty ``expected`` trivially passes (the
    workflow treats that as a "smoke test only" signal).
    """
    return all(actual.get(k) == v for k, v in expected.items())


async def run_synthetic_test(
    skill_md: str,
    test_input: dict,
    test_expected: dict,
) -> dict:
    """T2.5 — execute the reviewer-provided synthetic test against the draft.

    Spec §1.1. Runs the draft against ``test_input`` via the
    execute-draft endpoint, then compares the result against
    ``test_expected`` with subset-match semantics. Returns::

        {
            "passed": bool,
            "actual_output": dict | None,  # None on execution error
            "error": str | None,           # populated on execution error
        }

    NEVER raises. Synthetic-test failures are deliberate signal for the
    workflow to act on — turning them into Python exceptions would
    collapse the cache-vs-quarantine-vs-revise branching into a
    single generic 500.
    """
    try:
        actual = await _execute_draft(skill_md, test_input)
    except Exception as exc:  # noqa: BLE001 — all failures are data
        return {
            "passed": False,
            "actual_output": None,
            "error": str(exc),
        }
    return {
        "passed": _subset_match(actual, test_expected),
        "actual_output": actual,
        "error": None,
    }


# ── T2.6: install_skill ────────────────────────────────────────────────
# Persists an approved draft into the tenant skills library by POSTing to
# the api's internal install-learned endpoint (T4.4e — not yet shipped).
# Tests mock ``_install_via_api`` so this lands independently of T4.4e.
#
# Responsibilities split:
#   1. ``_inject_provenance`` rewrites the draft's frontmatter to embed
#      the spec §1.6 ``provenance:`` block (source_url, synthesis_date,
#      reviewer_agent_id, transcript_sha256, learned_by_agent_id) so the
#      installed skill carries a verifiable lineage record on disk.
#   2. ``_install_via_api`` POSTs the rewritten skill_md + slug + tenant
#      to the api. The api owns the transactional DB+FS write — this
#      module never touches the skills library directly (kept symmetric
#      with the other T2.x wrappers).
#   3. ``install_skill`` itself wraps the install call in a slug-conflict
#      retry loop: first attempt uses the bare slug; subsequent attempts
#      append ``-v2``, ``-v3``, … up to ``SLUG_MAX_RETRIES`` (5). Only 409
#      responses trigger a retry — any other ``HTTPStatusError`` bubbles
#      up. Exhausting the retries raises ``SlugExhausted`` (mapped to 409
#      by the HTTP shim per the spec table at top of module).
SLUG_MAX_RETRIES = 5


def _inject_provenance(
    skill_md: str,
    *,
    source_url: str,
    reviewer_agent_id: str,
    transcript_sha256: str,
    learned_by_agent_id: str,
) -> str:
    """Insert a ``provenance:`` block into the draft's frontmatter (spec §1.6).

    The block is spliced in immediately after the opening ``---\\n`` of the
    frontmatter via a one-shot regex (``count=1``) so we don't accidentally
    duplicate it if the body happens to contain another ``---`` line. The
    ``synthesis_date`` is captured at install-time (UTC ISO8601, second
    precision) because that's when the draft becomes durable — the upstream
    transcript may be older, and downstream tooling that diffs synthesis
    runs needs the install timestamp, not the source media timestamp.
    """
    iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    block = (
        "provenance:\n"
        f"  source_url: {source_url}\n"
        f'  synthesis_date: "{iso}"\n'
        f"  reviewer_agent_id: {reviewer_agent_id}\n"
        f"  transcript_sha256: {transcript_sha256}\n"
        f"  learned_by_agent_id: {learned_by_agent_id}\n"
    )
    return re.sub(r"^(---\n)", f"\\1{block}", skill_md, count=1)


async def _install_via_api(
    skill_md: str,
    slug: str,
    tenant_id: str,
    learned_by_agent_id: str,
    source_url: str,
) -> dict:
    """POST the rewritten draft to the api's install-learned endpoint.

    Endpoint (``POST /api/v1/skills/install-learned``) is T4.4e — it owns
    the transactional DB insert into ``library_skills`` + filesystem write
    to ``_tenant/<uuid>/<slug>/skill.md`` + ``library_revisions`` audit row
    (actor = ``learned_by_agent_id``, reason = ``learned from <source_url>``).
    On unique-constraint violation (slug already taken) the endpoint returns
    HTTP 409 so the caller can re-attempt with a suffixed slug.

    Split out as its own helper so unit tests can patch the network hop
    without spinning up the not-yet-shipped endpoint; T3.x's activity
    wrapper layer adds retry/timeout policy on top.
    """
    async with httpx.AsyncClient(timeout=15.0) as client:
        r = await client.post(
            f"{_API_BASE}/api/v1/skills/install-learned",
            json={
                "skill_md": skill_md,
                "slug": slug,
                "tenant_id": tenant_id,
                "actor_user_id": learned_by_agent_id,
                "reason": f"learned from {source_url}",
            },
            headers={"X-Internal-Key": _API_INTERNAL_KEY},
        )
        r.raise_for_status()
        return r.json()


async def install_skill(
    skill_md: str,
    slug: str,
    tenant_id: str,
    source_url: str,
    reviewer_agent_id: str,
    transcript_sha256: str,
    learned_by_agent_id: str,
) -> dict:
    """T2.6 — persist an approved draft into the tenant skills library.

    Injects the provenance frontmatter block (spec §1.6) into the draft,
    then attempts to install via the api endpoint. On a slug collision
    (HTTP 409) the loop appends ``-v2``, ``-v3``, … and re-attempts up to
    ``SLUG_MAX_RETRIES`` (5) times before raising ``SlugExhausted``. Any
    non-409 ``HTTPStatusError`` propagates unchanged so the workflow can
    surface the underlying api failure with its original status code.

    Returns whatever the api endpoint returned verbatim (typically
    ``{"skill_id": str, "path": str}``); the workflow uses ``skill_id``
    for the subsequent ``diffuse_learning`` call (T2.7).
    """
    md = _inject_provenance(
        skill_md,
        source_url=source_url,
        reviewer_agent_id=reviewer_agent_id,
        transcript_sha256=transcript_sha256,
        learned_by_agent_id=learned_by_agent_id,
    )
    for attempt in range(1, SLUG_MAX_RETRIES + 1):
        candidate = slug if attempt == 1 else f"{slug}-v{attempt}"
        try:
            return await _install_via_api(
                skill_md=md,
                slug=candidate,
                tenant_id=tenant_id,
                learned_by_agent_id=learned_by_agent_id,
                source_url=source_url,
            )
        except httpx.HTTPStatusError as e:
            if e.response.status_code != 409:
                raise
            # 409 → slug already taken, try the next ``-vN`` suffix.
    raise SlugExhausted(
        f"could not allocate slug for {slug!r} after {SLUG_MAX_RETRIES} attempts"
    )


# ── T2.7: diffuse_learning ─────────────────────────────────────────────
# After a skill is installed (T2.6), we POST a tenant-scoped observation
# describing the newly-learned capability so peer agents in the same
# tenant can discover it via semantic recall (``search_knowledge``).
# Design call (per the plan): observation is tenant-scoped, NOT
# agent-scoped — the whole point is stigmergy, every agent in the tenant
# should be able to find what Luna just learned.
#
# Soft-fail semantics are load-bearing: the workflow caller treats a
# KG-down failure as "skill installed + usable, semantic discovery
# delayed" and caches the pending diffusion (§1.11 in the plan). If we
# raised here, an unrelated KG outage would block every skill install
# Luna ever tries — which is the wrong call. Any Exception path collapses
# to ``{observation_id: None, soft_failed: True, error: str(e)}``.
#
# Endpoint contract note: the spec writes the URL as
# ``/api/v1/knowledge/observations``, but the api today exposes
# tenant-scoped observation ingest at ``/api/v1/memory/remember`` (JWT-
# gated) and entity-only CRUD on ``/api/v1/knowledge/entities/internal``
# (X-Internal-Key). Neither matches the spec verbatim; T4.x is expected
# to ship the literal ``/knowledge/observations`` internal-key endpoint.
# We POST to the spec'd URL so the moment T4.x lands we work end-to-end
# without another patch; until then this path 404s and we soft-fail —
# which is exactly the contract this function promises.
async def _record_observation(text: str, metadata: dict) -> dict:
    """POST a tenant-scoped observation to the api's KG endpoint.

    10s timeout — observation writes are cheap (single insert + an
    embedding hop) and we don't want to pin the install workflow on
    a slow KG. The activity wrapper (T3.1) layers retry policy on top.

    Split out as its own helper so the T2.7 unit tests can patch the
    network hop without a live api. Same X-Internal-Key auth as the
    other T2.x wrappers (see ``_API_INTERNAL_KEY`` at the top of this
    module).
    """
    async with httpx.AsyncClient(timeout=10.0) as client:
        r = await client.post(
            f"{_API_BASE}/api/v1/knowledge/observations",
            json={"text": text, "metadata": metadata},
            headers={"X-Internal-Key": _API_INTERNAL_KEY},
        )
        r.raise_for_status()
        return r.json()


async def diffuse_learning(
    skill_id: str,
    source_url: str,
    capabilities: list[str],
) -> dict:
    """T2.7 — broadcast the new skill to peer agents (stigmergy event).

    Spec §1.1 + design call: writes a tenant-scoped KG observation so
    every agent in the tenant (not just Luna) can find the new
    capability via semantic recall. The observation text embeds the
    source URL, the human-readable capability list, and the skill_id so
    a recall hit can jump straight to the installed skill.

    Returns:
        ``{"observation_id": str, "soft_failed": False}`` on success.
        ``{"observation_id": None, "soft_failed": True, "error": str}``
        on any failure (KG down, timeout, malformed response, etc.).

    NEVER raises. The workflow caller (T3.1) treats a soft-fail as
    "skill is installed and usable; semantic discovery may be delayed"
    and caches the pending diffusion for a later retry — install MUST
    NOT abort on a KG outage.
    """
    text = (
        f"Learned new capability from {source_url}. "
        f"Capabilities: {', '.join(capabilities)}. Skill: {skill_id}."
    )
    metadata = {
        "kind": "luna_learn",
        "skill_id": skill_id,
        "source_url": source_url,
        "capabilities": capabilities,
    }
    try:
        r = await _record_observation(text, metadata)
        return {"observation_id": r["observation_id"], "soft_failed": False}
    except Exception as e:  # noqa: BLE001 — soft-fail is the contract
        return {"observation_id": None, "soft_failed": True, "error": str(e)}


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
