"""Tests for ``app.services.features.update_features`` field gating.

Covers the security gap in PR #541: a non-superuser tenant member calling
``PUT /api/v1/features`` must NOT be able to elevate plan limits, flip
tenant-wide feature toggles, or change ``active_llm_provider`` for every
other member.

The service uses a default-deny allowlist (``_MEMBER_WRITABLE_FIELDS``);
this file pins the allowlist contract so a future PR that adds a new
sensitive field doesn't silently expose it.
"""
from __future__ import annotations

import logging
import uuid
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from app.schemas.tenant_features import TenantFeaturesUpdate
from app.services import features as service


def _make_row(**overrides):
    """Stand-in for a TenantFeatures ORM row.

    The service code only assigns attributes via ``setattr`` and never
    reads from the row, so a SimpleNamespace is enough — no DB needed.
    """
    base = dict(
        tenant_id=uuid.uuid4(),
        default_cli_platform="claude_code",
        github_primary_account=None,
        cpa_export_format="xlsx",
        rl_enabled=False,
        rl_settings=None,
        cli_orchestrator_enabled=False,
        active_llm_provider="gemini_llm",
        agents_enabled=True,
        chat_enabled=True,
        agent_groups_enabled=True,
        datasets_enabled=True,
        multi_llm_enabled=True,
        agent_memory_enabled=True,
        ai_insights_enabled=True,
        ai_recommendations_enabled=True,
        ai_anomaly_detection=True,
        max_agents=10,
        max_agent_groups=5,
        monthly_token_limit=1_000_000,
        storage_limit_gb=10.0,
        hide_agentprovision_branding=False,
        plan_type="starter",
        value_layer_enabled=False,
    )
    base.update(overrides)
    return SimpleNamespace(**base)


@pytest.fixture
def patched_get(monkeypatch):
    """Replace ``service.get_features`` with a stub returning the row we set."""
    row = _make_row()
    monkeypatch.setattr(service, "get_features", lambda db, tid: row)
    return row


@pytest.fixture
def db():
    """A mock SQLAlchemy session — we only assert on add/commit/refresh calls."""
    return MagicMock()


def test_superuser_can_write_every_field(patched_get, db):
    """A superuser PUT must persist all writable fields, including plan limits."""
    update = TenantFeaturesUpdate(
        default_cli_platform="codex",
        max_agents=999,
        plan_type="enterprise",
        active_llm_provider="anthropic_llm",
    )
    result = service.update_features(
        db, patched_get.tenant_id, update, is_superuser=True
    )
    assert result is patched_get
    assert patched_get.default_cli_platform == "codex"
    assert patched_get.max_agents == 999
    assert patched_get.plan_type == "enterprise"
    assert patched_get.active_llm_provider == "anthropic_llm"


def test_non_superuser_cannot_elevate_plan_limits(patched_get, db, caplog):
    """Non-superuser PUT with mixed body persists only the allowed field."""
    update = TenantFeaturesUpdate(
        default_cli_platform="codex",
        max_agents=999,
        plan_type="enterprise",
        active_llm_provider="anthropic_llm",
    )
    with caplog.at_level(logging.WARNING, logger="app.services.features"):
        service.update_features(
            db, patched_get.tenant_id, update, is_superuser=False
        )
    # Allowed field persisted.
    assert patched_get.default_cli_platform == "codex"
    # Sensitive fields untouched.
    assert patched_get.max_agents == 10
    assert patched_get.plan_type == "starter"
    assert patched_get.active_llm_provider == "gemini_llm"
    # Drop is observable in the logs.
    assert any(
        "dropped superuser-only fields" in rec.message for rec in caplog.records
    )


def test_non_superuser_cannot_flip_tenant_wide_toggles(patched_get, db):
    """``*_enabled`` toggles affect every tenant member → superuser-only."""
    update = TenantFeaturesUpdate(
        agents_enabled=False,
        chat_enabled=False,
        ai_insights_enabled=False,
    )
    service.update_features(
        db, patched_get.tenant_id, update, is_superuser=False
    )
    assert patched_get.agents_enabled is True
    assert patched_get.chat_enabled is True
    assert patched_get.ai_insights_enabled is True


def test_non_superuser_inline_cli_picker_happy_path(patched_get, db, caplog):
    """Body with only ``default_cli_platform`` (the InlineCliPicker case)."""
    update = TenantFeaturesUpdate(default_cli_platform="gemini_cli")
    with caplog.at_level(logging.WARNING, logger="app.services.features"):
        service.update_features(
            db, patched_get.tenant_id, update, is_superuser=False
        )
    assert patched_get.default_cli_platform == "gemini_cli"
    # No drop, no warning.
    assert not any(
        "dropped superuser-only fields" in rec.message for rec in caplog.records
    )


def test_is_superuser_defaults_to_false(patched_get, db):
    """Regression guard: the keyword-only ``is_superuser`` defaults to False.

    A bug that swaps the default to True silently grants every caller
    plan-limit access.
    """
    update = TenantFeaturesUpdate(max_agents=999)
    # No is_superuser kwarg — default path.
    service.update_features(db, patched_get.tenant_id, update)
    assert patched_get.max_agents == 10


def test_value_layer_enabled_is_superuser_only(patched_get, db, caplog):
    """The value-layer kill-switch (#647) is a tenant-wide policy
    switch — must require superuser. A non-superuser PUT must NOT
    flip it; the field gets dropped + logged."""
    update = TenantFeaturesUpdate(value_layer_enabled=True)
    with caplog.at_level(logging.WARNING, logger="app.services.features"):
        service.update_features(
            db, patched_get.tenant_id, update, is_superuser=False
        )
    assert patched_get.value_layer_enabled is False, (
        "non-superuser flipped value_layer_enabled — security regression"
    )
    assert any(
        "dropped superuser-only fields" in rec.message
        and "value_layer_enabled" in rec.message
        for rec in caplog.records
    )


def test_superuser_can_flip_value_layer_enabled(patched_get, db):
    """A superuser PUT with value_layer_enabled=True must persist."""
    update = TenantFeaturesUpdate(value_layer_enabled=True)
    service.update_features(
        db, patched_get.tenant_id, update, is_superuser=True
    )
    assert patched_get.value_layer_enabled is True


def test_returns_none_when_row_missing(monkeypatch, db):
    """If no features row exists, return None (the endpoint pre-creates)."""
    monkeypatch.setattr(service, "get_features", lambda db, tid: None)
    update = TenantFeaturesUpdate(default_cli_platform="codex")
    result = service.update_features(
        db, uuid.uuid4(), update, is_superuser=True
    )
    assert result is None
