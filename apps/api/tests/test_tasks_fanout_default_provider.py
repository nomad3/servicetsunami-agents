"""Unit tests for the per-tenant default-provider lookup in
tasks_fanout._resolve_default_provider.

Closes audit row #2 from
docs/plans/2026-05-19-pr-merge-plan-and-tech-debt-audit.md — until this
PR, the alpha-run dispatch hard-coded `claude_code` when the caller
passed neither `providers` nor `fanout`. Now the resolution honors
`tenant_features.default_cli_platform`.
"""
from __future__ import annotations

import uuid

import pytest
from sqlalchemy.orm import Session

from app.api.v1.tasks_fanout import DEFAULT_RUN_PROVIDER, _resolve_default_provider
from app.db.base import Base
from app.db.session import SessionLocal, engine
from app.models.tenant import Tenant
from app.models.tenant_features import TenantFeatures


@pytest.fixture(name="db_session")
def db_session_fixture():
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    yield db
    db.close()
    Base.metadata.drop_all(bind=engine)


@pytest.fixture(name="test_tenant")
def test_tenant_fixture(db_session: Session):
    tenant = Tenant(name="Tasks Fanout Default Provider Tenant")
    db_session.add(tenant)
    db_session.commit()
    db_session.refresh(tenant)
    return tenant


def test_no_tenant_features_returns_ship_default(db_session, test_tenant):
    """Tenant has no TenantFeatures row at all -> ship default."""
    assert _resolve_default_provider(db_session, test_tenant.id) == DEFAULT_RUN_PROVIDER


def test_empty_default_cli_platform_returns_ship_default(db_session, test_tenant):
    """TenantFeatures row exists but default_cli_platform is empty string -> ship default.

    The column has a default ('claude_code') at the DB level, but a
    caller might have explicitly set it to empty in a UI form. Defensive
    handling: empty string falls back to the ship default rather than
    sending the empty string downstream.
    """
    tf = TenantFeatures(tenant_id=test_tenant.id, default_cli_platform="")
    db_session.add(tf)
    db_session.commit()
    assert _resolve_default_provider(db_session, test_tenant.id) == DEFAULT_RUN_PROVIDER


def test_explicit_default_cli_platform_returned(db_session, test_tenant):
    """TenantFeatures row sets a specific platform -> that platform wins."""
    tf = TenantFeatures(tenant_id=test_tenant.id, default_cli_platform="codex")
    db_session.add(tf)
    db_session.commit()
    assert _resolve_default_provider(db_session, test_tenant.id) == "codex"


def test_default_db_value_is_returned(db_session, test_tenant):
    """When TenantFeatures is inserted with no explicit
    default_cli_platform, the column's DB-level default ('claude_code')
    is read back — exercising the round-trip through SQLAlchemy."""
    tf = TenantFeatures(tenant_id=test_tenant.id)
    db_session.add(tf)
    db_session.commit()
    db_session.refresh(tf)
    # The DB default IS claude_code; both branches converge here.
    assert _resolve_default_provider(db_session, test_tenant.id) == "claude_code"
    assert _resolve_default_provider(db_session, test_tenant.id) == DEFAULT_RUN_PROVIDER


def test_each_tenant_resolved_independently(db_session, test_tenant):
    """Two tenants with different default_cli_platform settings each
    resolve to their own value (no cross-tenant leakage)."""
    other_tenant = Tenant(name="Other Tasks Fanout Tenant")
    db_session.add(other_tenant)
    db_session.commit()
    db_session.refresh(other_tenant)

    db_session.add(TenantFeatures(tenant_id=test_tenant.id, default_cli_platform="gemini_cli"))
    db_session.add(TenantFeatures(tenant_id=other_tenant.id, default_cli_platform="copilot_cli"))
    db_session.commit()

    assert _resolve_default_provider(db_session, test_tenant.id) == "gemini_cli"
    assert _resolve_default_provider(db_session, other_tenant.id) == "copilot_cli"


def test_unknown_tenant_returns_ship_default(db_session):
    """Tenant id that doesn't exist at all -> graceful fallback."""
    assert _resolve_default_provider(db_session, uuid.uuid4()) == DEFAULT_RUN_PROVIDER
