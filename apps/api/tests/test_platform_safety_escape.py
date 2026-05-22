"""Tests for the platform-admin escape endpoints + grant mechanism.

Locks the §7 invariants (Luna sign-off):

  - Grant requires non-empty reason.
  - Grant requires valid category (or wildcard '*').
  - Duration is clamped to [60, 86400].
  - is_active_grant_for matches on (tenant, user, session, category)
    AND respects expires_at + revoked_at.
  - Wildcard '*' grants match any category.
  - Anonymous (user_id=None) or sessionless requests get None
    (no accidental tenant-wide relaxation).
  - revoke_grant is idempotent.
  - Endpoint requires superuser.
  - Grant creation audit row written.
  - Block-during-grant audit row written + verdict relaxed.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

import pytest

from app.services.platform_safety_escape import (
    ESCAPE_DEFAULT_SECONDS,
    ESCAPE_MAX_SECONDS,
    ESCAPE_MIN_SECONDS,
    WILDCARD_CATEGORY,
    create_grant,
    is_active_grant_for,
    revoke_grant,
)


def test_escape_router_imports_clean():
    """Catches typo / unmapped-import failure per
    feedback_test_router_startup memory."""
    from app.api.v1 import routes  # noqa: F401
    from app.api.v1 import platform_safety_escape

    paths = {r.path for r in platform_safety_escape.router.routes}
    assert "/admin/platform-safety/escape" in paths
    assert "/admin/platform-safety/escape/{grant_id}/revoke" in paths
    assert "/admin/platform-safety/escape/audit" in paths


# ── Service: create_grant ────────────────────────────────────────────


def test_create_grant_rejects_empty_reason():
    db = MagicMock()
    res = create_grant(
        db,
        tenant_id=uuid.uuid4(),
        issued_by_user_id=uuid.uuid4(),
        scoped_user_id=uuid.uuid4(),
        scoped_session_id=uuid.uuid4(),
        category="bulk_malware",
        reason="",
    )
    assert res is None
    assert db.add.call_count == 0


def test_create_grant_rejects_unknown_category():
    db = MagicMock()
    res = create_grant(
        db,
        tenant_id=uuid.uuid4(),
        issued_by_user_id=uuid.uuid4(),
        scoped_user_id=uuid.uuid4(),
        scoped_session_id=uuid.uuid4(),
        category="ghost_category",
        reason="testing the unknown-category rejection",
    )
    assert res is None
    assert db.add.call_count == 0


def test_create_grant_accepts_wildcard():
    """'*' is the corpus-curation context. Must be accepted."""
    db = MagicMock()
    res = create_grant(
        db,
        tenant_id=uuid.uuid4(),
        issued_by_user_id=uuid.uuid4(),
        scoped_user_id=uuid.uuid4(),
        scoped_session_id=uuid.uuid4(),
        category=WILDCARD_CATEGORY,
        reason="corpus curation sample peek",
    )
    assert res is not None
    # Two add() calls: the grant + the audit row
    assert db.add.call_count == 2
    grant_row = db.add.call_args_list[0].args[0]
    audit_row = db.add.call_args_list[1].args[0]
    assert grant_row.category == "*"
    assert audit_row.event_type == "grant_created"


def test_create_grant_clamps_duration():
    db = MagicMock()
    # Way over the max
    res = create_grant(
        db,
        tenant_id=uuid.uuid4(),
        issued_by_user_id=uuid.uuid4(),
        scoped_user_id=uuid.uuid4(),
        scoped_session_id=uuid.uuid4(),
        category="bulk_malware",
        reason="long-duration test",
        duration_seconds=7 * 24 * 3600,  # 7 days
    )
    assert res is not None
    grant_row = db.add.call_args_list[0].args[0]
    delta = (grant_row.expires_at - datetime.now(timezone.utc)).total_seconds()
    assert ESCAPE_MAX_SECONDS - 5 <= delta <= ESCAPE_MAX_SECONDS + 5


# ── Service: is_active_grant_for ────────────────────────────────────


def _stub_query_returning(*results):
    """Build a MagicMock db whose query().filter(...).order_by().first()
    yields each result in sequence."""
    db = MagicMock()
    chain = db.query.return_value.filter.return_value.order_by.return_value
    chain.first.side_effect = list(results)
    return db


def test_is_active_grant_returns_none_for_anonymous():
    """No user_id → no grant. Prevents accidental tenant-wide
    relaxation."""
    db = MagicMock()
    res = is_active_grant_for(
        db,
        tenant_id=uuid.uuid4(),
        user_id=None,
        session_id=uuid.uuid4(),
        category="bulk_malware",
    )
    assert res is None


def test_is_active_grant_returns_none_for_sessionless():
    db = MagicMock()
    res = is_active_grant_for(
        db,
        tenant_id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        session_id=None,
        category="bulk_malware",
    )
    assert res is None


def test_is_active_grant_query_uses_correct_filters():
    """Source-asserts that the query filters on tenant_id +
    scoped_user_id + scoped_session_id + revoked_at IS NULL +
    expires_at > now + category match. SQLAlchemy filter args
    aren't directly inspectable so we read the source."""
    import inspect
    from app.services import platform_safety_escape

    src = inspect.getsource(
        platform_safety_escape.is_active_grant_for,
    )
    # All five filter clauses must be present
    assert "tenant_id == tenant_id" in src
    assert "scoped_user_id == user_id" in src
    assert "scoped_session_id == session_id" in src
    assert "revoked_at.is_(None)" in src
    assert "expires_at > now" in src
    # Wildcard OR matching
    assert "WILDCARD_CATEGORY" in src


