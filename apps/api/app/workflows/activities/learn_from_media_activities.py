"""Temporal activities for LearnFromMediaWorkflow (spec §1.10).

Each ``@activity.defn``-decorated callable is a thin httpx call to the
mcp-server HTTP shim (T1.2a, registered at ``/agentprovision/v1/tools/``).
The ``_wrap`` decorator translates ``httpx.HTTPStatusError`` from that
shim into the result envelope shape consumed by ``LearnFromMediaWorkflow``
(T3.2)::

    {"ok": bool, "data": dict | None, "error": {"type": str, "message": str} | None}

The body's ``error_type`` field is AUTHORITATIVE for branching; the
``_STATUS_TO_TYPE`` map is only a fast-path fallback when the body is
absent or unparseable (matches the T1.2a shim contract — plan §1.10).
"""
from __future__ import annotations

import json
import os
from functools import wraps
from pathlib import Path

import httpx
from temporalio import activity


# ---------------------------------------------------------------------------
# Tenant workspace layout (spec §1.11 + §2 — T3.3)
# ---------------------------------------------------------------------------
# Cache:      _tenant/<uuid>/_learning_cache/<job_id>/             (7-day TTL)
# Quarantine: _tenant/<uuid>/_learning_quarantine/<job_id>/        (30-day TTL)
# A given job_id MUST NOT appear in both at the same time — see
# ``CacheAndQuarantineConflict``. Base path is module-level so tests can
# ``monkeypatch.setattr`` it onto a ``tmp_path`` without touching the FS.
_WORKSPACE_BASE = Path("/var/agentprovision/workspaces")


def _tenant_root(tenant_id: str) -> Path:
    """Resolve ``_tenant/<uuid>/`` under the configured workspace base."""
    return _WORKSPACE_BASE / "_tenant" / tenant_id


class CacheAndQuarantineConflict(Exception):
    """Spec §1.11: a job_id may exist in cache OR quarantine, never both."""


# Default targets the in-cluster service name on the docker-compose / Helm
# network. NOT 8001 — that's the FastMCP streamable port. The REST FastAPI
# shim added in T1.2a lives on 8000 (plan §0e).
_MCP_BASE = os.environ.get("MCP_SERVER_BASE", "http://mcp-tools:8000")
# Matches the existing ``server.py`` convention (plan §0d / T1.2a).
_TOOL_PREFIX = "/agentprovision/v1/tools"
_HEADERS = {"X-Internal-Key": os.environ.get("MCP_API_KEY", "")}


# Status → typed-exception-name fast-path. Mirrors ``learning.py`` exception
# classes. The body's ``error_type`` overrides this map when present.
_STATUS_TO_TYPE = {
    451: "MediaPrivate",
    404: "MediaNotFound",
    403: "MediaGeoBlocked",
    429: "MediaAntiScrape",
    413: "MediaTooLong",
    422: "DraftInvalid",
    424: "DraftForbiddenShellout",
    503: "ReviewerNotProvisioned",
    504: "ReviewTimeout",
    409: "SlugExhausted",
}


async def _call_mcp(tool: str, payload: dict) -> dict:
    """POST to the mcp-server shim and return the parsed body.

    Raises ``httpx.HTTPStatusError`` on non-2xx so ``_wrap`` can translate
    the typed-error envelope. Kept as a module-level callable so tests can
    patch it directly (plan §T3.2 NEW-IMPORTANT-2).
    """
    async with httpx.AsyncClient(timeout=120.0) as client:
        r = await client.post(
            f"{_MCP_BASE}{_TOOL_PREFIX}/{tool}",
            json=payload,
            headers=_HEADERS,
        )
        r.raise_for_status()
        return r.json()


def _wrap(coro):
    """Convert (success | HTTPStatusError) → Temporal result envelope.

    Body's ``error_type`` is AUTHORITATIVE; ``_STATUS_TO_TYPE`` is only the
    fast-path fallback when the body is missing or unparseable.
    """

    @wraps(coro)
    async def wrapper(*args, **kwargs):
        try:
            data = await coro(*args, **kwargs)
            return {"ok": True, "data": data, "error": None}
        except httpx.HTTPStatusError as e:
            try:
                body = e.response.json()
            except Exception:
                body = {}
            etype = body.get("error_type") or _STATUS_TO_TYPE.get(
                e.response.status_code, "UnknownError"
            )
            return {
                "ok": False,
                "data": None,
                "error": {"type": etype, "message": body.get("message", str(e))},
            }

    return wrapper


# ---------------------------------------------------------------------------
# 7 MCP-backed activities (T3.1)
# ---------------------------------------------------------------------------

