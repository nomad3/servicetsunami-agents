"""Tests for `publish_session_event` dual-write (PR-2 of Alpha Control Plane Tier 0-1).

Verifies:
  1. Happy path: row persists with monotonic seq_no, envelope returned with seq_no + event_id.
  2. Concurrent publishers in the same session don't duplicate seq_no
     (regression test for the advisory-lock allocator).
  3. Cross-session writes don't contend.
  4. tenant_id resolution from chat_sessions when caller omits it.
  5. Redis fan-out failure: envelope is still returned, row is persisted.
  6. Legacy v1 wire format published alongside the new envelope.

Postgres-fail path is structurally hard to test against a live DB
(would require breaking the connection mid-transaction); we rely on
the rollback semantics already covered by SQLAlchemy + the catch-and-
reraise in the function body.

Design: docs/plans/2026-05-15-alpha-control-plane-design.md §5.1
"""
from __future__ import annotations

import os
import threading
import uuid
from unittest.mock import patch

import pytest
from sqlalchemy import create_engine, text

# Test fixtures hit real Postgres via os.environ["DATABASE_URL"] +
# raw SQL INSERTs into tenants / chat_sessions / session_events.
# Belongs in the api(integration, postgres+pgvector) job. Same fix
# pattern as test_internal_session_stream.py / test_session_events.py
# — unmasked 2026-05-20 after create_chat_session ImportError stopped
# hiding everything below it.
pytestmark = pytest.mark.integration


@pytest.fixture
def engine():
    return create_engine(os.environ["DATABASE_URL"])


@pytest.fixture
def session_id(engine):
    """Insert a real chat_session row for the test (FK target)."""
    sid = uuid.uuid4()
    with engine.begin() as c:
        c.execute(
            text("INSERT INTO chat_sessions (id, source) VALUES (:id, 'test')"),
            {"id": sid},
        )
    yield str(sid)
    with engine.begin() as c:
        c.execute(text("DELETE FROM chat_sessions WHERE id = :id"), {"id": sid})


def _row_count(engine, session_id: str) -> int:
    with engine.connect() as c:
        return c.execute(
            text("SELECT COUNT(*) FROM session_events WHERE session_id = :sid"),
            {"sid": session_id},
        ).scalar()


def _max_seq(engine, session_id: str) -> int:
    with engine.connect() as c:
        return c.execute(
            text("SELECT MAX(seq_no) FROM session_events WHERE session_id = :sid"),
            {"sid": session_id},
        ).scalar() or 0


def test_happy_path_persists_and_returns_envelope(session_id, engine):
    """Single publish writes a row, returns envelope with seq_no=1."""
    from app.services.collaboration_events import publish_session_event

    envelope = publish_session_event(
        session_id, "chat_message",
        {"role": "user", "text": "hello"},
        tenant_id=str(uuid.uuid4()),
    )
    assert envelope["event_id"]
    assert envelope["session_id"] == session_id
    assert envelope["seq_no"] == 1
    assert envelope["type"] == "chat_message"
    assert envelope["payload"] == {"role": "user", "text": "hello"}
    assert _row_count(engine, session_id) == 1


def test_monotonic_seq_no_within_session(session_id, engine):
    """Sequential publishes get seq_no 1, 2, 3, ..."""
    from app.services.collaboration_events import publish_session_event

    for i in range(1, 6):
        envelope = publish_session_event(
            session_id, "tool_call_started",
            {"tool_name": f"t{i}"},
            tenant_id=str(uuid.uuid4()),
        )
        assert envelope["seq_no"] == i
    assert _row_count(engine, session_id) == 5
    assert _max_seq(engine, session_id) == 5


def test_concurrent_publishers_in_same_session_no_duplicates(session_id, engine):
    """20 parallel publishers across the same session must produce
    seq_nos 1..20 with no duplicates. Regression test for the
    advisory-lock allocator.
    """
    from app.services.collaboration_events import publish_session_event

    results = []
    errors = []
    lock = threading.Lock()

    def publisher(i: int):
        try:
            env = publish_session_event(
                session_id, "chat_message",
                {"i": i},
                tenant_id=str(uuid.uuid4()),
            )
            with lock:
                results.append(env["seq_no"])
        except Exception as e:  # pragma: no cover — surfaces in assertion
            with lock:
                errors.append(e)

    threads = [threading.Thread(target=publisher, args=(i,)) for i in range(20)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=10)

    assert not errors, f"Publisher errors: {errors}"
    assert len(results) == 20
    assert sorted(results) == list(range(1, 21)), (
        f"Expected seq_nos 1..20 with no duplicates, got {sorted(results)}"
    )
    # DB also has exactly 20 rows
    assert _row_count(engine, session_id) == 20