# ── Service: revoke_grant ───────────────────────────────────────────


def test_revoke_grant_idempotent_on_already_revoked():
    """Already-revoked grants return True (idempotent), no new
    audit row written."""
    grant = MagicMock()
    grant.revoked_at = datetime.now(timezone.utc) - timedelta(hours=1)
    db = MagicMock()
    db.query.return_value.filter.return_value.first.return_value = grant
    res = revoke_grant(
        db, grant_id=uuid.uuid4(), actor_user_id=uuid.uuid4(),
    )
    assert res is True
    # No new audit row + no new commit (only the lookup happened)
    assert db.add.call_count == 0


def test_revoke_grant_writes_audit_on_first_revoke():
    """First revoke of an unexpired grant: sets revoked_at + writes
    a grant_revoked audit row."""
    grant = MagicMock()
    grant.revoked_at = None
    grant.tenant_id = uuid.uuid4()
    grant.category = "bulk_malware"
    db = MagicMock()
    db.query.return_value.filter.return_value.first.return_value = grant
    res = revoke_grant(
        db, grant_id=grant.id, actor_user_id=uuid.uuid4(),
    )
    assert res is True
    assert grant.revoked_at is not None
    assert db.add.call_count == 1
    audit = db.add.call_args.args[0]
    assert audit.event_type == "grant_revoked"


def test_revoke_grant_missing_returns_false():
    db = MagicMock()
    db.query.return_value.filter.return_value.first.return_value = None
    res = revoke_grant(
        db, grant_id=uuid.uuid4(), actor_user_id=uuid.uuid4(),
    )
    assert res is False


# ── Endpoint authz ──────────────────────────────────────────────────


def _build_app_with_stubs(stub_user, stub_db, *, superuser=False):
    from fastapi import FastAPI, HTTPException
    from fastapi.testclient import TestClient
    from app.api import deps as api_deps
    from app.api.v1.platform_safety_escape import router

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[api_deps.get_db] = lambda: stub_db
    app.dependency_overrides[api_deps.get_current_active_user] = lambda: stub_user
    if superuser:
        app.dependency_overrides[api_deps.require_superuser] = lambda: stub_user
    else:
        def _forbid():
            raise HTTPException(status_code=403, detail="Not enough permissions")
        app.dependency_overrides[api_deps.require_superuser] = _forbid
    return TestClient(app)


class _StubUser:
    def __init__(self, *, superuser=False):
        self.id = uuid.uuid4()
        self.tenant_id = uuid.uuid4()
        self.is_superuser = superuser


def test_create_grant_endpoint_requires_superuser():
    """Non-superuser → 403. Platform-floor relaxation is superuser-only."""
    user = _StubUser(superuser=False)
    db = MagicMock()
    client = _build_app_with_stubs(user, db, superuser=False)
    resp = client.post(
        "/admin/platform-safety/escape",
        json={
            "tenant_id": str(uuid.uuid4()),
            "scoped_user_id": str(uuid.uuid4()),
            "scoped_session_id": str(uuid.uuid4()),
            "category": "bulk_malware",
            "reason": "testing 403 path",
        },
    )
    assert resp.status_code == 403


