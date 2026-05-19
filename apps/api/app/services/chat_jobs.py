"""Chat-job state machine + event-log writer.

Owns the server-side half of the async chat-result pattern
(task #161, design doc 2026-05-17-async-chat-result-pattern-design.md).

Surface:

    create_job(db, ...)                   -> ChatJobRow      (status='queued')
    start_job(db, job_id)                 -> ChatJobRow      (queued -> running)
    finish_job(db, job_id, message_id)    -> ChatJobRow      (running -> done)
    fail_job(db, job_id, error)           -> ChatJobRow      (running -> failed)
    cancel_job(db, job_id)                -> ChatJobRow      (sets cancel_requested;
                                                              terminal flip happens when
                                                              the worker observes it)
    append_event(db, job_id, kind, payload) -> int(seq)
    read_events(db, job_id, from_seq=0)   -> list[event row dicts]
    get_job(db, job_id, tenant_id=...)    -> ChatJobRow | None

Tenant safety:

    Every read/write path takes `tenant_id` and 404s on mismatch via the
    `get_job` helper. Endpoints MUST pass the caller's tenant_id — we
    refuse to dereference a job_id without it (matches the
    skill_evals.py 404-not-403 pattern).

State machine:

         queued ──start──► running ──finish──► done
            │                 │
            │                 ├──fail────────► failed
            │                 │
            │                 └──cancel + observe ► cancelled
            │
            └──cancel (immediate, never picked up) ► cancelled

Idempotency:

    `start_job`, `finish_job`, `fail_job` are idempotent on the terminal
    states (the second call is a no-op returning the existing row).
    This keeps worker retries safe and lets the SSE endpoint's "tail
    until terminal" loop call the helpers without racing.

Event seq:

    `append_event` allocates seq under an advisory lock keyed by
    hashtext(job_id::text) — same pattern as session_events (mig 133).
    Multiple emitters across replicas can't race a duplicate seq even
    if the WAL is replaying.

This module is intentionally thin SQLAlchemy Core (`db.execute(text())`)
rather than ORM mappings. The two tables are append-only event-log
plumbing, not relational graph nodes — keeping them off the ORM avoids
a relationship() web around chat_messages/sessions and keeps the tests
free of an Alembic-style metadata fixture.
"""

from __future__ import annotations

import json
import logging
import uuid
from typing import Any, Dict, List, Optional

from sqlalchemy import text
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)


# ────────────────────────────── allowed values ──────────────────────────────

JOB_STATUSES = ("queued", "running", "done", "failed", "cancelled")
TERMINAL_STATUSES = ("done", "failed", "cancelled")
EVENT_KINDS = ("chunk", "tool_use", "tool_result", "lifecycle")


# ─────────────────────────────── lifecycle ──────────────────────────────────


def create_job(
    db: Session,
    *,
    session_id: uuid.UUID,
    tenant_id: uuid.UUID,
    user_id: uuid.UUID,
    content: str,
) -> Dict[str, Any]:
    """Insert a new chat_jobs row in status='queued' and return its fields."""
    job_id = uuid.uuid4()
    db.execute(
        text(
            """
            INSERT INTO chat_jobs (id, session_id, tenant_id, user_id, status, request_content)
            VALUES (:id, :session_id, :tenant_id, :user_id, 'queued', :content)
            """
        ),
        {
            "id": str(job_id),
            "session_id": str(session_id),
            "tenant_id": str(tenant_id),
            "user_id": str(user_id),
            "content": content,
        },
    )
    db.commit()
    return {
        "id": str(job_id),
        "session_id": str(session_id),
        "tenant_id": str(tenant_id),
        "user_id": str(user_id),
        "status": "queued",
    }


def get_job(
    db: Session,
    *,
    job_id: uuid.UUID,
    tenant_id: uuid.UUID,
) -> Optional[Dict[str, Any]]:
    """Return the job row scoped to ``tenant_id``, else None.

    Caller turns None into a 404. Returning None on tenant mismatch (not
    403 / not raising) avoids leaking whether a foreign-tenant job_id
    exists — same convention as skill_evals._verify_tenant_owns_skill.
    """
    row = db.execute(
        text(
            """
            SELECT id, session_id, tenant_id, user_id, status,
                   result_message_id, error, cancel_requested,
                   created_at, finished_at
              FROM chat_jobs
             WHERE id = :id
            """
        ),
        {"id": str(job_id)},
    ).fetchone()
    if row is None:
        return None
    if str(row[2]) != str(tenant_id):
        return None
    return {
        "id": str(row[0]),
        "session_id": str(row[1]),
        "tenant_id": str(row[2]),
        "user_id": str(row[3]),
        "status": row[4],
        "result_message_id": str(row[5]) if row[5] else None,
        "error": row[6],
        "cancel_requested": bool(row[7]),
        "created_at": row[8].isoformat() if row[8] else None,
        "finished_at": row[9].isoformat() if row[9] else None,
    }