def test_cross_session_writes_dont_contend(engine):
    """Two sessions in parallel each get their own seq_no sequence
    starting at 1 (no shared counter)."""
    from app.services.collaboration_events import publish_session_event

    sid_a = uuid.uuid4()
    sid_b = uuid.uuid4()
    with engine.begin() as c:
        c.execute(text("INSERT INTO chat_sessions (id, source) VALUES (:id, 'test')"), {"id": sid_a})
        c.execute(text("INSERT INTO chat_sessions (id, source) VALUES (:id, 'test')"), {"id": sid_b})
    try:
        results_a, results_b = [], []
        lock = threading.Lock()

        def publish_to(sid: uuid.UUID, bucket: list):
            for _ in range(5):
                env = publish_session_event(
                    str(sid), "chat_message", {},
                    tenant_id=str(uuid.uuid4()),
                )
                with lock:
                    bucket.append(env["seq_no"])

        ta = threading.Thread(target=publish_to, args=(sid_a, results_a))
        tb = threading.Thread(target=publish_to, args=(sid_b, results_b))
        ta.start(); tb.start(); ta.join(); tb.join()

        assert sorted(results_a) == [1, 2, 3, 4, 5]
        assert sorted(results_b) == [1, 2, 3, 4, 5]
    finally:
        with engine.begin() as c:
            c.execute(text("DELETE FROM chat_sessions WHERE id IN (:a, :b)"), {"a": sid_a, "b": sid_b})


def test_tenant_id_resolved_from_session_when_omitted(engine):
    """If caller doesn't pass tenant_id, the function looks it up from
    chat_sessions. Verified by creating a session with a known tenant
    and asserting the persisted row carries it.
    """
    from app.services.collaboration_events import publish_session_event

    # Create a real tenant so the FK is satisfied
    tid = uuid.uuid4()
    sid = uuid.uuid4()
    with engine.begin() as c:
        c.execute(
            text("INSERT INTO tenants (id, name) VALUES (:id, 'tier01-test-tenant')"),
            {"id": tid},
        )
        c.execute(
            text("INSERT INTO chat_sessions (id, tenant_id, source) VALUES (:id, :tid, 'test')"),
            {"id": sid, "tid": tid},
        )
    try:
        envelope = publish_session_event(str(sid), "chat_message", {})  # no tenant_id arg
        assert envelope["tenant_id"] == str(tid)
        # Verify the row carries the resolved tenant
        with engine.connect() as c:
            persisted_tid = c.execute(
                text("SELECT tenant_id FROM session_events WHERE id = :eid"),
                {"eid": envelope["event_id"]},
            ).scalar()
        assert str(persisted_tid) == str(tid)
    finally:
        with engine.begin() as c:
            c.execute(text("DELETE FROM chat_sessions WHERE id = :id"), {"id": sid})
            c.execute(text("DELETE FROM tenants WHERE id = :id"), {"id": tid})


def test_redis_publish_failure_does_not_raise(session_id, engine):
    """If the Redis publish fails after a successful Postgres write,
    the function logs a warning and returns the envelope. The row is
    persisted; live SSE listeners missed it but replay will recover it.
    """
    from app.services import collaboration_events

    # Patch _get_redis to return a Redis whose publish raises.
    class _FakeRedis:
        def publish(self, *args, **kwargs):
            raise RuntimeError("redis unreachable for test")

    with patch.object(collaboration_events, "_get_redis", return_value=_FakeRedis()):
        envelope = collaboration_events.publish_session_event(
            session_id, "chat_message", {"text": "with redis down"},
            tenant_id=str(uuid.uuid4()),
        )
        # Function returned normally; envelope has a seq_no
        assert envelope["seq_no"] >= 1
    # Row IS persisted despite Redis failure
    assert _row_count(engine, session_id) >= 1


def test_legacy_v1_wire_format_published_alongside_v2(session_id, engine):
    """Two Redis publishes per event: legacy v1 shape on `session:{id}`
    + new envelope on `session:{id}:v2`. Captures both via a fake."""
    from app.services import collaboration_events

    published = []

    class _CapturingRedis:
        def publish(self, channel, message):
            published.append((channel, message))

    with patch.object(collaboration_events, "_get_redis", return_value=_CapturingRedis()):
        collaboration_events.publish_session_event(
            session_id, "chat_message", {"text": "hi"},
            tenant_id=str(uuid.uuid4()),
        )

    assert len(published) == 2
    legacy_channel, _ = [p for p in published if not p[0].endswith(":v2")][0]
    v2_channel, _ = [p for p in published if p[0].endswith(":v2")][0]
    assert legacy_channel == f"session:{session_id}"
    assert v2_channel == f"session:{session_id}:v2"
