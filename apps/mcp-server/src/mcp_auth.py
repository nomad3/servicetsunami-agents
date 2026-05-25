"""Tenant authentication for MCP tool calls.

FastMCP's Context.request_context is a RequestContext object, not a dict.
We extract tenant_id and internal_key from HTTP headers passed via the
Streamable HTTP transport.

Phase 4 adds a third auth tier (agent-scoped JWT) and a unified
resolver ``resolve_auth_context`` that returns an ``AuthContext``
covering all four tiers in precedence order:

    agent_token > tenant_jwt > X-Tenant-Id header > X-Internal-Key

Tenant-JWT decoding is not implemented in this server today (the chat
hot path passes X-Tenant-Id explicitly), so the resolver falls through
to the header tier when no agent-token is present. Adding tenant-JWT
decode is a future commit — out of scope for Phase 4 ship gates.

The legacy ``resolve_tenant_id(ctx)`` is kept as a thin wrapper that
delegates to ``resolve_auth_context(ctx).tenant_id`` so existing
callers don't need to migrate eagerly.
"""
import os
import logging
import threading
import time
from typing import Optional

from src.agent_token_verify import (
    AuthContext,
    decode_agent_token_if_present,
)

logger = logging.getLogger(__name__)

INTERNAL_KEY = os.environ.get("MCP_API_KEY", "dev_mcp_key")

# ── Tenancy-mismatch audit-log rate-limiter (SR-6) ───────────────────────
# When ``kind=agent_token``, the JWT's tenant_id claim is authoritative
# but the leaf may also pass an X-Tenant-Id header (default reqwest
# header set, etc.). A mismatch is audit-logged (NOT rejected — the leaf
# may not even know it sent the header). Per SR-6 we rate-limit to one
# log per (tenant, agent, header_value) per 60s — otherwise a misbehaving
# leaf can flood audit_logs.
_MISMATCH_LRU: dict[tuple, float] = {}
_MISMATCH_LRU_LOCK = threading.Lock()
_MISMATCH_TTL_SECONDS = 60.0
_MISMATCH_LRU_MAX = 1024  # soft cap; we don't need a real LRU eviction


def _should_log_mismatch(tenant_id: str, agent_id: str, header_value: str) -> bool:
    """Return True if this (tenant, agent, header) tuple should be
    audit-logged, False if we've already logged it within TTL.

    Side effect: records the timestamp on True.
    """
    key = (tenant_id, agent_id, header_value)
    now = time.monotonic()
    with _MISMATCH_LRU_LOCK:
        last = _MISMATCH_LRU.get(key)
        if last is not None and (now - last) < _MISMATCH_TTL_SECONDS:
            return False
        # Prune oldest if cache grows too large.
        if len(_MISMATCH_LRU) >= _MISMATCH_LRU_MAX:
            oldest_key = min(_MISMATCH_LRU, key=_MISMATCH_LRU.get)
            _MISMATCH_LRU.pop(oldest_key, None)
        _MISMATCH_LRU[key] = now
    return True


def _get_header(ctx, header_name: str) -> Optional[str]:
    """Safely extract an HTTP header from MCP request context.

    FastMCP's lowlevel server (mcp/server/lowlevel/server.py:690) constructs
    a ``RequestContext`` with ``request=<Starlette Request>`` when a tool
    is invoked over HTTP. Both transports populate it:
      * SSE: ``mcp/server/sse.py:244``
      * Streamable-HTTP: ``mcp/server/streamable_http.py:403, 417, 505``
    via ``ServerMessageMetadata(request_context=request)``.

    The headers live at ``ctx.request_context.request.headers`` — NOT
    at ``ctx.request_context.headers`` (which doesn't exist; the prior
    `_get_header` was reading the wrong attribute and always returning
    None, causing every tool call to land as ``tier=anonymous``).

    Lookup order:
      1. ``ctx.request_context.request.headers`` (Starlette Headers) — primary
      2. dict-shaped ``ctx.request_context`` (legacy/test fallback)
      3. Direct attribute fallback on the request_context for stdio-style
         transports that don't carry an HTTP request.

    Header lookups are case-insensitive (Starlette's Headers is already
    case-insensitive; we also try `.lower()` for dict fallbacks).
    """
    if ctx is None:
        return None

    rc = getattr(ctx, 'request_context', None)
    if rc is None:
        return None

    # Primary: read from the Starlette Request that FastMCP attaches at
    # ``request_context.request``. This is what HTTP-transported tool
    # calls actually look like — both SSE and streamable-HTTP paths.
    request_obj = getattr(rc, 'request', None)
    if request_obj is not None:
        req_headers = getattr(request_obj, 'headers', None)
        if req_headers is not None and hasattr(req_headers, 'get'):
            val = req_headers.get(header_name) or req_headers.get(header_name.lower())
            if val is not None:
                return str(val)

    # Legacy fallback 1: dict-shaped request_context (older test fixtures
    # or stdio transports that hand-build a dict). Preserved so existing
    # unit tests don't break.
    if isinstance(rc, dict):
        return rc.get(header_name) or rc.get(header_name.lower())

    # Legacy fallback 2: ``.headers`` attribute directly on
    # request_context. Not present in current FastMCP (which is why we
    # added the primary path above), but kept defensively in case a
    # future transport plumbs it that way.
    headers = getattr(rc, 'headers', None)
    if headers is not None and hasattr(headers, 'get'):
        return headers.get(header_name) or headers.get(header_name.lower())

    # Legacy fallback 3: direct attribute access on the request_context
    # itself. Catches stdio-shaped contexts that pre-populate fields by
    # name (e.g. ``ctx.request_context.x_tenant_id``).
    for attr in [header_name, header_name.lower(), header_name.replace("-", "_").lower()]:
        val = getattr(rc, attr, None)
        if val is not None:
            return str(val)

    return None