@activity.defn
@_wrap
async def act_extract_media(url: str, max_duration_s: int = 900) -> dict:
    return await _call_mcp(
        "extract_media", {"url": url, "max_duration_s": max_duration_s}
    )


@activity.defn
@_wrap
async def act_transcribe_url(audio_path: str) -> dict:
    """Transcribe + delete audio on success (spec §1.12).

    Failure path leaves the file in place so the T3.3 quarantine bundle
    can copy it (and the periodic orphan sweep eventually clears the rest).
    """
    try:
        result = await _call_mcp("transcribe_url", {"audio_path": audio_path})
    except httpx.HTTPStatusError:
        # Re-raise so ``_wrap`` builds the typed-error envelope; file
        # intentionally NOT deleted on failure (quarantine consumes it).
        raise
    # Success path: delete the audio file.
    p = Path(audio_path)
    if p.exists():
        p.unlink(missing_ok=True)
    return result


@activity.defn
@_wrap
async def act_synthesize_skill_draft(
    transcript: str,
    source_url: str,
    hints: list[str] | None = None,
) -> dict:
    return await _call_mcp(
        "synthesize_skill_draft",
        {
            "transcript": transcript,
            "source_url": source_url,
            "hints": hints or [],
        },
    )


@activity.defn
@_wrap
async def act_dispatch_skill_review(
    skill_md: str,
    transcript: str,
    source_url: str,
    synthetic_test_input: dict,
    synthetic_test_expected: dict,
) -> dict:
    return await _call_mcp(
        "dispatch_skill_review",
        {
            "skill_md": skill_md,
            "transcript": transcript,
            "source_url": source_url,
            "synthetic_test_input": synthetic_test_input,
            "synthetic_test_expected": synthetic_test_expected,
        },
    )


@activity.defn
@_wrap
async def act_run_synthetic_test(
    skill_md: str,
    test_input: dict,
    test_expected: dict,
) -> dict:
    return await _call_mcp(
        "run_synthetic_test",
        {
            "skill_md": skill_md,
            "test_input": test_input,
            "test_expected": test_expected,
        },
    )


@activity.defn
@_wrap
async def act_install_skill(
    skill_md: str,
    slug: str,
    tenant_id: str,
    source_url: str,
    reviewer_agent_id: str,
    transcript_sha256: str,
    learned_by_agent_id: str,
) -> dict:
    return await _call_mcp(
        "install_skill",
        {
            "skill_md": skill_md,
            "slug": slug,
            "tenant_id": tenant_id,
            "source_url": source_url,
            "reviewer_agent_id": reviewer_agent_id,
            "transcript_sha256": transcript_sha256,
            "learned_by_agent_id": learned_by_agent_id,
        },
    )


@activity.defn
@_wrap
async def act_diffuse_learning(
    skill_id: str,
    source_url: str,
    capabilities: list[str],
) -> dict:
    return await _call_mcp(
        "diffuse_learning",
        {
            "skill_id": skill_id,
            "source_url": source_url,
            "capabilities": capabilities,
        },
    )


# ---------------------------------------------------------------------------
# Minimal stub bodies — T3.2b–f need these callable so the workflow body
# can branch through cache/quarantine/audit dispatch. Real bodies land in
# T3.3 (cache/quarantine), T3.5 (notify), T4.4b (probe_attachment),
# T4.4e (test-fail audit). For T3.2b–f these return the result-envelope
# shape so workflow steps can proceed without behavioural coupling.
# ---------------------------------------------------------------------------

@activity.defn
async def act_write_cache(
    tenant_id: str,
    job_id: str,
    transcript: str,
    draft: dict,
    last_review: dict | None,
    last_test: dict | None,
) -> dict:
    """Persist resumable cache bundle under ``_learning_cache/<job_id>/``.

    Enforces the spec §1.11 mutex: if a quarantine entry already exists for
    this ``job_id``, refuse (raising ``CacheAndQuarantineConflict``) so the
    caller cannot leave the workspace in a both-locations state. The
    quarantine layout uses ``<YYYY-MM-DD-HHMMSS>-<slug>`` directory names
    (spec §2), so we match by suffix on ``job_id``.
    """
    cdir = _tenant_root(tenant_id) / "_learning_cache" / job_id
    qdir = _tenant_root(tenant_id) / "_learning_quarantine"
    if qdir.exists() and any(
        d.name.endswith(job_id) for d in qdir.iterdir() if d.is_dir()
    ):
        raise CacheAndQuarantineConflict(f"{job_id} already quarantined")
    cdir.mkdir(parents=True, exist_ok=True)
    (cdir / "transcript.txt").write_text(transcript or "")
    # Write skill_md to draft.md for operator inspection, plus the FULL
    # draft dict to draft.json so the resume path (T3.4) has slug +
    # synthetic_test_input + synthetic_test_expected intact. Losing those
    # on resume would force re-synth and break determinism (same skill_id,
    # same install path).
    (cdir / "draft.md").write_text((draft or {}).get("skill_md", ""))
    if draft:
        (cdir / "draft.json").write_text(json.dumps(draft))
    if last_review:
        (cdir / "review.json").write_text(json.dumps(last_review))
    if last_test:
        (cdir / "test.json").write_text(json.dumps(last_test))
    return {"cache_dir": str(cdir)}


