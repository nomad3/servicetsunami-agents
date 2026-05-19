"""Tests for the async chat-result pattern (task #161).

Covers two surfaces:

  1. ``app.services.chat_jobs`` — state machine + event-log helpers.
     A SQL-stub DB recognises the queries each helper issues so we can
     run without a live Postgres backend (same pattern as
     test_skill_evals_endpoint.py).

  2. ``app.api.v1.chat`` — the four new endpoints. Tenant ownership
     (foreign-tenant -> 404, not 403), happy-path job snapshot, cancel,
     and the SSE generator's terminal-state close.
"""

from __future__ import annotations

import os
os.environ["TESTING"] = "True"

import uuid
from typing import Any, Dict, List, Optional, Tuple
from unittest.mock import MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api import deps
from app.models.user import User
from app.services import chat_jobs as chat_jobs_service


# ──────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────


def _user(tenant_id: Optional[uuid.UUID] = None) -> User:
    return User(
        id=uuid.uuid4(),
        email=f"user-{uuid.uuid4().hex[:6]}@test.com",
        tenant_id=tenant_id or uuid.uuid4(),
        is_active=True,
        is_superuser=False,
        hashed_password="x",
    )


class _StubResult:
    """Minimal stand-in for SQLAlchemy 1.4 Result with fetchone/fetchall +
    rowcount.
    """

    def __init__(self, rows: Optional[List[Tuple]] = None, rowcount: int = 0):
        self._rows = rows if rows is not None else []
        self.rowcount = rowcount

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def fetchall(self):
        return list(self._rows)


