"""Tests for /api/v2/sessions/{session_id}/events (PR-3 of Alpha Control Plane Tier 0-1).

Covers the JSON replay path (pagination, ordering, 24h window cap,
subprocess-stream coalescing) and a minimal SSE smoke test. SSE
heavy-lifting (live pub/sub) is covered by integration tests in the
deployed environment; here we verify the JSON path is correct.

Design: docs/plans/2026-05-15-alpha-control-plane-design.md §5, §5.4
"""
from __future__ import annotations

import os
import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text

pytest.importorskip("fastapi")

# Test fixtures hit real Postgres tables (tenants + chat_sessions +
# session_events) via raw SQL. SQLite unit run lacks those, so this
# file belongs in the api(integration, postgres+pgvector) job. Same
# fix as test_internal_session_stream.py — unmasked 2026-05-20 after
# the create_chat_session ImportError stopped hiding everything below.
pytestmark = pytest.mark.integration

from app.api import deps
from app.api.v2 import router as v2_router


@pytest.fixture
def engine():
    return create_engine(os.environ["DATABASE_URL"])


@pytest.fixture
def session_id_and_tenant(engine):
    """Real chat_sessions + tenant row for the test (FK targets)."""
    tid = uuid.uuid4()
    sid = uuid.uuid4()
    with engine.begin() as c:
        c.execute(text("INSERT INTO tenants (id, name) VALUES (:id, 'v2-test')"), {"id": tid})
        c.execute(
            text("INSERT INTO chat_sessions (id, tenant_id, source) VALUES (:id, :tid, 'test')"),
            {"id": sid, "tid": tid},
        )
    yield (sid, tid)
    with engine.begin() as c:
        c.execute(text("DELETE FROM chat_sessions WHERE id = :id"), {"id": sid})
        c.execute(text("DELETE FROM tenants WHERE id = :id"), {"id": tid})


def _make_client(tenant_id: uuid.UUID):
    """Wire a minimal FastAPI app around the v2 router with a stubbed
    auth dependency (the caller's tenant matches the session's)."""
    app = FastAPI()
    app.include_router(v2_router, prefix="/api/v2")

    user = MagicMock()
    user.id = uuid.uuid4()
    user.tenant_id = tenant_id
    user.is_active = True

    def _fake_db():
        from app.db.session import SessionLocal
        db = SessionLocal()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[deps.get_db] = _fake_db
    app.dependency_overrides[deps.get_current_active_user] = lambda: user
    return TestClient(app)


def _publish(sid, tid, kind, payload, seq_no):
    """Insert a session_events row directly (faster than going through
    publish_session_event for setup)."""
    engine = create_engine(os.environ["DATABASE_URL"])
    with engine.begin() as c:
        c.execute(
            text(
                "INSERT INTO session_events "
                "(session_id, tenant_id, seq_no, event_type, payload) "
                "VALUES (:sid, :tid, :seq, :type, CAST(:payload AS jsonb))"
            ),
            {"sid": sid, "tid": tid, "seq": seq_no, "type": kind, "payload": __import__("json").dumps(payload)},
        )


def test_replay_returns_events_ordered_by_seq_no(session_id_and_tenant):
    sid, tid = session_id_and_tenant
    for i in range(1, 6):
        _publish(sid, tid, "chat_message", {"i": i}, i)

    client = _make_client(tid)
    resp = client.get(f"/api/v2/sessions/{sid}/events?since=0", headers={"Accept": "application/json"})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert len(body["events"]) == 5
    seq_nos = [e["seq_no"] for e in body["events"]]
    assert seq_nos == [1, 2, 3, 4, 5]
    assert body["latest_seq_no"] == 5
    assert body["next_cursor"] is None  # didn't hit the limit


def test_replay_pagination_via_next_cursor(session_id_and_tenant):
    sid, tid = session_id_and_tenant
    for i in range(1, 11):
        _publish(sid, tid, "chat_message", {"i": i}, i)

    client = _make_client(tid)
    # First page (limit 3)
    resp = client.get(f"/api/v2/sessions/{sid}/events?since=0&limit=3", headers={"Accept": "application/json"})
    body = resp.json()
    assert [e["seq_no"] for e in body["events"]] == [1, 2, 3]
    assert body["next_cursor"] == 3

    # Next page (since=3, limit 3)
    resp = client.get(f"/api/v2/sessions/{sid}/events?since=3&limit=3", headers={"Accept": "application/json"})
    body = resp.json()
    assert [e["seq_no"] for e in body["events"]] == [4, 5, 6]
    assert body["next_cursor"] == 6

    # Last partial page (since=6, limit 3, only 4 rows left)
    resp = client.get(f"/api/v2/sessions/{sid}/events?since=6&limit=3", headers={"Accept": "application/json"})
    body = resp.json()
    assert [e["seq_no"] for e in body["events"]] == [7, 8, 9]
    assert body["next_cursor"] == 9  # we hit the limit


