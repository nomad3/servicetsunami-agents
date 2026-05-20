"""Tests for the Phase 1 PR C operator write endpoints — Pydantic
validation paths only. End-to-end HTTP tests require the FastAPI test
client + JWT setup which isn't wired in this test suite. The Pydantic
validators are the load-bearing safety net for body validation.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest

from app.api.v1.team import (
    AmendRoleContractRequest,
    CreateNormRequest,
    CreateRoleContractRequest,
)
from app.schemas.team import (
    ALLOWED_NORM_KEYS,
    ALLOWED_ROLES,
    ALLOWED_SCOPES,
)


# ── CreateRoleContractRequest validation ──────────────────────────────


def test_create_role_request_accepts_canonical_split():
    """The 2026-05-19 role split shape passes validation."""
    body = CreateRoleContractRequest(
        agent_id=uuid.uuid4(),
        role="driver",
        scope="execution",
        conditions={"until_codex_subscription_tier": "team"},
        rationale="Claude does heavy lifting while Codex tier is bumped.",
    )
    assert body.role == "driver"
    assert body.scope == "execution"


def test_create_role_request_rejects_invalid_role():
    with pytest.raises(ValueError, match="role must be one of"):
        CreateRoleContractRequest(
            agent_id=uuid.uuid4(),
            role="boss",
            scope="execution",
        )


def test_create_role_request_rejects_invalid_scope():
    with pytest.raises(ValueError, match="scope must be one of"):
        CreateRoleContractRequest(
            agent_id=uuid.uuid4(),
            role="driver",
            scope="universe",
        )


def test_create_role_request_accepts_all_known_roles():
    for role in ALLOWED_ROLES:
        body = CreateRoleContractRequest(
            agent_id=uuid.uuid4(),
            role=role,
            scope="execution",
        )
        assert body.role == role


def test_create_role_request_accepts_all_known_scopes():
    for scope in ALLOWED_SCOPES:
        body = CreateRoleContractRequest(
            agent_id=uuid.uuid4(),
            role="driver",
            scope=scope,
        )
        assert body.scope == scope


def test_create_role_request_defaults_effective_from_to_none():
    """effective_from is None by default — handler fills with current
    UTC time. Lets callers omit if they want 'effective immediately'."""
    body = CreateRoleContractRequest(
        agent_id=uuid.uuid4(),
        role="driver",
        scope="execution",
    )
    assert body.effective_from is None


# ── CreateNormRequest validation ──────────────────────────────────────


def test_create_norm_request_accepts_canonical_keys():
    for key in ALLOWED_NORM_KEYS:
        body = CreateNormRequest(key=key, value="some_value")
        assert body.key == key


def test_create_norm_request_rejects_unknown_key():
    with pytest.raises(ValueError, match="key must be one of"):
        CreateNormRequest(key="strange_norm", value="x")


def test_create_norm_request_accepts_arbitrary_value_type():
    """value is intentionally typed Any — norms have per-key semantics."""
    CreateNormRequest(key="turn_taking", value="round_robin")
    CreateNormRequest(key="turn_taking", value={"order": ["claude", "luna"]})
    CreateNormRequest(key="turn_taking", value=[1, 2, 3])
    CreateNormRequest(key="turn_taking", value=42)


# ── AmendRoleContractRequest ──────────────────────────────────────────


def test_amend_request_accepts_partial_overrides():
    """All fields optional — caller chooses what to override."""
    body = AmendRoleContractRequest()
    assert body.effective_from is None
    assert body.effective_until is None
    assert body.conditions is None
    assert body.rationale is None


def test_amend_request_accepts_full_override():
    body = AmendRoleContractRequest(
        effective_from=datetime.now(timezone.utc).isoformat(),
        effective_until=None,
        conditions={"until_codex_subscription_tier": "pro"},
        rationale="Updated after Codex tier bump.",
    )
    assert body.conditions == {"until_codex_subscription_tier": "pro"}
    assert body.rationale == "Updated after Codex tier bump."