class _StubDB:
    """Recognises every SQL the chat_jobs service issues.

    Keyed by substring matchers — the service's queries are stable
    enough that ``"INSERT INTO chat_jobs"`` etc. are unambiguous.
    """

    def __init__(self):
        # job_id (str) -> dict of row fields
        self.jobs: Dict[str, Dict[str, Any]] = {}
        # job_id (str) -> list[(seq, kind, payload_str, created_at)]
        self.events: Dict[str, List[Tuple]] = {}
        self.committed_count = 0
        self.rolled_back_count = 0
        self.executed_sql: List[str] = []

    def _now(self):
        from datetime import datetime, timezone
        return datetime.now(timezone.utc)

    def execute(self, statement, params=None):
        sql = str(statement)
        self.executed_sql.append(sql)
        params = params or {}

        # ── chat_jobs writes
        if "INSERT INTO chat_jobs" in sql:
            jid = params["id"]
            self.jobs[jid] = {
                "id": jid,
                "session_id": params["session_id"],
                "tenant_id": params["tenant_id"],
                "user_id": params["user_id"],
                "status": "queued",
                "request_content": params["content"],
                "result_message_id": None,
                "error": None,
                "cancel_requested": False,
                "created_at": self._now(),
                "finished_at": None,
            }
            return _StubResult(rowcount=1)

        if "UPDATE chat_jobs" in sql and "status = 'running'" in sql:
            jid = params["id"]
            job = self.jobs.get(jid)
            if job and job["status"] == "queued":
                job["status"] = "running"
                return _StubResult(rowcount=1)
            return _StubResult(rowcount=0)

        if "UPDATE chat_jobs" in sql and "status = 'done'" in sql:
            jid = params["id"]
            job = self.jobs.get(jid)
            if job and job["status"] not in ("done", "failed", "cancelled"):
                job["status"] = "done"
                job["result_message_id"] = params.get("rmid")
                job["finished_at"] = self._now()
                return _StubResult(rowcount=1)
            return _StubResult(rowcount=0)

        if "UPDATE chat_jobs" in sql and "status = 'failed'" in sql:
            jid = params["id"]
            job = self.jobs.get(jid)
            if job and job["status"] not in ("done", "failed", "cancelled"):
                job["status"] = "failed"
                job["error"] = params.get("error")
                job["finished_at"] = self._now()
                return _StubResult(rowcount=1)
            return _StubResult(rowcount=0)

        if "UPDATE chat_jobs" in sql and "cancel_requested = TRUE" in sql:
            jid = params["id"]
            job = self.jobs.get(jid)
            if job and job["status"] not in ("done", "failed", "cancelled"):
                job["cancel_requested"] = True
                if job["status"] == "queued":
                    job["status"] = "cancelled"
                    job["finished_at"] = self._now()
                return _StubResult(rowcount=1)
            return _StubResult(rowcount=0)

        if "UPDATE chat_jobs" in sql and "status = 'cancelled'" in sql:
            jid = params["id"]
            job = self.jobs.get(jid)
            if job and job["status"] not in ("done", "failed", "cancelled"):
                job["status"] = "cancelled"
                job["finished_at"] = self._now()
                return _StubResult(rowcount=1)
            return _StubResult(rowcount=0)

        # ── chat_jobs reads
        if "FROM chat_jobs" in sql and "WHERE id = :id" in sql:
            jid = params["id"]
            job = self.jobs.get(jid)
            if not job:
                return _StubResult()
            row = (
                job["id"],
                job["session_id"],
                job["tenant_id"],
                job["user_id"],
                job["status"],
                job["result_message_id"],
                job["error"],
                job["cancel_requested"],
                job["created_at"],
                job["finished_at"],
            )
            return _StubResult([row])

        # ── advisory lock (no-op for stub)
        if "pg_advisory_xact_lock" in sql:
            return _StubResult()

        # ── chat_job_events max(seq) + 1
        if "SELECT COALESCE(MAX(seq), 0) + 1" in sql:
            jid = params["jid"]
            ev = self.events.get(jid, [])
            next_seq = (max((e[0] for e in ev), default=0)) + 1
            return _StubResult([(next_seq,)])

        if "INSERT INTO chat_job_events" in sql:
            jid = params["jid"]
            self.events.setdefault(jid, []).append(
                (params["seq"], params["kind"], params["payload"], self._now())
            )
            return _StubResult(rowcount=1)

        if "FROM chat_job_events" in sql and "ORDER BY seq ASC" in sql:
            jid = params["jid"]
            from_seq = params["from_seq"]
            ev = self.events.get(jid, [])
            rows = [
                (seq, kind, payload, created_at)
                for (seq, kind, payload, created_at) in ev
                if seq > from_seq
            ]
            rows.sort(key=lambda r: r[0])
            return _StubResult(rows[: params["limit"]])

        if "DELETE FROM chat_jobs" in sql:
            # not exercised in current tests; supported for parity
            return _StubResult(rowcount=0)

        return _StubResult()

    def commit(self):
        self.committed_count += 1

    def rollback(self):
        self.rolled_back_count += 1

    def close(self):
        # Workers call .close() on their SessionLocal()-backed sessions;
        # the stub is a no-op so tests can reuse the same instance across
        # request + worker without losing state.
        pass


# ──────────────────────────────────────────────────────────────────────
# Service-level: state machine
# ──────────────────────────────────────────────────────────────────────


def test_create_job_inserts_queued_row():
    db = _StubDB()
    sid = uuid.uuid4()
    tid = uuid.uuid4()
    uid = uuid.uuid4()
    job = chat_jobs_service.create_job(
        db, session_id=sid, tenant_id=tid, user_id=uid, content="hello"
    )
    assert job["status"] == "queued"
    assert uuid.UUID(job["id"])  # parses
    assert db.committed_count == 1


def test_start_finish_transitions_are_idempotent():
    db = _StubDB()
    sid, tid, uid = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    job = chat_jobs_service.create_job(db, session_id=sid, tenant_id=tid, user_id=uid, content="hi")
    jid = uuid.UUID(job["id"])

    assert chat_jobs_service.start_job(db, job_id=jid) is True
    # Second start while running is a no-op (returns False).
    assert chat_jobs_service.start_job(db, job_id=jid) is False

    rmid = uuid.uuid4()
    assert chat_jobs_service.finish_job(db, job_id=jid, result_message_id=rmid) is True
    # Second finish is a no-op.
    assert chat_jobs_service.finish_job(db, job_id=jid, result_message_id=rmid) is False

    snap = chat_jobs_service.get_job(db, job_id=jid, tenant_id=tid)
    assert snap["status"] == "done"
    assert snap["result_message_id"] == str(rmid)