def test_since_older_than_24h_returns_409(session_id_and_tenant, engine):
    """If the caller's since=X refers to a row older than 24h, the
    server returns 409 telling them to start over."""
    sid, tid = session_id_and_tenant
    # Insert event AT 25h ago + a few recent ones
    old_ts = datetime.now(timezone.utc) - timedelta(hours=25)
    with engine.begin() as c:
        c.execute(
            text(
                "INSERT INTO session_events "
                "(session_id, tenant_id, seq_no, event_type, payload, created_at) "
                "VALUES (:sid, :tid, 1, 'chat_message', '{}'::jsonb, :ts)"
            ),
            {"sid": sid, "tid": tid, "ts": old_ts},
        )
    for i in range(2, 5):
        _publish(sid, tid, "chat_message", {}, i)

    client = _make_client(tid)
    resp = client.get(f"/api/v2/sessions/{sid}/events?since=1", headers={"Accept": "application/json"})
    assert resp.status_code == 409, resp.text
    detail = resp.json()["detail"]
    assert detail["error"] == "replay_window_expired"
    assert detail["latest_seq_no"] == 4
    assert detail["replay_window_hours"] == 24


def test_subprocess_stream_coalescing(session_id_and_tenant, engine):
    """50 cli_subprocess_stream chunks from same platform in a tight
    burst should collapse to 1 coalesced event on replay."""
    sid, tid = session_id_and_tenant
    base_ts = datetime.now(timezone.utc) - timedelta(seconds=10)
    with engine.begin() as c:
        for i in range(1, 51):
            c.execute(
                text(
                    "INSERT INTO session_events "
                    "(session_id, tenant_id, seq_no, event_type, payload, created_at) "
                    "VALUES (:sid, :tid, :seq, 'cli_subprocess_stream', CAST(:payload AS jsonb), :ts)"
                ),
                {
                    "sid": sid, "tid": tid, "seq": i,
                    "payload": __import__("json").dumps(
                        {"platform": "claude_code", "fd": "stdout", "chunk": f"line {i}", "chunk_kind": "text"}
                    ),
                    # Make all 50 fit inside one 5s window
                    "ts": base_ts + timedelta(milliseconds=i * 50),
                },
            )

    client = _make_client(tid)
    resp = client.get(f"/api/v2/sessions/{sid}/events?since=0", headers={"Accept": "application/json"})
    body = resp.json()
    # 50 source events should coalesce to 1
    assert len(body["events"]) == 1
    evt = body["events"][0]
    assert evt["type"] == "cli_subprocess_stream"
    assert evt["payload"]["coalesced_count"] == 50
    # Only last 3 chunks kept + truncation flag
    assert len(evt["payload"]["chunks"]) == 3
    assert evt["payload"]["chunks_truncated"] is True
    # New shape: each chunk is a dict carrying its kind + fd
    for ch in evt["payload"]["chunks"]:
        assert isinstance(ch, dict)
        assert ch.get("chunk_kind") == "text"
        assert ch.get("fd") == "stdout"
        assert ch.get("chunk", "").startswith("line ")
    # seq_no should be the LAST in the window (50)
    assert evt["seq_no"] == 50


def test_subprocess_stream_coalescing_preserves_chunk_kind(session_id_and_tenant, engine):
    """Mixed chunk_kinds (reasoning/text/tool_use) must round-trip
    through the coalescer so the frontend can colour each line."""
    sid, tid = session_id_and_tenant
    base_ts = datetime.now(timezone.utc) - timedelta(seconds=2)
    kinds = ["reasoning", "text", "tool_use"]
    with engine.begin() as c:
        for i, kind in enumerate(kinds, start=1):
            c.execute(
                text(
                    "INSERT INTO session_events "
                    "(session_id, tenant_id, seq_no, event_type, payload, created_at) "
                    "VALUES (:sid, :tid, :seq, 'cli_subprocess_stream', CAST(:payload AS jsonb), :ts)"
                ),
                {
                    "sid": sid, "tid": tid, "seq": i,
                    "payload": __import__("json").dumps(
                        {"platform": "claude_code", "fd": "stdout", "chunk": f"{kind}-line", "chunk_kind": kind}
                    ),
                    "ts": base_ts + timedelta(milliseconds=i * 50),
                },
            )

    client = _make_client(tid)
    resp = client.get(f"/api/v2/sessions/{sid}/events?since=0", headers={"Accept": "application/json"})
    body = resp.json()
    assert len(body["events"]) == 1
    chunks = body["events"][0]["payload"]["chunks"]
    # 3 chunks, exact-window, no truncation
    assert len(chunks) == 3
    assert [c["chunk_kind"] for c in chunks] == kinds
    assert [c["chunk"] for c in chunks] == [f"{k}-line" for k in kinds]


