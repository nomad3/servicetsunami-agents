"""Tests for POST /api/v2/internal/sessions/{id}/events.

Covers:
  - Valid X-Internal-Key writes a row + publishes (single chunk).
  - Batch fan-out splits into per-chunk seq_no allocations.
  - Bad / missing X-Internal-Key → 401.
  - Cross-tenant session_id → 404.
  - Non-whitelisted ``type`` → 400.

Plan: docs/plans/2026-05-16-terminal-full-cli-output.md §4.2
"""
from __future__ import annotations

import os
import uuid

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text

pytest.importorskip("fastapi")

from app.api import deps
from app.api.v2 import router as v2_router
from app.core.config import settings

# Test fixtures INSERT directly into tenants + chat_sessions via raw SQL
# (`engine.begin() … INSERT INTO tenants …`). SQLite-backed unit runs
# lack those tables, so every test in this module needs a real Postgres
# (the api(integration, postgres+pgvector) job). Marker added 2026-05-20
# when the create_chat_session ImportError stopped masking this — the
# tests have been silently miscollected since the file was added.
pytestmark = pytest.mark.integration


@pytest.fixture
def engine():
    return create_engine(os.environ["DATABASE_URL"])


@pytest.fixture
def session_and_tenant(engine):
    tid = uuid.uuid4()
    sid = uuid.uuid4()
    with engine.begin() as c:
        c.execute(text("INSERT INTO tenants (id, name) VALUES (:id, 'iss-test')"), {"id": tid})
        c.execute(
            text("INSERT INTO chat_sessions (id, tenant_id, source) VALUES (:id, :tid, 'test')"),
            {"id": sid, "tid": tid},
        )
    yield (sid, tid)
    with engine.begin() as c:
        c.execute(text("DELETE FROM session_events WHERE session_id = :id"), {"id": sid})
        c.execute(text("DELETE FROM chat_sessions WHERE id = :id"), {"id": sid})
        c.execute(text("DELETE FROM tenants WHERE id = :id"), {"id": tid})


def _make_client():
    app = FastAPI()
    app.include_router(v2_router, prefix="/api/v2")

    def _fake_db():
        from app.db.session import SessionLocal
        db = SessionLocal()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[deps.get_db] = _fake_db
    return TestClient(app)


def _valid_key() -> str:
    # Mirror the auth helper: API_INTERNAL_KEY or MCP_API_KEY.
    return settings.API_INTERNAL_KEY or settings.MCP_API_KEY or "dev_internal_key"


def test_valid_key_writes_and_publishes(session_and_tenant, engine):
    sid, tid = session_and_tenant
    client = _make_client()
    body = {
        "tenant_id": str(tid),
        "type": "cli_subprocess_stream",
        "payload": {
            "platform": "claude_code",
            "chunk_kind": "text",
            "chunk": "hello world\n",
            "fd": "stdout",
            "attempt": 1,
        },
    }
    resp = client.post(
        f"/api/v2/internal/sessions/{sid}/events",
        json=body,
        headers={"X-Internal-Key": _valid_key()},
    )
    assert resp.status_code == 200, resp.text
    out = resp.json()
    assert len(out["events"]) == 1
    assert out["events"][0]["seq_no"] >= 1

    # Verify row persisted with the right shape.
    with engine.begin() as c:
        rows = c.execute(
            text("SELECT event_type, payload FROM session_events WHERE session_id = :sid"),
            {"sid": sid},
        ).all()
    assert len(rows) == 1
    assert rows[0][0] == "cli_subprocess_stream"
    pl = rows[0][1]
    if isinstance(pl, str):
        import json as _json
        pl = _json.loads(pl)
    assert pl["chunk"] == "hello world\n"
    assert pl["chunk_kind"] == "text"


def test_batch_payload_splits_into_per_chunk_seq_no(session_and_tenant, engine):
    sid, tid = session_and_tenant
    client = _make_client()
    body = {
        "tenant_id": str(tid),
        "type": "cli_subprocess_stream",
        "payload": {
            "platform": "claude_code",
            "batch": [
                {"chunk_kind": "reasoning", "chunk": "thinking...", "fd": "stdout"},
                {"chunk_kind": "tool_use", "chunk": "→ Tool(Read)", "fd": "stdout"},
                {"chunk_kind": "text", "chunk": "ok done", "fd": "stdout"},
            ],
        },
    }
    resp = client.post(
        f"/api/v2/internal/sessions/{sid}/events",
        json=body,
        headers={"X-Internal-Key": _valid_key()},
    )
    assert resp.status_code == 200, resp.text
    out = resp.json()
    # 3 chunks → 3 envelopes, each with distinct seq_no
    assert len(out["events"]) == 3
    seq_nos = [e["seq_no"] for e in out["events"]]
    assert seq_nos == sorted(seq_nos)
    assert len(set(seq_nos)) == 3

    with engine.begin() as c:
        rows = c.execute(
            text(
                "SELECT seq_no, payload FROM session_events "
                "WHERE session_id = :sid ORDER BY seq_no"
            ),
            {"sid": sid},
        ).all()
    assert len(rows) == 3
    import json as _json
    payloads = [r[1] if isinstance(r[1], dict) else _json.loads(r[1]) for r in rows]
    assert [p["chunk_kind"] for p in payloads] == ["reasoning", "tool_use", "text"]
    # platform propagated from outer payload to each child
    assert all(p["platform"] == "claude_code" for p in payloads)


def test_bad_key_returns_401(session_and_tenant):
    sid, tid = session_and_tenant
    client = _make_client()
    resp = client.post(
        f"/api/v2/internal/sessions/{sid}/events",
        json={"tenant_id": str(tid), "type": "cli_subprocess_stream", "payload": {}},
        headers={"X-Internal-Key": "definitely-not-the-key"},
    )
    assert resp.status_code == 401


def test_missing_key_returns_401(session_and_tenant):
    sid, tid = session_and_tenant
    client = _make_client()
    resp = client.post(
        f"/api/v2/internal/sessions/{sid}/events",
        json={"tenant_id": str(tid), "type": "cli_subprocess_stream", "payload": {}},
    )
    assert resp.status_code == 401


def test_cross_tenant_returns_404(session_and_tenant):
    sid, _tid = session_and_tenant
    other_tid = uuid.uuid4()
    client = _make_client()
    resp = client.post(
        f"/api/v2/internal/sessions/{sid}/events",
        json={"tenant_id": str(other_tid), "type": "cli_subprocess_stream", "payload": {}},
        headers={"X-Internal-Key": _valid_key()},
    )
    assert resp.status_code == 404


def test_non_whitelisted_type_returns_400(session_and_tenant):
    sid, tid = session_and_tenant
    client = _make_client()
    resp = client.post(
        f"/api/v2/internal/sessions/{sid}/events",
        json={"tenant_id": str(tid), "type": "chat_message", "payload": {"text": "spoof"}},
        headers={"X-Internal-Key": _valid_key()},
    )
    assert resp.status_code == 400


def test_unknown_session_returns_404():
    """Random session_id (not in chat_sessions) → 404, before any publish."""
    bogus_sid = uuid.uuid4()
    tid = uuid.uuid4()
    client = _make_client()
    resp = client.post(
        f"/api/v2/internal/sessions/{bogus_sid}/events",
        json={"tenant_id": str(tid), "type": "cli_subprocess_stream", "payload": {}},
        headers={"X-Internal-Key": _valid_key()},
    )
    assert resp.status_code == 404
