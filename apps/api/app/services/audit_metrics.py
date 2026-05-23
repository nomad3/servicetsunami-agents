"""Prometheus instrumentation for the audit-substrate fail-loud P0c.

Design: docs/plans/2026-05-23-p0c-audit-log-fail-loud.md §5.
Hard-test report: docs/report/2026-05-23-prompt-injection-tool-permission-test.md §3.4.
Luna sign-off: dialogue session 05979efd-a06a-4956-9df9-3fd84ec3c10d.

Covers the api-side audit failure surfaces:
  - `audit_log.write_audit_log` background-thread write (drop site #4)
  - `platform_safety_io._record_event` swallow (promoted from P1)
  - `platform_safety_io._check_repeat_attempts` swallow (promoted from P1)

The mcp-server-side surfaces (tool_audit.py drop sites #1-#3) have
their own counters in `apps/mcp-server/src/audit_metrics.py` — kept
separate because the two services have separate Prometheus registries
and separate `/metrics` exposure paths.

Design choices (mirror `emotion_engine_metrics`):

- Import-safe no-op fallback if `prometheus_client` isn't installed,
  so the audit hot path never crashes on missing-dep edge cases.
- Cardinality bounded by tenant + small enum sets (category, tier,
  exception class name). Never label with raw user-controlled strings.
- Counters are at module scope so `audit_metrics.audit_log_write_failed_total`
  is the canonical reference.

Luna's principle (revised closer): "Audit is not accountability unless
failure is visible." This module makes audit-substrate failures visible.
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

# Drop site #4 — audit_log.write_audit_log background-thread failure.
# Per-(event_type, exception_class). High counts mean the
# `agent_audit_logs` table is dropping rows — operators must
# investigate. Alert threshold: > 5 in 5min.
audit_log_write_failed_total = Counter(
    "audit_log_write_failed_total",
    "Background `write_audit_log` thread failed to commit an "
    "agent_audit_logs row. Each increment is a LOST audit row — "
    "the chat caller path is unaffected, but forensic record is "
    "missing. Alert > 5 in 5min.",
    labelnames=("event_type", "reason"),
)

# Promoted from P1 — platform_safety_io._record_event swallow.
# When a tier-1/2/3 verdict fires (block or allow with category
# context), we must record the audit row. If the write fails
# silently, the safety-floor block fired but the auditor cannot
# testify. Per-(category, tier). Alert: > 0 in 5min.
platform_safety_record_event_failed_total = Counter(
    "platform_safety_record_event_failed_total",
    "platform_safety_io._record_event swallowed an exception — a "
    "safety-floor verdict fired but the audit row write failed. "
    "Operators have no DB record of the event. Promoted from P1 to "
    "P0c after Luna review 2026-05-23: 'if safety IO fails silently, "
    "the safety floor has been bypassed.' Alert > 0 in 5min.",
    labelnames=("category", "tier"),
)

# Promoted from P1 — platform_safety_io._check_repeat_attempts swallow.
# Adversary-probe detector — counts repeat attempts after a refusal
# verdict. Silent failure here means we lose the only signal of
# someone probing the safety floor. Alert: > 0 in 5min.
platform_safety_repeat_check_failed_total = Counter(
    "platform_safety_repeat_check_failed_total",
    "platform_safety_io._check_repeat_attempts swallowed an "
    "exception — adversary-probe detector failed. The single "
    "user-facing refusal still fired, but pattern-detection is "
    "blind for this attempt. Promoted from P1 to P0c after Luna "
    "review 2026-05-23. Alert > 0 in 5min.",
    labelnames=("tenant_id",),
)


# ── Helpers ───────────────────────────────────────────────────────────

def record_audit_log_failure(
    *,
    event_type: Optional[str],
    exception: BaseException,
) -> None:
    """Increment the audit_log_write_failed counter. Best-effort: never raises."""
    try:
        audit_log_write_failed_total.labels(
            event_type=event_type or "unknown",
            reason=type(exception).__name__,
        ).inc()
    except Exception:  # noqa: BLE001 — metrics layer is the last line
        pass


def record_platform_safety_record_event_failure(
    *,
    category: Optional[str],
    tier: Optional[int],
) -> None:
    """Increment the platform_safety_record_event_failed counter."""
    try:
        platform_safety_record_event_failed_total.labels(
            category=category or "unknown",
            tier=str(tier) if tier is not None else "unknown",
        ).inc()
    except Exception:  # noqa: BLE001
        pass


def record_platform_safety_repeat_check_failure(
    *,
    tenant_id: Optional[str],
) -> None:
    """Increment the platform_safety_repeat_check_failed counter."""
    try:
        platform_safety_repeat_check_failed_total.labels(
            tenant_id=tenant_id or "unknown",
        ).inc()
    except Exception:  # noqa: BLE001
        pass
