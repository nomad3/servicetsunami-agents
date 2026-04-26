"""Tool-call audit — logs every MCP tool invocation to the tool_calls table.

Wraps FastMCP's `mcp.call_tool` so we capture (tenant_id, tool_name,
arguments, result, duration) for every call, success or failure. This
gives us the missing observability gap that the failure-only stderr
parser in code-worker can't fill: which tools were ACTUALLY called
when an agent claims to have grounded a response.

Audit failures NEVER propagate to the caller. If logging breaks, we
log a warning and let the real tool result through unchanged.

Correlation back to a chat turn happens at query time, not write time:
    JOIN chat_messages cm ON tool_calls.tenant_id = cm.tenant_id
                          AND tool_calls.started_at BETWEEN cm.user_msg_at
                                                       AND cm.assistant_msg_at
This is a join cost we accept in exchange for keeping the MCP call
path stateless.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import time
import uuid
from typing import Any

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine

from src.mcp_auth import resolve_tenant_id

logger = logging.getLogger(__name__)

_engine: Engine | None = None

# Tools whose arguments are inherently sensitive (auth tokens, raw PII bodies,
# user-provided long text). Their args are reduced to a key-list-only summary
# before persistence. Add to this set conservatively; it's safer to redact than
# to leak.
_SENSITIVE_ARG_TOOLS: frozenset[str] = frozenset({
    "connect_mcp_server",       # custom_headers may carry bearer tokens
    "register_webhook",         # headers may carry bearer tokens
    "send_email",               # body / html
    "reply_to_email",           # body / html
    "send_email_attachment",    # body
    "create_drive_file",        # content
    "create_calendar_event",    # description
    "record_observation",       # raw observation text (PII-ish)
    "create_entity",            # description / attributes free-form
    "update_entity",
})

# Compiled patterns for value-side credential detection.
_CREDENTIAL_VALUE_PATTERNS = (
    re.compile(r"\bBearer\s+\S{12,}", re.IGNORECASE),
    re.compile(r"\bsk-[A-Za-z0-9_\-]{20,}"),       # OpenAI/Anthropic-style
    re.compile(r"\bxox[baprs]-\S{10,}", re.IGNORECASE),  # Slack
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{30,}"),   # GitHub PAT
    re.compile(r"\beyJ[A-Za-z0-9_\-]+\.[A-Za-z0-9_\-]+\.[A-Za-z0-9_\-]+"),  # JWT
)


def _redact_value(v: Any) -> Any:
    """If a value is a string containing a credential-shaped substring, redact."""
    if isinstance(v, str):
        for pat in _CREDENTIAL_VALUE_PATTERNS:
            if pat.search(v):
                return "[redacted-credential]"
    return v


def _get_engine() -> Engine | None:
    """Lazy singleton — creates the engine the first time we need it."""
    global _engine
    if _engine is not None:
        return _engine
    dsn = os.environ.get("DATABASE_URL", "")
    if not dsn:
        logger.warning("tool_audit: DATABASE_URL not set, audit disabled")
        return None
    try:
        # Force psycopg2 driver — sqlalchemy 2.x defaults to async asyncpg
        # which is incompatible with our sync execute() pattern.
        if dsn.startswith("postgresql://"):
            dsn = dsn.replace("postgresql://", "postgresql+psycopg2://", 1)
        # Pool sized for bursty parallel tool fan-out (e.g. coalition turns
        # firing 5+ tools at once). Audit writes are fire-and-forget on a
        # background task in the same event loop, but the connection is
        # still held briefly per insert.
        _engine = create_engine(dsn, pool_pre_ping=True, pool_size=10, max_overflow=20)
        logger.info("tool_audit: engine initialized")
        return _engine
    except Exception as e:
        logger.warning("tool_audit: failed to init engine — audit disabled: %s", e)
        return None


def _truncate(s: str, limit: int = 800) -> str:
    if s is None:
        return ""
    if len(s) <= limit:
        return s
    return s[:limit] + "…(truncated)"


def _log_call(
    tenant_id: str | None,
    tool_name: str,
    arguments: dict[str, Any],
    result_status: str,
    result_summary: str | None,
    error: str | None,
    duration_ms: int,
    started_at_unix: float,
) -> None:
    """Persist one audit row. Never raises."""
    eng = _get_engine()
    if eng is None or not tenant_id:
        return
    try:
        # Three-layer redaction:
        #   1. Tools known to carry credentials/PII collapse to a key-list only.
        #   2. Argument keys that look credential-shaped redact the value.
        #   3. Argument values that look credential-shaped (Bearer/JWT/sk-/xoxb-/gh*_)
        #      redact, even when keyed under an innocuous name.
        if tool_name in _SENSITIVE_ARG_TOOLS:
            safe_args: dict[str, Any] = {
                "_redacted": True,
                "keys": list((arguments or {}).keys())[:20],
            }
        else:
            safe_args = {}
            for k, v in (arguments or {}).items():
                if any(t in k.lower() for t in ("token", "secret", "password", "api_key")):
                    safe_args[k] = "[redacted]"
                else:
                    safe_args[k] = _redact_value(v)
        with eng.begin() as conn:
            conn.execute(
                text("""
                    INSERT INTO tool_calls
                        (id, tenant_id, tool_name, arguments,
                         result_status, result_summary, error,
                         duration_ms, started_at, ended_at)
                    VALUES
                        (:id, CAST(:tenant_id AS uuid), :tool_name, CAST(:args AS jsonb),
                         :result_status, :result_summary, :error,
                         :duration_ms, to_timestamp(:started_at), NOW())
                """),
                {
                    "id": str(uuid.uuid4()),
                    "tenant_id": tenant_id,
                    "tool_name": tool_name,
                    "args": json.dumps(safe_args, default=str)[:10000],
                    "result_status": result_status,
                    "result_summary": _truncate(result_summary or ""),
                    "error": _truncate(error or "", 600) if error else None,
                    "duration_ms": duration_ms,
                    "started_at": started_at_unix,
                },
            )
    except Exception as e:
        logger.warning("tool_audit: write failed for %s: %s", tool_name, e)


def install_audit(mcp_server) -> None:
    """Wrap the tool dispatch path with audit logging.

    The lowlevel server's CallToolRequest handler captures FastMCP's
    `self.call_tool` as a bound method at FastMCP __init__ time. By
    the time we run, the bound reference is frozen — patching the
    class or the instance method has no effect on the handler's
    closure. So we replace the registered handler itself.

    The handler we install does:
      1. record the call start time and resolve tenant_id
      2. dispatch through the ORIGINAL handler (which calls the bound
         FastMCP.call_tool internally)
      3. log the call to tool_calls in the finally block

    Idempotent. Called once at server startup, after
    `import src.mcp_tools` has triggered all @mcp.tool() decorators.
    """
    if getattr(mcp_server, "_tool_audit_installed", False):
        return
    try:
        from mcp.types import CallToolRequest
    except Exception as e:
        logger.warning("tool_audit: cannot import CallToolRequest: %s", e)
        return

    lowlevel = getattr(mcp_server, "_mcp_server", None)
    handlers = getattr(lowlevel, "request_handlers", None) if lowlevel else None
    if not handlers or CallToolRequest not in handlers:
        logger.warning("tool_audit: CallToolRequest handler not found, audit disabled")
        return

    original_handler = handlers[CallToolRequest]

    async def audited_handler(req):
        tool_name = ""
        arguments: dict[str, Any] = {}
        try:
            tool_name = req.params.name
            arguments = req.params.arguments or {}
        except Exception:
            pass
        logger.info(
            "tool_audit: tool=%s args_keys=%s",
            tool_name, list(arguments.keys())[:5],
        )
        started = time.monotonic()
        started_unix = time.time()
        try:
            ctx = mcp_server.get_context()
            tenant_id = resolve_tenant_id(ctx)
        except Exception:
            tenant_id = None
        result = None
        error_msg = None
        status = "ok"
        try:
            result = await original_handler(req)
            return result
        except Exception as exc:
            status = "error"
            error_msg = f"{type(exc).__name__}: {exc}"
            raise
        finally:
            # Fire-and-forget the DB write so pool waits never block the
            # tool path. Wrapped in try/except so even task scheduling
            # failures cannot propagate.
            try:
                duration_ms = int((time.monotonic() - started) * 1000)
                summary = ""
                if status == "ok":
                    summary = _truncate(repr(result), 800)
                payload = {
                    "tenant_id": tenant_id,
                    "tool_name": tool_name,
                    "arguments": dict(arguments) if arguments else {},
                    "result_status": status,
                    "result_summary": summary,
                    "error": error_msg,
                    "duration_ms": duration_ms,
                    "started_at_unix": started_unix,
                }
                loop = asyncio.get_running_loop()
                loop.run_in_executor(None, lambda p=payload: _log_call(**p))
            except Exception:
                pass

    handlers[CallToolRequest] = audited_handler
    mcp_server._tool_audit_installed = True
    logger.info("tool_audit: installed on lowlevel CallToolRequest handler")
