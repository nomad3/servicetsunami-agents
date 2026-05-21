"""Tests for the break-glass endpoint + service (PR 6 of #647).

Locks the design §6 / §10 PR 6 contract:

  - POST /api/v1/luna/values/break-glass writes a new value-set
    version with reduced protect/avoid + expires_at metadata.
  - duration_seconds is clamped to
    [BREAK_GLASS_MIN_SECONDS, BREAK_GLASS_MAX_SECONDS]; default 1h.
  - keep_protect_slugs / keep_avoid_slugs are KEEP-lists. None /
    empty = drop everything (full break-glass).
  - operator_id is taken from the authenticated user (NEVER body)
    so the audit log can't be forged.
  - read_value_set walks PAST expired break-glass versions to the
    next non-expired version (auto-expire WITHOUT a background job).
  - A non-expired break-glass version dropping a protect slug means
    consult() no longer blocks on it for the duration.
  - The audit-log line ('BREAK_GLASS_OPENED') is emitted exactly once
    per use.
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

import pytest


def test_break_glass_router_imports_clean():
    """Catches typo / unmapped-import failure mode (per
    feedback_test_router_startup memory)."""
    from app.api.v1 import routes  # noqa: F401
    from app.api.v1 import values

    paths = {r.path for r in values.router.routes}
    assert "/luna/values/break-glass" in paths
    assert "/luna/values/agents/{agent_id}/break-glass" in paths


def _build_app_with_stubs(stub_user, stub_db):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from app.api import deps as api_deps
    from app.api.v1.values import router

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[api_deps.get_current_user] = lambda: stub_user
    app.dependency_overrides[api_deps.get_db] = lambda: stub_db
    return TestClient(app)


class _StubUser:
    def __init__(self):
        self.id = uuid.uuid4()
        self.tenant_id = uuid.uuid4()


class _StubAgent:
    def __init__(self, tenant_id):
        self.id = uuid.uuid4()
        self.tenant_id = tenant_id
        self.name = "Luna"


# ── Pure layer (AgentValueSet.is_break_glass + from_dict/to_dict) ─────


def test_value_set_round_trip_preserves_break_glass_fields():
    from app.services.agent_value_set import AgentValueSet

    vs = AgentValueSet(
        protect=[], pursue=[], avoid=[],
        version=2, updated_at="2026-05-21T00:00:00+00:00",
        expires_at="2026-05-21T01:00:00+00:00",
        break_glass_reason="prod incident #1234",
        break_glass_operator_id="op-abc",
    )
    rt = AgentValueSet.from_dict(vs.to_dict())
    assert rt.expires_at == "2026-05-21T01:00:00+00:00"
    assert rt.break_glass_reason == "prod incident #1234"
    assert rt.break_glass_operator_id == "op-abc"
    assert rt.is_break_glass() is True


def test_value_set_ordinary_version_no_break_glass_keys_in_dict():
    """Ordinary (non-break-glass) versions must NOT carry empty
    expires_at/reason/operator keys in their serialized JSON —
    keeps the audit-trail rows tidy + back-compat with pre-PR-6
    consumers that don't know the fields."""
    from app.services.agent_value_set import AgentValueSet

    vs = AgentValueSet(protect=[], pursue=[], avoid=[], version=1)
    d = vs.to_dict()
    assert "expires_at" not in d
    assert "break_glass_reason" not in d
    assert "break_glass_operator_id" not in d
    assert vs.is_break_glass() is False


# ── IO layer (_break_glass_expired + read walk-back) ──────────────────


def test_break_glass_expired_helper():
    from app.services.agent_value_set_io import _break_glass_expired

    past = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    future = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
    assert _break_glass_expired(past) is True
    assert _break_glass_expired(future) is False
    assert _break_glass_expired(None) is False
    assert _break_glass_expired("") is False
    # Malformed → treated as expired (defensive)
    assert _break_glass_expired("not-iso-at-all") is True


