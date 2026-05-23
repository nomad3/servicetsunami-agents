"""Breadcrumb writer for the tool_audit_drops table.

P0c §6. When the main `tool_calls` write cannot proceed (tenant_id
unresolvable, SQL failure, executor scheduling failure), this module
writes a minimal breadcrumb to `tool_audit_drops` so operators can
correlate against session_events + chat_messages by timestamp.

NO tenant_id field on the breadcrumb table — the whole point is we
couldn't resolve one. Top-level argument keys only (no values) to
avoid leaking PII through the safety net.

Design: docs/plans/2026-05-23-p0c-audit-log-fail-loud.md §6.

Connection pool: small + separate from the main `_get_engine()` pool
so a congested main DB (which is often *why* tool_calls writes are
failing) doesn't also block the drops table. If the breadcrumb write
itself fails, we fall through to logs + Prometheus counters only —
that's the last layer of accountability.

Never raises.
"""
from __future__ import annotations

import logging
import os
import threading
import uuid as _uuid
from typing import Any, Optional

logger = logging.getLogger(__name__)

# Module-singleton small engine + lock — created on first use.
_engine: Any = None
_engine_lock = threading.Lock()


def _get_breadcrumb_engine() -> Any:
    """Lazily create a small SQLAlchemy engine for the drops table.

    Separate pool from the main `_log_call` engine in tool_audit.py
    so a saturated main pool doesn't also starve breadcrumb writes.
    Pool size of 2 is intentional — drops are rare in healthy
    operation, and a tiny pool keeps the contention surface small.
    """
    global _engine
    if _engine is not None:
        return _engine
    with _engine_lock:
        if _engine is not None:
            return _engine
        url = os.environ.get("DATABASE_URL")
        if not url:
            logger.warning(
                "audit_breadcrumb: DATABASE_URL not set — drops table "
                "writes disabled (logs + counters remain as last line)."
            )
            return None
        try:
            from sqlalchemy import create_engine

            _engine = create_engine(
                url,
                pool_size=2,
                max_overflow=1,
                pool_pre_ping=True,
                # Short timeout — if the DB is unreachable in 2s, we
                # don't want to block the tool path. Fall through to
                # logs + counters.
                connect_args={"connect_timeout": 2},
            )
            logger.info(
                "audit_breadcrumb: engine initialized (separate pool, "
                "size=2) for tool_audit_drops writes"
            )
        except Exception as e:  # noqa: BLE001
            logger.warning(
                "audit_breadcrumb: failed to init engine — drops "
                "disabled: %s", e,
            )
            _engine = None
        return _engine


def write_drop(
    *,
    tool_name: str,
    drop_reason: str,
    tier: Optional[str] = None,
    args_keys: Optional[list[str]] = None,
    error_message: Optional[str] = None,
) -> None:
    """Write a single breadcrumb row. Never raises.

    Best-effort. If the breadcrumb DB itself is unreachable, fall
    through to logger.warning so the operator has at least the log
    line. The Prometheus counter at the call site is independent of
    this writer and ticks regardless.
    """
    eng = _get_breadcrumb_engine()
    if eng is None:
        return
    try:
        from sqlalchemy import text

        # Cap args_keys to 20 keys, each <=128 chars. Hard caps to
        # prevent a pathological tool from blowing up the breadcrumb
        # table.
        safe_keys: Optional[list[str]] = None
        if args_keys:
            safe_keys = [str(k)[:128] for k in args_keys[:20]]
        safe_error = (
            error_message[:600] if isinstance(error_message, str) else None
        )
        with eng.begin() as conn:
            conn.execute(
                text("""
                    INSERT INTO tool_audit_drops
                        (id, tool_name, drop_reason, tier,
                         args_keys, error_message)
                    VALUES
                        (:id, :tool_name, :drop_reason, :tier,
                         :args_keys, :error_message)
                """),
                {
                    "id": str(_uuid.uuid4()),
                    "tool_name": tool_name[:200],
                    "drop_reason": drop_reason[:64],
                    "tier": tier[:32] if tier else None,
                    "args_keys": safe_keys,
                    "error_message": safe_error,
                },
            )
    except Exception as e:  # noqa: BLE001 — last-line discipline
        logger.warning(
            "audit_breadcrumb: write_drop FAILED for %s "
            "(drop_reason=%s): %s — log + counter remain",
            tool_name, drop_reason, e,
        )
