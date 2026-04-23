"""Defensive DB helpers.

When an exception is caught mid-transaction, SQLAlchemy marks the session as
"broken" and every subsequent query raises ``InvalidRequestError`` until
``rollback()`` is called. The rollback itself can fail (connection dropped,
transaction was never begun, session already closed) — and in those cases
there's nothing the caller can sanely do. We swallow but log at DEBUG so
the failure is at least traceable.

Replaces the inline pattern::

    try: db.rollback()
    except Exception: pass

with::

    safe_rollback(db)
"""

from __future__ import annotations

import logging

from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)


def safe_rollback(db: Session) -> None:
    """Best-effort rollback. Never raises; logs failure at DEBUG."""
    try:
        db.rollback()
    except Exception as exc:  # pragma: no cover — only fires when DB is already broken
        logger.debug("safe_rollback: rollback itself failed (%s)", exc)
