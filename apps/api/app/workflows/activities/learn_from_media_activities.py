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

import os
from functools import wraps
from pathlib import Path

import httpx
from temporalio import activity


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
async def act_write_cache(*args, **kwargs) -> dict:
    """T3.3 stub — returns a cache marker so T3.2c/e branches can proceed."""
    return {"ok": True, "data": {"cache_dir": "stub"}, "error": None}


@activity.defn
async def act_write_quarantine(*args, **kwargs) -> dict:
    """T3.3 stub — returns a quarantine marker so T3.2b/c/d branches can proceed."""
    return {"ok": True, "data": {"quarantine_dir": "stub"}, "error": None}


@activity.defn
async def act_log_test_fail(*args, **kwargs) -> dict:
    """T4.4e stub — audit row for test_failed branch (T3.2d)."""
    return {"ok": True, "data": None, "error": None}


@activity.defn
async def act_notify_session(*args, **kwargs) -> dict:
    raise NotImplementedError("T3.5")


@activity.defn
async def act_probe_attachment(*args, **kwargs) -> dict:
    raise NotImplementedError("T4.4b")