def test_read_value_set_walks_past_expired_break_glass(monkeypatch):
    """Locks the auto-expire mechanism without a background job:
    when the most-recent row is an expired break-glass version,
    read_value_set walks BACK to the prior non-expired version."""
    import json
    from app.services import agent_value_set_io
    from app.services.agent_value_set import AgentValueSet, ValueItem

    tenant_id = uuid.uuid4()
    agent_id = uuid.uuid4()

    # Version 1: ordinary, with a protect slug
    v1 = {
        "protect": [{
            "slug": "production-main",
            "description": "prod main",
            "added_at": "2026-05-20T00:00:00+00:00",
            "added_by": "operator",
            "evidence_memory_ids": [],
        }],
        "pursue": [], "avoid": [],
        "version": 1,
        "updated_at": "2026-05-20T00:00:00+00:00",
    }
    # Version 2: break-glass, EXPIRED 1h ago, drops all protects
    v2 = {
        "protect": [], "pursue": [], "avoid": [],
        "version": 2,
        "updated_at": "2026-05-21T00:00:00+00:00",
        "expires_at": (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat(),
        "break_glass_reason": "incident",
        "break_glass_operator_id": "op-abc",
    }

    # Stub the SQL: return both rows; order doesn't matter (read sorts)
    db = MagicMock()
    rows = [(json.dumps(v2),), (json.dumps(v1),)]
    chained = MagicMock()
    chained.filter.return_value.order_by.return_value.all.return_value = rows
    db.query.return_value = chained

    vs = agent_value_set_io.read_value_set(
        db, tenant_id=tenant_id, agent_id=agent_id,
    )
    # Walked back from expired v2 to v1
    assert vs.version == 1
    assert len(vs.protect) == 1
    assert vs.protect[0].slug == "production-main"
    assert vs.is_break_glass() is False


def test_read_value_set_returns_active_break_glass_until_expiry(monkeypatch):
    """Locks the active-override mechanism: a non-expired break-glass
    version wins latest-wins read AND surfaces with is_break_glass
    metadata."""
    import json
    from app.services import agent_value_set_io

    tenant_id = uuid.uuid4()
    agent_id = uuid.uuid4()

    v1 = {
        "protect": [{
            "slug": "production-main",
            "description": "prod main",
            "added_at": "2026-05-20T00:00:00+00:00",
            "added_by": "operator",
            "evidence_memory_ids": [],
        }],
        "pursue": [], "avoid": [],
        "version": 1,
        "updated_at": "2026-05-20T00:00:00+00:00",
    }
    v2 = {
        "protect": [], "pursue": [], "avoid": [],
        "version": 2,
        "updated_at": "2026-05-21T00:00:00+00:00",
        "expires_at": (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat(),
        "break_glass_reason": "incident",
        "break_glass_operator_id": "op-xyz",
    }

    db = MagicMock()
    rows = [(json.dumps(v1),), (json.dumps(v2),)]
    chained = MagicMock()
    chained.filter.return_value.order_by.return_value.all.return_value = rows
    db.query.return_value = chained

    vs = agent_value_set_io.read_value_set(
        db, tenant_id=tenant_id, agent_id=agent_id,
    )
    # The active break-glass wins; protect list is empty (full override)
    assert vs.version == 2
    assert len(vs.protect) == 0
    assert vs.is_break_glass() is True
    assert vs.break_glass_operator_id == "op-xyz"


# ── open_break_glass service ──────────────────────────────────────────


def test_open_break_glass_clamps_duration(monkeypatch):
    """Locks the duration clamp at [MIN, MAX]. A 1-week request must
    be clamped to 24h; a 1-second request to 60s."""
    from app.services import agent_value_set_io
    from app.services.agent_value_set import AgentValueSet

    tenant_id = uuid.uuid4()
    agent_id = uuid.uuid4()

    captured = {}

    def _fake_read(db, **kw):
        return AgentValueSet.empty()

    def _fake_write(db, *, expires_at, **kw):
        captured["expires_at"] = expires_at
        captured["kwargs"] = kw
        return AgentValueSet(
            protect=[], pursue=[], avoid=[], version=1,
            updated_at="x", expires_at=expires_at,
            break_glass_reason=kw.get("break_glass_reason"),
            break_glass_operator_id=kw.get("break_glass_operator_id"),
        )

    monkeypatch.setattr(agent_value_set_io, "read_value_set", _fake_read)
    monkeypatch.setattr(agent_value_set_io, "write_value_set", _fake_write)

    # 1 week → clamp to 24h
    now_before = datetime.now(timezone.utc)
    agent_value_set_io.open_break_glass(
        MagicMock(),
        tenant_id=tenant_id, agent_id=agent_id,
        operator_id="op1", reason="any",
        duration_seconds=7 * 24 * 3600,
    )
    expiry_max = datetime.fromisoformat(captured["expires_at"])
    delta_max = (expiry_max - now_before).total_seconds()
    assert (
        agent_value_set_io.BREAK_GLASS_MAX_SECONDS - 5
        <= delta_max
        <= agent_value_set_io.BREAK_GLASS_MAX_SECONDS + 5
    ), f"24h clamp failed; delta={delta_max}"

    # 1 second → clamp to MIN
    captured.clear()
    now_before = datetime.now(timezone.utc)
    agent_value_set_io.open_break_glass(
        MagicMock(),
        tenant_id=tenant_id, agent_id=agent_id,
        operator_id="op1", reason="any",
        duration_seconds=1,
    )
    expiry_min = datetime.fromisoformat(captured["expires_at"])
    delta_min = (expiry_min - now_before).total_seconds()
    assert (
        agent_value_set_io.BREAK_GLASS_MIN_SECONDS - 5
        <= delta_min
        <= agent_value_set_io.BREAK_GLASS_MIN_SECONDS + 5
    ), f"min clamp failed; delta={delta_min}"


def test_open_break_glass_keep_lists_filter_correctly(monkeypatch):
    """keep_protect_slugs preserves the named slugs and drops the rest;
    full empty list drops everything (full break-glass). Pursue list
    is ALWAYS preserved (pursue match is benign)."""
    from app.services import agent_value_set_io
    from app.services.agent_value_set import AgentValueSet, ValueItem

    current = AgentValueSet(
        protect=[
            ValueItem(slug="production-main", description="prod",
                      added_at="x", added_by="op",
                      evidence_memory_ids=[]),
            ValueItem(slug="staging-db", description="staging",
                      added_at="x", added_by="op",
                      evidence_memory_ids=[]),
        ],
        pursue=[
            ValueItem(slug="morning-report", description="morning",
                      added_at="x", added_by="op",
                      evidence_memory_ids=[]),
        ],
        avoid=[
            ValueItem(slug="legacy-code", description="legacy",
                      added_at="x", added_by="op",
                      evidence_memory_ids=[]),
        ],
        version=1, updated_at="x",
    )

    captured = {}

    def _fake_read(db, **kw):
        return current

    def _fake_write(db, *, protect, pursue, avoid, **kw):
        captured["protect"] = protect
        captured["pursue"] = pursue
        captured["avoid"] = avoid
        return AgentValueSet(
            protect=[], pursue=[ValueItem.from_dict(p) for p in pursue],
            avoid=[], version=2, updated_at="y",
            expires_at=kw["expires_at"],
        )

    monkeypatch.setattr(agent_value_set_io, "read_value_set", _fake_read)
    monkeypatch.setattr(agent_value_set_io, "write_value_set", _fake_write)

    # Keep staging-db only → drop production-main
    agent_value_set_io.open_break_glass(
        MagicMock(),
        tenant_id=uuid.uuid4(), agent_id=uuid.uuid4(),
        operator_id="op-abc", reason="staging-only override",
        keep_protect_slugs=["staging-db"],
        keep_avoid_slugs=[],   # full clear of avoid
    )
    protect_slugs = [p["slug"] for p in captured["protect"]]
    assert protect_slugs == ["staging-db"]
    assert captured["avoid"] == []
    # Pursue is always inherited
    assert [p["slug"] for p in captured["pursue"]] == ["morning-report"]


def test_open_break_glass_emits_exactly_one_audit_log_entry(
    monkeypatch, caplog,
):
    """The §6 invariant: ONE audit-log line per use. Verify by
    counting BREAK_GLASS_OPENED occurrences."""
    from app.services import agent_value_set_io
    from app.services.agent_value_set import AgentValueSet

    monkeypatch.setattr(
        agent_value_set_io, "read_value_set",
        lambda db, **kw: AgentValueSet.empty(),
    )
    monkeypatch.setattr(
        agent_value_set_io, "write_value_set",
        lambda db, **kw: AgentValueSet(
            protect=[], pursue=[], avoid=[], version=1,
            updated_at="x", expires_at=kw["expires_at"],
            break_glass_reason=kw["break_glass_reason"],
            break_glass_operator_id=kw["break_glass_operator_id"],
        ),
    )

    with caplog.at_level(logging.INFO):
        agent_value_set_io.open_break_glass(
            MagicMock(),
            tenant_id=uuid.uuid4(), agent_id=uuid.uuid4(),
            operator_id="op-abc",
            reason="prod incident #1234",
        )
    audit_lines = [
        r for r in caplog.records
        if "BREAK_GLASS_OPENED" in r.getMessage()
    ]
    assert len(audit_lines) == 1, (
        f"expected exactly 1 audit line, got {len(audit_lines)}: "
        f"{[r.getMessage() for r in audit_lines]}"
    )
    msg = audit_lines[0].getMessage()
    assert "operator=op-abc" in msg
    assert "prod incident" in msg


def test_open_break_glass_returns_none_when_read_fails(monkeypatch):
    """Locks the abort-if-current-state-unknown invariant: if we
    can't read the current value set, refuse to open break-glass."""
    from sqlalchemy.exc import SQLAlchemyError
    from app.services import agent_value_set_io

    def _crash_read(db, **kw):
        raise SQLAlchemyError("simulated DB transient")

    write_calls = {"n": 0}

    def _track_write(db, **kw):
        write_calls["n"] += 1
        return None

    monkeypatch.setattr(agent_value_set_io, "read_value_set", _crash_read)
    monkeypatch.setattr(agent_value_set_io, "write_value_set", _track_write)

    result = agent_value_set_io.open_break_glass(
        MagicMock(),
        tenant_id=uuid.uuid4(), agent_id=uuid.uuid4(),
        operator_id="op", reason="any",
    )
    assert result is None
    assert write_calls["n"] == 0, "write must NOT fire if read failed"


# ── Endpoint ─────────────────────────────────────────────────────────


def test_break_glass_endpoint_forces_operator_id_from_jwt(monkeypatch):
    """The audit-log operator_id must be the authenticated user's id —
    even if the body tries to smuggle one (the schema doesn't accept
    it; extra fields are dropped). Locks the audit-forge protection."""
    from app.services import agent_value_set_io
    from app.services.agent_value_set import AgentValueSet

    user = _StubUser()
    db = MagicMock()
    db.query.return_value.filter.return_value.order_by.return_value.first.return_value = _StubAgent(
        user.tenant_id
    )

    captured = {}

    def _fake_open(db, **kw):
        captured.update(kw)
        return AgentValueSet(
            protect=[], pursue=[], avoid=[], version=1, updated_at="x",
            expires_at=(datetime.now(timezone.utc) + timedelta(hours=1)).isoformat(),
            break_glass_reason=kw["reason"],
            break_glass_operator_id=kw["operator_id"],
        )

    monkeypatch.setattr(agent_value_set_io, "open_break_glass", _fake_open)

    client = _build_app_with_stubs(user, db)
    resp = client.post(
        "/luna/values/break-glass",
        json={
            "reason": "prod incident",
            "duration_seconds": 3600,
            # Attacker-controlled smuggle attempts:
            "operator_id": "attacker",
            "added_by": "attacker",
        },
    )
    assert resp.status_code == 201, resp.text
    # Server forced operator_id to the JWT user id
    assert captured["operator_id"] == str(user.id)


def test_break_glass_endpoint_rejects_blank_reason():
    """The reason field is required + bounded. An empty reason yields
    422 (pydantic) — locks the audit-quality bar."""
    user = _StubUser()
    db = MagicMock()
    client = _build_app_with_stubs(user, db)
    resp = client.post(
        "/luna/values/break-glass",
        json={"reason": "", "duration_seconds": 3600},
    )
    assert resp.status_code == 422


def test_break_glass_endpoint_rejects_out_of_range_duration():
    """duration_seconds outside [MIN, MAX] yields 422 at the schema
    layer. The service ALSO clamps as a defensive belt-and-suspender
    but the schema is the first line."""
    user = _StubUser()
    db = MagicMock()
    client = _build_app_with_stubs(user, db)
    resp = client.post(
        "/luna/values/break-glass",
        json={"reason": "any", "duration_seconds": 7 * 24 * 3600},
    )
    assert resp.status_code == 422


def test_per_agent_break_glass_404s_on_foreign_tenant():
    """Cross-tenant write protection. Foreign agent_id → 404 same as
    PUT /luna/values/agents/{id}."""
    user = _StubUser()
    db = MagicMock()
    db.query.return_value.filter.return_value.first.return_value = None
    client = _build_app_with_stubs(user, db)
    resp = client.post(
        f"/luna/values/agents/{uuid.uuid4()}/break-glass",
        json={"reason": "any", "duration_seconds": 3600},
    )
    assert resp.status_code == 404


def test_break_glass_endpoint_503_on_service_failure(monkeypatch):
    """When the service returns None (read/write failure), the
    endpoint surfaces 503."""
    from app.services import agent_value_set_io

    user = _StubUser()
    db = MagicMock()
    db.query.return_value.filter.return_value.order_by.return_value.first.return_value = _StubAgent(
        user.tenant_id
    )
    monkeypatch.setattr(
        agent_value_set_io, "open_break_glass",
        lambda db, **kw: None,
    )
    client = _build_app_with_stubs(user, db)
    resp = client.post(
        "/luna/values/break-glass",
        json={"reason": "incident", "duration_seconds": 3600},
    )
    assert resp.status_code == 503