@activity.defn
async def act_write_quarantine(
    tenant_id: str,
    job_id: str,
    transcript: str,
    draft: dict,
    review: dict,
    test_result: dict | None,
    abort_reason: str,
) -> dict:
    """Persist final-failure bundle under ``_learning_quarantine/<job_id>/``.

    Enforces the spec §1.11 mutex: if a cache entry exists for this
    ``job_id``, refuse (raising ``CacheAndQuarantineConflict``). Caller is
    expected to clear the cache entry before quarantining.
    """
    cdir = _tenant_root(tenant_id) / "_learning_cache" / job_id
    if cdir.exists():
        raise CacheAndQuarantineConflict(f"{job_id} already in cache")
    qdir = _tenant_root(tenant_id) / "_learning_quarantine" / job_id
    qdir.mkdir(parents=True, exist_ok=True)
    (qdir / "transcript.txt").write_text(transcript or "")
    (qdir / "draft.md").write_text((draft or {}).get("skill_md", ""))
    (qdir / "review.json").write_text(json.dumps(review) if review else "{}")
    if test_result:
        (qdir / "test_result.json").write_text(json.dumps(test_result))
    (qdir / "abort_reason.txt").write_text(abort_reason)
    return {"quarantine_dir": str(qdir)}


@activity.defn
async def act_read_cache(tenant_id: str, job_id: str) -> dict:
    """T3.4 — read cached resume bundle under ``_learning_cache/<job_id>/``.

    Returns the parsed bundle so the workflow can short-circuit to the
    failed step. Two cache shapes per spec §1.11 + §3:

    * **Reviewer-down**: ``transcript.txt`` + ``draft.md`` only. No
      ``review.json``. Workflow resumes from step 4 (review).
    * **KG-down (diffuse soft-fail)**: ``transcript.txt`` + ``draft.md`` +
      ``review.json`` (holding ``{skill_id, capabilities}`` from the
      install step). Workflow resumes from step 7 (diffuse).

    The activity is read-only — it does NOT clear the cache. The resume
    flow re-runs the failed step and overwrites the cache (on subsequent
    failure) or proceeds past it (on success) via the normal pipeline.

    Returns ``{ok: False, error: {type: "CacheNotFound"}}`` when the
    directory is missing, so the workflow surfaces a clean error envelope
    instead of crashing.
    """
    cdir = _tenant_root(tenant_id) / "_learning_cache" / job_id
    if not cdir.exists():
        return {
            "ok": False,
            "data": None,
            "error": {"type": "CacheNotFound", "message": f"no cache for job {job_id}"},
        }
    transcript_p = cdir / "transcript.txt"
    draft_json_p = cdir / "draft.json"
    draft_md_p = cdir / "draft.md"
    review_p = cdir / "review.json"
    test_p = cdir / "test.json"
    transcript = transcript_p.read_text() if transcript_p.exists() else ""
    # Prefer the full draft JSON (slug + test inputs intact); fall back to
    # the skill_md-only shape for legacy caches written before T3.4.
    draft: dict = {}
    if draft_json_p.exists():
        try:
            draft = json.loads(draft_json_p.read_text())
        except (json.JSONDecodeError, ValueError):
            draft = {}
    if not draft and draft_md_p.exists():
        draft = {"skill_md": draft_md_p.read_text()}
    last_review = None
    if review_p.exists():
        try:
            last_review = json.loads(review_p.read_text())
        except (json.JSONDecodeError, ValueError):
            last_review = None
    last_test = None
    if test_p.exists():
        try:
            last_test = json.loads(test_p.read_text())
        except (json.JSONDecodeError, ValueError):
            last_test = None
    return {
        "ok": True,
        "data": {
            "transcript": transcript,
            "draft": draft,
            "last_review": last_review,
            "last_test": last_test,
        },
        "error": None,
    }