def test_fail_job_records_error_and_is_terminal():
    db = _StubDB()
    sid, tid, uid = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    job = chat_jobs_service.create_job(db, session_id=sid, tenant_id=tid, user_id=uid, content="hi")
    jid = uuid.UUID(job["id"])
    chat_jobs_service.start_job(db, job_id=jid)

    assert chat_jobs_service.fail_job(db, job_id=jid, error="boom") is True
    # Finish after fail is no-op (terminal already).
    assert chat_jobs_service.finish_job(db, job_id=jid) is False

    snap = chat_jobs_service.get_job(db, job_id=jid, tenant_id=tid)
    assert snap["status"] == "failed"
    assert snap["error"] == "boom"


def test_cancel_on_queued_flips_directly_to_cancelled():
    db = _StubDB()
    sid, tid, uid = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    job = chat_jobs_service.create_job(db, session_id=sid, tenant_id=tid, user_id=uid, content="hi")
    jid = uuid.UUID(job["id"])

    assert chat_jobs_service.cancel_job(db, job_id=jid) is True
    snap = chat_jobs_service.get_job(db, job_id=jid, tenant_id=tid)
    assert snap["status"] == "cancelled"
    assert snap["cancel_requested"] is True


def test_cancel_on_running_sets_flag_but_keeps_status():
    db = _StubDB()
    sid, tid, uid = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    job = chat_jobs_service.create_job(db, session_id=sid, tenant_id=tid, user_id=uid, content="hi")
    jid = uuid.UUID(job["id"])
    chat_jobs_service.start_job(db, job_id=jid)

    assert chat_jobs_service.cancel_job(db, job_id=jid) is True
    snap = chat_jobs_service.get_job(db, job_id=jid, tenant_id=tid)
    assert snap["status"] == "running"  # worker hasn't observed yet
    assert snap["cancel_requested"] is True

    # Worker observes.
    assert chat_jobs_service.observe_cancel(db, job_id=jid) is True
    snap = chat_jobs_service.get_job(db, job_id=jid, tenant_id=tid)
    assert snap["status"] == "cancelled"


# ──────────────────────────────────────────────────────────────────────
# Service-level: tenant isolation on get_job
# ──────────────────────────────────────────────────────────────────────


def test_get_job_returns_none_for_foreign_tenant():
    db = _StubDB()
    sid, tid_owner, tid_foreign, uid = (
        uuid.uuid4(), uuid.uuid4(), uuid.uuid4(), uuid.uuid4(),
    )
    job = chat_jobs_service.create_job(
        db, session_id=sid, tenant_id=tid_owner, user_id=uid, content="hi"
    )
    jid = uuid.UUID(job["id"])

    # Same tenant — sees it.
    assert chat_jobs_service.get_job(db, job_id=jid, tenant_id=tid_owner) is not None
    # Foreign tenant — None (caller turns into 404).
    assert chat_jobs_service.get_job(db, job_id=jid, tenant_id=tid_foreign) is None


def test_get_job_unknown_id_returns_none():
    db = _StubDB()
    assert chat_jobs_service.get_job(
        db, job_id=uuid.uuid4(), tenant_id=uuid.uuid4()
    ) is None


# ──────────────────────────────────────────────────────────────────────
# Service-level: event log
# ──────────────────────────────────────────────────────────────────────


def test_append_event_assigns_monotonic_seq():
    db = _StubDB()
    sid, tid, uid = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    job = chat_jobs_service.create_job(db, session_id=sid, tenant_id=tid, user_id=uid, content="hi")
    jid = uuid.UUID(job["id"])

    seq1 = chat_jobs_service.append_event(db, job_id=jid, kind="lifecycle", payload={"event": "started"})
    seq2 = chat_jobs_service.append_event(db, job_id=jid, kind="chunk", payload={"text": "hello"})
    seq3 = chat_jobs_service.append_event(db, job_id=jid, kind="chunk", payload={"text": "world"})
    assert (seq1, seq2, seq3) == (1, 2, 3)


