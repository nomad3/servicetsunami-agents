"""Tests for the per-tenant NightlyReflectionWorkflow kill-switch.

Locked design decision #4: synthesis must NOT run unless an operator
has explicitly enabled it via tenant_features.nightly_reflection_enabled.
Default OFF. Missing row → OFF. Query failure → OFF.

These tests run on the real-Postgres integration job (same pattern
as M1's IO tests — see commit log on metacog_io.py UUID-cast fix).
The shipped SQLite shim fights with the JSONB / UUID compile paths,
so we don't try.
"""
from __future__ import annotations

import uuid

import pytest

from app.models.tenant import Tenant
from app.models.tenant_features import TenantFeatures
from app.services.reflection_killswitch import is_nightly_reflection_enabled

pytestmark = [pytest.mark.integration, pytest.mark.serial]


def _make_tenant(db, name: str = "Reflection Killswitch Tenant") -> Tenant:
    tenant = Tenant(name=name)
    db.add(tenant)
    db.commit()
    db.refresh(tenant)
    return tenant


def test_missing_features_row_returns_false(db_session):
    """A tenant with NO tenant_features row must default OFF — we
    refuse to run synthesis on accident for any tenant the operator
    hasn't deliberately reviewed."""
    tenant = _make_tenant(db_session, "No Features Row")
    assert is_nightly_reflection_enabled(db_session, tenant.id) is False


def test_features_row_with_flag_false_returns_false(db_session):
    """Features row exists but flag is False — synthesis stays off."""
    tenant = _make_tenant(db_session, "Flag False")
    features = TenantFeatures(
        tenant_id=tenant.id,
        nightly_reflection_enabled=False,
    )
    db_session.add(features)
    db_session.commit()

    assert is_nightly_reflection_enabled(db_session, tenant.id) is False


def test_features_row_with_flag_true_returns_true(db_session):
    """Operator opt-in path: features row exists and flag is True."""
    tenant = _make_tenant(db_session, "Flag True")
    features = TenantFeatures(
        tenant_id=tenant.id,
        nightly_reflection_enabled=True,
    )
    db_session.add(features)
    db_session.commit()

    assert is_nightly_reflection_enabled(db_session, tenant.id) is True


def test_returns_false_for_unknown_tenant(db_session):
    """An unknown tenant UUID has no row → defaults OFF, no raise."""
    fake_tid = uuid.uuid4()
    assert is_nightly_reflection_enabled(db_session, fake_tid) is False


def test_accepts_string_tenant_id(db_session):
    """The activity layer passes ``tenant_id`` as a string (Temporal
    arguments are JSON-friendly). The killswitch must accept either
    form without raising."""
    tenant = _make_tenant(db_session, "String Tenant")
    features = TenantFeatures(
        tenant_id=tenant.id,
        nightly_reflection_enabled=True,
    )
    db_session.add(features)
    db_session.commit()

    assert is_nightly_reflection_enabled(db_session, str(tenant.id)) is True