def start_job(db: Session, *, job_id: uuid.UUID) -> bool:
    """queued -> running. Idempotent (no-op on already running/terminal)."""
    res = db.execute(
        text(
            """
            UPDATE chat_jobs
               SET status = 'running'
             WHERE id = :id
               AND status = 'queued'
            """
        ),
        {"id": str(job_id)},
    )
    db.commit()
    return res.rowcount > 0


def finish_job(
    db: Session,
    *,
    job_id: uuid.UUID,
    result_message_id: Optional[uuid.UUID] = None,
) -> bool:
    """running -> done. Idempotent on terminal states."""
    res = db.execute(
        text(
            """
            UPDATE chat_jobs
               SET status = 'done',
                   result_message_id = :rmid,
                   finished_at = NOW()
             WHERE id = :id
               AND status NOT IN ('done', 'failed', 'cancelled')
            """
        ),
        {
            "id": str(job_id),
            "rmid": str(result_message_id) if result_message_id else None,
        },
    )
    db.commit()
    return res.rowcount > 0


def fail_job(db: Session, *, job_id: uuid.UUID, error: str) -> bool:
    """running -> failed. Idempotent on terminal states."""
    res = db.execute(
        text(
            """
            UPDATE chat_jobs
               SET status = 'failed',
                   error = :error,
                   finished_at = NOW()
             WHERE id = :id
               AND status NOT IN ('done', 'failed', 'cancelled')
            """
        ),
        {"id": str(job_id), "error": error[:8192] if error else None},
    )
    db.commit()
    return res.rowcount > 0


def cancel_job(db: Session, *, job_id: uuid.UUID) -> bool:
    """Set cancel_requested=TRUE; if still queued, flip directly to
    cancelled.  A running worker observes the flag on its next poll
    and flips itself.
    """
    res = db.execute(
        text(
            """
            UPDATE chat_jobs
               SET cancel_requested = TRUE,
                   status = CASE WHEN status = 'queued' THEN 'cancelled' ELSE status END,
                   finished_at = CASE WHEN status = 'queued' THEN NOW() ELSE finished_at END
             WHERE id = :id
               AND status NOT IN ('done', 'failed', 'cancelled')
            """
        ),
        {"id": str(job_id)},
    )
    db.commit()
    return res.rowcount > 0


def is_cancel_requested(db: Session, *, job_id: uuid.UUID) -> bool:
    """Cheap point-in-time read of ``cancel_requested``.

    The worker polls this between phases (BLOCKER #2 from PR review):
    cancel_job() only flips the flag, so without an explicit check the
    worker keeps running past the user's cancel request and races
    finish_job(done) vs observe_cancel(cancelled). Returning a plain
    bool keeps the call cheap enough to invoke before every event
    emission; the row read is indexed on the PK.
    """
    row = db.execute(
        text("SELECT cancel_requested FROM chat_jobs WHERE id = :id"),
        {"id": str(job_id)},
    ).fetchone()
    return bool(row[0]) if row else False


def observe_cancel(db: Session, *, job_id: uuid.UUID) -> bool:
    """Worker calls this once it has acknowledged a cancel_requested
    flag: flips running -> cancelled. Separate helper so the worker
    decides *when* it has unwound safely, not the cancel endpoint.
    """
    res = db.execute(
        text(
            """
            UPDATE chat_jobs
               SET status = 'cancelled',
                   finished_at = NOW()
             WHERE id = :id
               AND status NOT IN ('done', 'failed', 'cancelled')
            """
        ),
        {"id": str(job_id)},
    )
    db.commit()
    return res.rowcount > 0


# ─────────────────────────────── event log ──────────────────────────────────