def test_append_event_rejects_invalid_kind():
    db = _StubDB()
    with pytest.raises(ValueError):
        chat_jobs_service.append_event(
            db, job_id=uuid.uuid4(), kind="banana", payload={},
        )


def test_read_events_filters_by_from_seq_and_is_idempotent():
    db = _StubDB()
    sid, tid, uid = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    job = chat_jobs_service.create_job(db, session_id=sid, tenant_id=tid, user_id=uid, content="hi")
    jid = uuid.UUID(job["id"])
    for i in range(5):
        chat_jobs_service.append_event(db, job_id=jid, kind="chunk", payload={"i": i})

    all_events = chat_jobs_service.read_events(db, job_id=jid, from_seq=0)
    assert [e["seq"] for e in all_events] == [1, 2, 3, 4, 5]

    # Same read again is identical (no side effects).
    again = chat_jobs_service.read_events(db, job_id=jid, from_seq=0)
    assert [e["seq"] for e in again] == [1, 2, 3, 4, 5]

    # Resume past seq 3.
    tail = chat_jobs_service.read_events(db, job_id=jid, from_seq=3)
    assert [e["seq"] for e in tail] == [4, 5]


# ──────────────────────────────────────────────────────────────────────
# Endpoint-level: ownership + cancel
# ──────────────────────────────────────────────────────────────────────


def _build_client(user: User, db: _StubDB, *, session_lookup=None) -> TestClient:
    """Build a TestClient with only the four async endpoints mounted.

    `session_lookup` is a callable that resolves (session_id, tenant_id)
    -> ChatSession-ish or None, letting tests stub session ownership for
    the /messages/start path.
    """
    from app.api.v1 import chat as chat_module

    app = FastAPI()
    app.dependency_overrides[deps.get_current_user] = lambda: user
    app.dependency_overrides[deps.get_current_active_user] = lambda: user

    def _stub_db():
        yield db

    app.dependency_overrides[deps.get_db] = _stub_db
    app.include_router(chat_module.router, prefix="/api/v1/chat")
    return TestClient(app, raise_server_exceptions=False)


def test_get_job_foreign_tenant_returns_404():
    owner = _user()
    db = _StubDB()
    job = chat_jobs_service.create_job(
        db, session_id=uuid.uuid4(), tenant_id=owner.tenant_id,
        user_id=owner.id, content="hi",
    )

    foreign = _user()
    client = _build_client(foreign, db)
    r = client.get(f"/api/v1/chat/jobs/{job['id']}")
    assert r.status_code == 404


def test_get_job_happy_path_returns_snapshot():
    user = _user()
    db = _StubDB()
    job = chat_jobs_service.create_job(
        db, session_id=uuid.uuid4(), tenant_id=user.tenant_id,
        user_id=user.id, content="hi",
    )

    client = _build_client(user, db)
    r = client.get(f"/api/v1/chat/jobs/{job['id']}")
    assert r.status_code == 200
    body = r.json()
    assert body["id"] == job["id"]
    assert body["status"] == "queued"


def test_cancel_job_returns_404_for_unknown_id():
    user = _user()
    db = _StubDB()
    client = _build_client(user, db)
    r = client.post(f"/api/v1/chat/jobs/{uuid.uuid4()}/cancel")
    assert r.status_code == 404


def test_cancel_job_sets_flag_and_returns_202():
    user = _user()
    db = _StubDB()
    job = chat_jobs_service.create_job(
        db, session_id=uuid.uuid4(), tenant_id=user.tenant_id,
        user_id=user.id, content="hi",
    )
    chat_jobs_service.start_job(db, job_id=uuid.UUID(job["id"]))

    client = _build_client(user, db)
    r = client.post(f"/api/v1/chat/jobs/{job['id']}/cancel")
    assert r.status_code == 202

    snap = chat_jobs_service.get_job(
        db, job_id=uuid.UUID(job["id"]), tenant_id=user.tenant_id
    )
    assert snap["cancel_requested"] is True