def test_subprocess_stream_coalescing_preserves_tool_evidence(session_id_and_tenant, engine):
    """Coalescer truncation MUST keep tool_use / tool_result / lifecycle
    chunks even when the window exceeds 3 entries. Dropping those would
    erase the audit trail of what the agent did (review B3 — never
    truncate tool evidence)."""
    sid, tid = session_id_and_tenant
    base_ts = datetime.now(timezone.utc) - timedelta(seconds=5)
    # 10 entries: text-text-text-tool_use-text-text-text-tool_result-text-text
    pattern = [
        ("text", "intro line"),
        ("text", "more reasoning"),
        ("text", "even more"),
        ("tool_use", "Read /etc/passwd"),
        ("text", "got back data"),
        ("reasoning", "thinking…"),
        ("text", "let me try"),
        ("tool_result", "exit 0"),
        ("text", "done"),
        ("text", "summarising"),
    ]
    with engine.begin() as c:
        for i, (kind, chunk_text) in enumerate(pattern, start=1):
            c.execute(
                text(
                    "INSERT INTO session_events "
                    "(session_id, tenant_id, seq_no, event_type, payload, created_at) "
                    "VALUES (:sid, :tid, :seq, 'cli_subprocess_stream', CAST(:payload AS jsonb), :ts)"
                ),
                {
                    "sid": sid, "tid": tid, "seq": i,
                    "payload": __import__("json").dumps({
                        "platform": "claude_code", "fd": "stdout",
                        "chunk": chunk_text, "chunk_kind": kind,
                    }),
                    "ts": base_ts + timedelta(milliseconds=i * 50),
                },
            )

    client = _make_client(tid)
    resp = client.get(f"/api/v2/sessions/{sid}/events?since=0", headers={"Accept": "application/json"})
    body = resp.json()
    assert len(body["events"]) == 1
    evt = body["events"][0]
    assert evt["payload"]["coalesced_count"] == 10
    chunks = evt["payload"]["chunks"]
    kept_kinds = [c["chunk_kind"] for c in chunks]
    # Both tool entries MUST survive truncation.
    assert "tool_use" in kept_kinds, f"tool_use dropped: {kept_kinds}"
    assert "tool_result" in kept_kinds, f"tool_result dropped: {kept_kinds}"
    # Payload was truncated flag still set.
    assert evt["payload"]["chunks_truncated"] is True


def test_subprocess_streams_in_different_windows_dont_coalesce(session_id_and_tenant, engine):
    """Two bursts >5s apart stay as separate events."""
    sid, tid = session_id_and_tenant
    burst_a_start = datetime.now(timezone.utc) - timedelta(seconds=20)
    burst_b_start = datetime.now(timezone.utc) - timedelta(seconds=10)  # 10s gap

    with engine.begin() as c:
        # Burst A: 3 chunks at t-20s, t-19.9s, t-19.8s
        for i, offset in enumerate([0, 0.1, 0.2], start=1):
            c.execute(
                text(
                    "INSERT INTO session_events "
                    "(session_id, tenant_id, seq_no, event_type, payload, created_at) "
                    "VALUES (:sid, :tid, :seq, 'cli_subprocess_stream', CAST(:payload AS jsonb), :ts)"
                ),
                {
                    "sid": sid, "tid": tid, "seq": i,
                    "payload": __import__("json").dumps({"platform": "claude_code", "chunk": f"a{i}"}),
                    "ts": burst_a_start + timedelta(seconds=offset),
                },
            )
        # Burst B: 3 chunks at t-10s, t-9.9s, t-9.8s
        for i, offset in enumerate([0, 0.1, 0.2], start=4):
            c.execute(
                text(
                    "INSERT INTO session_events "
                    "(session_id, tenant_id, seq_no, event_type, payload, created_at) "
                    "VALUES (:sid, :tid, :seq, 'cli_subprocess_stream', CAST(:payload AS jsonb), :ts)"
                ),
                {
                    "sid": sid, "tid": tid, "seq": i,
                    "payload": __import__("json").dumps({"platform": "claude_code", "chunk": f"b{i}"}),
                    "ts": burst_b_start + timedelta(seconds=offset),
                },
            )

    client = _make_client(tid)
    resp = client.get(f"/api/v2/sessions/{sid}/events?since=0", headers={"Accept": "application/json"})
    body = resp.json()
    # 6 source events in 2 bursts → 2 coalesced events
    assert len(body["events"]) == 2
    assert body["events"][0]["payload"]["coalesced_count"] == 3
    assert body["events"][1]["payload"]["coalesced_count"] == 3


def test_session_not_visible_to_other_tenant_returns_404(session_id_and_tenant):
    """Session in tenant A is invisible to tenant B."""
    sid, _tid = session_id_and_tenant
    other_tid = uuid.uuid4()  # different tenant

    client = _make_client(other_tid)
    resp = client.get(f"/api/v2/sessions/{sid}/events", headers={"Accept": "application/json"})
    assert resp.status_code == 404


def test_session_does_not_exist_returns_404(engine):
    """Random session_id returns 404."""
    tid = uuid.uuid4()
    sid = uuid.uuid4()
    client = _make_client(tid)
    resp = client.get(f"/api/v2/sessions/{sid}/events", headers={"Accept": "application/json"})
    assert resp.status_code == 404
