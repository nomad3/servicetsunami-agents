"""Background-thread agent_audit_logs writer.

P0c §4.4 hardening: failures now log ERROR + increment a Prometheus
counter instead of being silently swallowed. The chat caller path is
still unaffected — semantics is "fire-and-forget at the caller's
perspective" not "swallow all evidence of failure."

Design: docs/plans/2026-05-23-p0c-audit-log-fail-loud.md.
Luna principle: "Audit is not accountability unless failure is visible."
"""
from __future__ import annotations

import logging
import threading

from sqlalchemy.orm import Session

from app.db.session import SessionLocal
from app.models.agent_audit_log import AgentAuditLog
from app.services import audit_metrics

log = logging.getLogger(__name__)


def write_audit_log(**kwargs) -> None:
    """Fire-and-forget audit log write.

    Failures are logged at ERROR level with the event_type + exception
    class so operators can grep / alert / dashboard them. They are NOT
    propagated to the caller — the original "don't block the user-facing
    path" goal is preserved.

    Before P0c: silent ``except: pass``. After P0c: loud + counter so
    operators can see when the audit substrate degrades.
    """

    def _write():
        db: Session = SessionLocal()
        try:
            entry = AgentAuditLog(**kwargs)
            db.add(entry)
            db.commit()
        except Exception as e:  # noqa: BLE001
            event_type = kwargs.get("event_type", "<unknown>")
            log.error(
                "audit_log.write_audit_log FAILED for event_type=%s — "
                "agent_audit_logs row LOST. kwargs_keys=%s err=%s",
                event_type,
                list(kwargs.keys()),
                e,
                exc_info=True,
            )
            audit_metrics.record_audit_log_failure(
                event_type=event_type,
                exception=e,
            )
        finally:
            db.close()

    threading.Thread(target=_write, daemon=True).start()