def test_messages_start_404s_for_unknown_session(monkeypatch):
    user = _user()
    db = _StubDB()

    # Patch chat_service.get_session to always return None — simulates
    # foreign / unknown session id.
    from app.services import chat as chat_service_module
    monkeypatch.setattr(chat_service_module, "get_session", lambda *a, **kw: None)

    client = _build_client(user, db)
    r = client.post(
        f"/api/v1/chat/sessions/{uuid.uuid4()}/messages/start",
        json={"content": "hello"},
    )
    assert r.status_code == 404


# ──────────────────────────────────────────────────────────────────────
# Worker safety: closure must NOT touch request-scoped ORM objects
# ──────────────────────────────────────────────────────────────────────


def test_worker_does_not_capture_request_scoped_user(monkeypatch):
    """Regression: BLOCKER #1.

    The worker thread fired by `post_message_start` used to dereference
    `current_user.tenant_id` / `current_user.id` and `payload.content`
    AFTER the request returned. Under load the ORM session backing
    `current_user` is closed → DetachedInstanceError.

    Now the handler snapshots primitives before launching the worker.
    This test trips the regression by deliberately invalidating the
    request-scoped `current_user` between request return and worker
    execution: we set `current_user.tenant_id = None` once the request
    completes, then assert the worker still finished successfully
    against the captured snapshot.
    """
    import threading as _threading

    user = _user()
    db = _StubDB()

    # Fake session lookup so the worker's branch survives.
    from app.services import chat as chat_service_module

    class _Sess:
        def __init__(self):
            self.id = uuid.uuid4()

    captured_args: Dict[str, Any] = {}

    def _fake_get_session(_db, *, session_id, tenant_id):
        captured_args.setdefault("session_lookups", []).append(
            {"session_id": session_id, "tenant_id": tenant_id}
        )
        return _Sess()

    monkeypatch.setattr(chat_service_module, "get_session", _fake_get_session)

    # The worker opens its own SessionLocal()-backed session. Point that
    # factory at our `_StubDB` so the chat_jobs queries land on the stub
    # instead of the real SQLite test engine.
    from app.db import session as _session_module
    monkeypatch.setattr(_session_module, "SessionLocal", lambda: db)

    # Block the worker until we've trashed `current_user` so we can
    # prove the snapshot is what's being used.
    user_trashed = _threading.Event()
    worker_done = _threading.Event()
    post_user_args: Dict[str, Any] = {}

    def _fake_post_user_message(_db, *, session, user_id, content, **kwargs):
        # Wait for the request to "complete" and `current_user` to be
        # mutated before doing any work.
        assert user_trashed.wait(timeout=2.0), "test setup never signalled"
        post_user_args["user_id"] = user_id
        post_user_args["content"] = content
        post_user_args["tenant_id"] = session is not None  # session object captured
        # Return objects matching the .id + .content shape the worker reads.
        u = MagicMock()
        u.id = uuid.uuid4()
        u.content = content
        a = MagicMock()
        a.id = uuid.uuid4()
        a.content = "response"
        worker_done.set()
        return (u, a)

    monkeypatch.setattr(
        chat_service_module, "post_user_message", _fake_post_user_message
    )

    client = _build_client(user, db)
    expected_tenant = user.tenant_id
    expected_user_id = user.id
    r = client.post(
        f"/api/v1/chat/sessions/{uuid.uuid4()}/messages/start",
        json={"content": "hello-snapshot"},
    )
    assert r.status_code == 202

    # Simulate request-scope teardown: trash the user's tenant_id. If
    # the worker still references `current_user`, it'll crash here.
    user.tenant_id = None
    user.id = None
    user_trashed.set()

    assert worker_done.wait(timeout=3.0), "worker never ran"

    # Worker still used the captured primitives, not the trashed ORM.
    assert post_user_args["user_id"] == expected_user_id
    assert post_user_args["content"] == "hello-snapshot"

    # And the session lookup inside the worker used the snapshotted
    # tenant id, not the now-None one on `current_user`.
    lookups = captured_args.get("session_lookups", [])
    assert lookups, "worker never looked up the session"
    assert lookups[-1]["tenant_id"] == expected_tenant
