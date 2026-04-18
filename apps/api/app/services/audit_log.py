import threading

from sqlalchemy.orm import Session

from app.db.session import SessionLocal
from app.models.agent_audit_log import AgentAuditLog


def write_audit_log(**kwargs) -> None:
    """Fire-and-forget audit log write. Swallows all exceptions."""

    def _write():
        db: Session = SessionLocal()
        try:
            entry = AgentAuditLog(**kwargs)
            db.add(entry)
            db.commit()
        except Exception:
            pass  # never let audit failures affect the caller
        finally:
            db.close()

    threading.Thread(target=_write, daemon=True).start()