@activity.defn
async def act_log_test_fail(*args, **kwargs) -> dict:
    """T4.4e stub — audit row for test_failed branch (T3.2d)."""
    return {"ok": True, "data": None, "error": None}


# ---------------------------------------------------------------------------
# T3.5 — Completion notification (spec §2 step 8)
# ---------------------------------------------------------------------------
# Writes a ChatMessage(role="agent", context.kind="learn_complete") to the
# session that originated the learning intent. The existing WhatsApp
# message-out plumbing (chat → outbound) keys on agent-role rows for that
# session and surfaces the content to the user. The activity is a thin
# DB-write boundary: keeping it as an activity (not a workflow step) means
# Temporal handles the retry on transient DB errors without polluting
# workflow state.
#
# The content string is rendered here so the workflow stays free of
# user-facing copy (single source of truth, easier to localise later).
# spec §3 failure rows are already rendered by the workflow into
# ``result["message"]`` before this activity is called — for failure
# envelopes we just forward that string verbatim. Success envelopes get
# the canonical "learned X. capabilities: Y, Z. source: <url>" body.


def _render_notify_body(result: dict) -> str:
    """Render the user-facing message body from a workflow ``result`` dict.

    Success envelope (spec §2 step 8):
        {status: "success", skill_name, capabilities, source_url, ...}
    Failure envelopes (spec §3 rows):
        {status: "<...>_failed" | "quarantined" | ..., message: "<copy>"}

    Failure envelopes already carry the spec §3 user-facing copy in
    ``message`` — forward it verbatim. Unknown shapes fall back to a
    generic "learning finished" string so the user always gets *some*
    closing notification (the workflow shouldn't silently drop the user).
    """
    status = (result or {}).get("status")
    if status == "success":
        name = result.get("skill_name") or "<unnamed>"
        caps = [c for c in (result.get("capabilities") or []) if c]
        caps_str = ", ".join(caps) if caps else "(none)"
        src = result.get("source_url") or "<unknown>"
        body = f"✓ learned '{name}'. Capabilities: {caps_str}. Source: {src}"
        if result.get("resumed"):
            body = f"{body} (resumed)"
        if result.get("diffuse_cached"):
            body = f"{body} (diffuse pending)"
        return body
    msg = (result or {}).get("message")
    if msg:
        return str(msg)
    return f"learning finished: {status or 'unknown'}"


@activity.defn
async def act_notify_session(session_id: str, result: dict) -> dict:
    """Write a ChatMessage(role="agent", context.kind="learn_complete") row.

    Returns the standard envelope ``{ok, data, error}``:
      * success → ``{ok: True, data: {message_id, content}}``
      * session not found → ``{ok: False, error: {type: "SessionNotFound", ...}}``
      * unexpected DB error → ``{ok: False, error: {type: "NotifyWriteFailed", ...}}``

    Failures here MUST NOT raise — the workflow already considers the
    learning result final at this point. A notify failure would only
    cost the user the closing message, not invalidate the install.
    """
    import uuid as _uuid

    # Late imports keep the activity module importable in the Temporal
    # workflow sandbox (which forbids eager DB/ORM imports — see §0g).
    from sqlalchemy.orm import Session

    from app.db.session import SessionLocal
    from app.models.chat import ChatMessage, ChatSession

    body = _render_notify_body(result or {})
    context = {"kind": "learn_complete", **(result or {})}

    db: Session = SessionLocal()
    try:
        try:
            sess_uuid = _uuid.UUID(str(session_id))
        except (ValueError, TypeError) as e:
            return {
                "ok": False,
                "data": None,
                "error": {"type": "InvalidSessionId", "message": str(e)},
            }
        session = db.query(ChatSession).filter(ChatSession.id == sess_uuid).first()
        if session is None:
            return {
                "ok": False,
                "data": None,
                "error": {
                    "type": "SessionNotFound",
                    "message": f"session_id={session_id} not found",
                },
            }
        try:
            message = ChatMessage(
                session_id=session.id,
                role="agent",
                content=body,
                context=context,
            )
            db.add(message)
            db.commit()
            db.refresh(message)
        except Exception as e:  # noqa: BLE001 — envelope contract per docstring
            db.rollback()
            return {
                "ok": False,
                "data": None,
                "error": {"type": "NotifyWriteFailed", "message": str(e)},
            }
        return {
            "ok": True,
            "data": {"message_id": str(message.id), "content": body},
            "error": None,
        }
    finally:
        db.close()


@activity.defn
async def act_probe_attachment(*args, **kwargs) -> dict:
    raise NotImplementedError("T4.4b")