def append_event(
    db: Session,
    *,
    job_id: uuid.UUID,
    kind: str,
    payload: Dict[str, Any],
) -> int:
    """Append a row to chat_job_events; returns the allocated seq.

    .. warning::
        Caller MUST pass a **dedicated** ``Session`` (e.g.
        ``SessionLocal()``), NOT a request-scoped ``db`` from
        ``Depends(get_db)``. This helper issues ``db.commit()`` mid-
        function, which would commit any pending work the request
        handler had open on its own transaction. Workers already do
        this correctly via ``wdb = SessionLocal()``; the SSE / GET
        request paths must NOT call ``append_event`` directly.

    Seq allocation uses the same advisory-lock pattern as session_events
    (mig 133): `pg_advisory_xact_lock(hashtext(job_id))` serialises
    per-job inserts within a single transaction so the
    `COALESCE(MAX(seq), 0) + 1` read is consistent. The (job_id, seq)
    PK is the safety net if a future caller forgets the lock.

    Invalid ``kind`` raises ValueError *here* (cheap pre-flight). Note
    that even if the Python guard were skipped, the DB's
    ``chat_job_events_kind_check`` CHECK constraint would fail the
    INSERT with a 500 — the constraint failure surfaces as a
    transaction-aborting error, not a silent skip (IMPORTANT #7).
    """
    if kind not in EVENT_KINDS:
        raise ValueError(f"invalid event kind: {kind!r}; allowed={EVENT_KINDS}")

    # advisory lock — keyed by hashtext on the UUID string so different
    # jobs never collide. The lock is xact-scoped; commits / rollbacks
    # release it.
    db.execute(text("SELECT pg_advisory_xact_lock(hashtext(:k))"), {"k": str(job_id)})

    # COALESCE(MAX(seq), 0) + 1 always returns exactly one row (even
    # when chat_job_events has zero rows for this job_id), so the
    # ``if next_seq_row else 1`` fallback the first draft carried is
    # dead. Trust the aggregate (NIT #9).
    next_seq_row = db.execute(
        text(
            """
            SELECT COALESCE(MAX(seq), 0) + 1
              FROM chat_job_events
             WHERE job_id = :jid
            """
        ),
        {"jid": str(job_id)},
    ).fetchone()
    seq = int(next_seq_row[0])

    db.execute(
        text(
            """
            INSERT INTO chat_job_events (job_id, seq, kind, payload)
            VALUES (:jid, :seq, :kind, CAST(:payload AS JSONB))
            """
        ),
        {
            "jid": str(job_id),
            "seq": seq,
            "kind": kind,
            "payload": json.dumps(payload or {}),
        },
    )
    db.commit()
    return seq


def read_events(
    db: Session,
    *,
    job_id: uuid.UUID,
    from_seq: int = 0,
    limit: int = 2000,
) -> List[Dict[str, Any]]:
    """Return events with seq > from_seq, ordered ASC.

    `limit` defaults to 2000 — matches the per-job retention ceiling in
    the design doc. The SSE replay path passes from_seq from the
    client's last-rendered seq so reconnects don't re-emit history.
    """
    rows = db.execute(
        text(
            """
            SELECT seq, kind, payload, created_at
              FROM chat_job_events
             WHERE job_id = :jid
               AND seq > :from_seq
             ORDER BY seq ASC
             LIMIT :limit
            """
        ),
        {"jid": str(job_id), "from_seq": int(from_seq), "limit": int(limit)},
    ).fetchall()
    out: List[Dict[str, Any]] = []
    for r in rows:
        payload = r[2]
        # psycopg2 returns JSONB as dict already; defensive parse if a
        # test stub hands back a string.
        if isinstance(payload, str):
            try:
                payload = json.loads(payload)
            except Exception:
                payload = {"raw": payload}
        out.append(
            {
                "seq": int(r[0]),
                "kind": r[1],
                "payload": payload,
                "created_at": r[3].isoformat() if r[3] else None,
            }
        )
    return out


# ────────────────────────── janitor (housekeeping) ──────────────────────────


def purge_finished_jobs(db: Session, *, older_than_hours: int = 24) -> int:
    """Delete jobs (and their events via CASCADE) finished more than
    ``older_than_hours`` ago. Intended for a cron / scheduled task,
    not for request-time use.
    """
    res = db.execute(
        text(
            """
            DELETE FROM chat_jobs
             WHERE finished_at IS NOT NULL
               AND finished_at < NOW() - (:hours * INTERVAL '1 hour')
            """
        ),
        {"hours": int(older_than_hours)},
    )
    db.commit()
    return res.rowcount or 0
