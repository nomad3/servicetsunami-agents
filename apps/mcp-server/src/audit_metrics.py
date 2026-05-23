"""Prometheus instrumentation for the mcp-server-side audit substrate.

P0c §5. Counters for the three drop sites in tool_audit.py:
  #1: `tenant_id` is None — the breach-hider exposed by round 3 of
      the 2026-05-23 hard-tests.
  #2: SQL INSERT failure on tool_calls.
  #3: Executor scheduling failure.

Companion to `apps/api/app/services/audit_metrics.py` which covers
the api-side audit_log + platform_safety_io surfaces. Kept separate
because the two services have separate Prometheus registries.

Design choices (mirror `apps/api/app/services/emotion_engine_metrics`):

- Import-safe no-op fallback if `prometheus_client` isn't installed.
- Cardinality bounded by tool_name (small enum) + drop_reason
  (3 values) / exception class name. No user-controlled labels.

/metrics endpoint exposure for mcp-server is a follow-up — counters
exist in-process. Scrape config keeps using the api-side endpoint
until then; the breadcrumb table (tool_audit_drops) is the
authoritative durable signal for #1 and #2 in the meantime.

Luna principle: "Audit is not accountability unless failure is visible."
"""
from __future__ import annotations

from typing import Optional

try:
    from prometheus_client import Counter

    _PROMETHEUS_AVAILABLE = True
except ImportError:  # pragma: no cover — graceful degradation

    _PROMETHEUS_AVAILABLE = False

    class _NoOp:
        """No-op stand-in for prometheus_client.Counter when the library
        isn't installed. Keeps call sites identical."""

        def __init__(self, *_args, **_kwargs) -> None:
            pass

        def labels(self, *_args, **_kwargs) -> "_NoOp":
            return self

        def inc(self, *_args, **_kwargs) -> None:
            pass

    Counter = _NoOp  # type: ignore[assignment,misc]


# ── Metric definitions ────────────────────────────────────────────────

# Drop site #1 — tenant_id unresolvable. Each increment is a
# DROPPED audit row. Alert: > 0 in 5min.
tool_audit_drop_total = Counter(
    "tool_audit_drop_total",
    "tool_audit handler dropped an audit row because tenant_id could "
    "not be resolved from auth context or arguments. Each increment "
    "is a tool invocation with no DB forensic record. THIS HID THE "
    "ROUND-3 BREACH. Alert > 0 in 5min.",
    labelnames=("reason", "tool_name"),
)

# Drop site #2 — SQL INSERT into tool_calls failed.
tool_audit_write_failed_total = Counter(
    "tool_audit_write_failed_total",
    "tool_audit handler's SQL INSERT into tool_calls raised — audit "
    "row LOST. Reasons: NOT NULL violation, schema drift, DB pool "
    "exhaustion, connection refused. Alert > 5 in 5min.",
    labelnames=("tool_name", "reason"),
)

# Drop site #3 — executor scheduling failure (loop closed, executor
# full). Operationally serious — every subsequent tool call may be
# un-audited until the executor recovers.
tool_audit_scheduling_failed_total = Counter(
    "tool_audit_scheduling_failed_total",
    "tool_audit handler could not schedule the async _log_call write "
    "into the executor. Subsequent tool calls may be silently "
    "un-audited until the executor recovers. Alert > 0 in 5min.",
    labelnames=("tool_name",),
)


# ── Helpers ───────────────────────────────────────────────────────────

# P0c review I3 — cardinality guard. `tool_name` comes from MCP
# request bodies, which can include made-up names from a hostile or
# buggy caller. Without a guard, an attacker spamming unique
# tool_name strings could balloon the label set into thousands of
# series per metric. The helper folds anything not in the registered
# MCP tool set to "_unknown" so cardinality stays bounded by
# registered_tools + {None → "unknown", unrecognized → "_unknown"}.
# Lazy-populated from FastMCP the first time it's referenced.
_KNOWN_TOOL_NAMES: Optional[frozenset[str]] = None


def _label_tool_name(tool_name: Optional[str]) -> str:
    """Cardinality-bounded label value for tool_name (P0c review I3)."""
    if tool_name is None:
        return "unknown"
    global _KNOWN_TOOL_NAMES
    if _KNOWN_TOOL_NAMES is None:
        # Lazy populate from FastMCP registry. Defer the import so
        # this module loads even without the mcp dep present (tests,
        # non-server contexts). On any failure we fall through with
        # an empty set, which then accepts all names through with the
        # 128-char length cap — a small bootstrap window where we
        # accept unknowns rather than crashing the metric path.
        try:
            from src.mcp_app import mcp as _mcp  # type: ignore

            registry = getattr(_mcp, "_tool_manager", None)
            if registry is not None and hasattr(registry, "_tools"):
                _KNOWN_TOOL_NAMES = frozenset(registry._tools.keys())
            else:
                _KNOWN_TOOL_NAMES = frozenset()
        except Exception:  # noqa: BLE001
            _KNOWN_TOOL_NAMES = frozenset()
    if _KNOWN_TOOL_NAMES and tool_name not in _KNOWN_TOOL_NAMES:
        return "_unknown"
    return tool_name[:128]  # length cap as a last line


def record_drop(*, reason: str, tool_name: Optional[str]) -> None:
    """Increment the drop counter. Best-effort: never raises."""
    try:
        tool_audit_drop_total.labels(
            reason=reason,
            tool_name=_label_tool_name(tool_name),
        ).inc()
    except Exception:  # noqa: BLE001 — metrics layer is last line
        pass


def record_write_failure(
    *,
    tool_name: Optional[str],
    exception: BaseException,
) -> None:
    """Increment the SQL-write-failure counter."""
    try:
        tool_audit_write_failed_total.labels(
            tool_name=_label_tool_name(tool_name),
            reason=type(exception).__name__,
        ).inc()
    except Exception:  # noqa: BLE001
        pass


def record_scheduling_failure(*, tool_name: Optional[str]) -> None:
    """Increment the executor-scheduling-failure counter."""
    try:
        tool_audit_scheduling_failed_total.labels(
            tool_name=_label_tool_name(tool_name),
        ).inc()
    except Exception:  # noqa: BLE001
        pass