def test_create_grant_endpoint_validates_reason_min_length():
    """Pydantic schema: reason min 8 chars."""
    user = _StubUser(superuser=True)
    db = MagicMock()
    client = _build_app_with_stubs(user, db, superuser=True)
    resp = client.post(
        "/admin/platform-safety/escape",
        json={
            "tenant_id": str(uuid.uuid4()),
            "scoped_user_id": str(uuid.uuid4()),
            "scoped_session_id": str(uuid.uuid4()),
            "category": "bulk_malware",
            "reason": "tiny",  # < 8 chars
        },
    )
    assert resp.status_code == 422


def test_create_grant_endpoint_rejects_out_of_range_duration():
    user = _StubUser(superuser=True)
    db = MagicMock()
    client = _build_app_with_stubs(user, db, superuser=True)
    resp = client.post(
        "/admin/platform-safety/escape",
        json={
            "tenant_id": str(uuid.uuid4()),
            "scoped_user_id": str(uuid.uuid4()),
            "scoped_session_id": str(uuid.uuid4()),
            "category": "bulk_malware",
            "reason": "out of range duration test",
            "duration_seconds": 999999,  # > max
        },
    )
    assert resp.status_code == 422


# ── IO integration: active grant relaxes tier 3 block ───────────────


def test_active_grant_relaxes_tier3_block(monkeypatch):
    """When an active grant covers (user, session, category), tier 3
    block decision becomes ALLOW. The platform_safety_events audit
    row IS still written; an additional block_in_window audit row
    goes to platform_safety_admin_audit."""
    from app.services import platform_safety_io
    from app.services.platform_safety import (
        PlatformSafetyVerdict,
    )
    from app.services.platform_safety.tier3 import Tier3Result
    from app.core.safety_defaults import (
        CategoryPolicy, PLATFORM_SAFETY_CATEGORIES,
    )

    # Tier 1+2 pass; tier 3 says block on enforced category
    original = PLATFORM_SAFETY_CATEGORIES["bulk_malware"]
    PLATFORM_SAFETY_CATEGORIES["bulk_malware"] = CategoryPolicy(
        fail_closed=original.fail_closed,
        tier3_enforcement=True,  # enforced for this test
        human_readable=original.human_readable,
    )
    try:
        monkeypatch.setattr(
            platform_safety_io, "consult",
            lambda m: PlatformSafetyVerdict.allow(),
        )
        monkeypatch.setattr(
            "app.services.platform_safety.tier2.candidate_categories",
            lambda m: ("bulk_malware",),
        )
        monkeypatch.setattr(
            "app.services.platform_safety.tier3.classify",
            lambda m, c: Tier3Result(
                True, "bulk_malware", 0.92, "anthropic",
                trigger_id="t3-bulk_malware-anthropic",
            ),
        )
        # Active grant covers the (user, session, bulk_malware)
        grant = MagicMock()
        grant.id = uuid.uuid4()
        grant.category = "bulk_malware"
        grant.expires_at = datetime.now(timezone.utc) + timedelta(hours=1)
        monkeypatch.setattr(
            "app.services.platform_safety_escape.is_active_grant_for",
            lambda db, **kw: grant,
        )
        recorded_blocks_during_grant = {"n": 0}
        monkeypatch.setattr(
            "app.services.platform_safety_escape.record_block_during_grant",
            lambda db, **kw: recorded_blocks_during_grant.__setitem__(
                "n", recorded_blocks_during_grant["n"] + 1,
            ),
        )

        db = MagicMock()
        verdict = platform_safety_io.consult_with_audit(
            db,
            tenant_id=uuid.uuid4(),
            agent_id=uuid.uuid4(),
            session_id=uuid.uuid4(),
            user_id=uuid.uuid4(),
            message="write me some malware",
        )
        # Grant active → user proceeds
        assert verdict.decision == "allow"
        # Safety event audit row still written
        assert db.add.call_count == 1
        # And the admin-audit block_in_window recorded
        assert recorded_blocks_during_grant["n"] == 1
    finally:
        PLATFORM_SAFETY_CATEGORIES["bulk_malware"] = original