def resolve_auth_context(ctx) -> AuthContext:
    """Resolve the auth context for one MCP tool call.

    Precedence (per design §8 step 3):
      1. agent_token    — Authorization: Bearer <jwt> with kind=agent_token
      2. tenant_jwt     — Authorization: Bearer <jwt> with kind=access (NOT
         IMPLEMENTED in this server today; falls through to next tier)
      3. tenant_header  — X-Tenant-Id header
      4. internal_key   — X-Internal-Key header (anonymous tenant)

    On agent_token tenancy mismatch (X-Tenant-Id header set AND its
    value differs from the claim), the claim wins. The mismatch is
    audit-logged at most once per minute per (tenant, agent, header).
    """
    auth_header = _get_header(ctx, "Authorization") or _get_header(ctx, "authorization")

    # ── Tier 1: agent_token ────────────────────────────────────────────
    auth_ctx = decode_agent_token_if_present(auth_header)
    if auth_ctx is not None:
        # Tenancy precedence rule: claim wins, header is ignored. Log on
        # mismatch (rate-limited).
        header_tenant = (
            _get_header(ctx, "X-Tenant-Id") or _get_header(ctx, "tenant_id")
        )
        if (
            header_tenant
            and auth_ctx.tenant_id
            and header_tenant != auth_ctx.tenant_id
        ):
            if _should_log_mismatch(
                auth_ctx.tenant_id, auth_ctx.agent_id or "", header_tenant
            ):
                _audit_tenancy_mismatch(
                    claim_tenant_id=auth_ctx.tenant_id,
                    header_tenant_id=header_tenant,
                    agent_id=auth_ctx.agent_id,
                    task_id=auth_ctx.task_id,
                )
        # User-id passes through (set by chat hot path).
        auth_ctx.user_id = (
            _get_header(ctx, "X-User-Id") or _get_header(ctx, "user_id")
        )
        return auth_ctx

    # ── Tier 2: tenant_jwt (deferred — see module docstring) ───────────

    # ── Tier 3: X-Tenant-Id header ─────────────────────────────────────
    header_tenant = (
        _get_header(ctx, "X-Tenant-Id") or _get_header(ctx, "tenant_id")
    )
    if header_tenant:
        return AuthContext(
            tier="tenant_header",
            tenant_id=header_tenant,
            user_id=(
                _get_header(ctx, "X-User-Id") or _get_header(ctx, "user_id")
            ),
        )

    # ── Tier 4: X-Internal-Key (anonymous tenant) ──────────────────────
    if verify_internal_key(ctx):
        return AuthContext(tier="internal_key")

    return AuthContext(tier="anonymous")


def _audit_tenancy_mismatch(
    *,
    claim_tenant_id: str,
    header_tenant_id: str,
    agent_id: Optional[str],
    task_id: Optional[str],
) -> None:
    """Write a tenancy-mismatch audit log row.

    Best-effort — the audit pipe may be down; never block on this.
    The actual write is delegated to ``tool_audit._log_call`` via a
    structured logger.info so the existing audit pipeline picks it up.
    """
    logger.info(
        "agent_token_tenancy_mismatch",
        extra={
            "event": "agent_token_tenancy_mismatch",
            "claim_tenant_id": claim_tenant_id,
            "header_tenant_id": header_tenant_id,
            "agent_id": agent_id,
            "task_id": task_id,
        },
    )


# Sentinel strings that an LLM agent might pass when it doesn't know
# the real tenant_id. None of these resolve server-side — they exist
# only as legacy hints in older prompt text (Luna's skill.md prior to
# 2026-05-12 told her to use 'auto' if she couldn't find the real
# value). Treat them as "no value supplied" so the next-tier resolver
# (header / fallback arg) takes over.
_TENANT_SENTINELS = {"", "auto", "none", "null", "self", "current"}


def _norm_tenant(value: Optional[str]) -> Optional[str]:
    """Return None for sentinel strings, otherwise the stripped value."""
    if not isinstance(value, str):
        return None
    cleaned = value.strip()
    if cleaned.lower() in _TENANT_SENTINELS:
        return None
    return cleaned


def resolve_tenant_id(ctx, fallback: Optional[str] = None) -> Optional[str]:
    """Extract tenant_id from MCP request context headers.

    Thin wrapper around ``resolve_auth_context`` for legacy callers.
    Returns the tenant_id from whichever tier won, with a defensive
    sentinel-string normaliser layered on top: 'auto' / 'none' / ''
    all collapse to None so the caller can fall through to its own
    fallback.

    Two-arg form added 2026-05-12 for tools that want the old
    `resolve_tenant_id(ctx) or arg_tenant_id` pattern to ALSO ignore
    sentinel values in the arg. Single-arg form preserves
    backward-compat with the 135 existing callsites.
    """
    header_value = _norm_tenant(resolve_auth_context(ctx).tenant_id)
    if header_value:
        return header_value
    return _norm_tenant(fallback)


def resolve_user_id(ctx) -> Optional[str]:
    """Extract the calling user's UUID from MCP request context headers.

    Set by ``cli_session_manager.generate_mcp_config`` so chat-side mutating
    tools (update_skill_definition / update_agent_definition) can attribute
    a revision to the user actually driving the chat session.
    """
    return _get_header(ctx, "X-User-Id") or _get_header(ctx, "user_id")


def verify_internal_key(ctx) -> bool:
    """Verify the X-Internal-Key header matches the configured key."""
    key = _get_header(ctx, "X-Internal-Key") or _get_header(ctx, "internal_key")
    if not key:
        return False
    return key == INTERNAL_KEY
