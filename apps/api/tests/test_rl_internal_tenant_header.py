"""F9 P1 hardening — X-Tenant-Id header enforcement on /internal/experience
and /internal/provider-council.

Before this PR: only the X-Internal-Key was checked; the tenant scope
came from the request body. RCE in any internal-key holder (code-worker,
mcp-tools, orchestration-worker — cf. F1) could write arbitrary RL
experiences into ANY tenant's policy state by lying about the body's
tenant_id.

This file locks four invariants:

  1. Missing X-Tenant-Id header → 400
  2. Body tenant_id mismatching X-Tenant-Id header → 400
  3. Cross-tenant experience_id on /internal/provider-council → 403
  4. Happy path (header == body, valid key) → write succeeds
"""
from __future__ import annotations

import uuid
from unittest.mock import patch, MagicMock

import pytest
from fastapi.testclient import TestClient

# Marker: the TestClient-based tests below trigger Base.metadata model
# compilation which fails on sqlite for PG types (INET / JSONB /
# pgvector). The api_integration job runs them against real Postgres.
# The first 5 unit tests target the helper directly (no app spin-up)
# and run in the regular api job.


# ── Helper-direct unit tests (no app spin-up, run in api job) ────────


def test_helper_rejects_missing_x_tenant_id(monkeypatch):
    """Invariant 1 (helper-direct): missing X-Tenant-Id → 400."""
    from fastapi import HTTPException
    from app.api.v1.rl import _verify_internal_key_and_tenant
    from app.core.config import settings

    monkeypatch.setattr(settings, "API_INTERNAL_KEY", "k", raising=False)
    with pytest.raises(HTTPException) as exc:
        _verify_internal_key_and_tenant(
            body_tenant_id=str(uuid.uuid4()),
            x_internal_key="k",
            x_tenant_id=None,
        )
    assert exc.value.status_code == 400
    assert "X-Tenant-Id" in exc.value.detail


def test_helper_rejects_body_header_mismatch(monkeypatch):
    """Invariant 2 (helper-direct): mismatched header vs body → 400."""
    from fastapi import HTTPException
    from app.api.v1.rl import _verify_internal_key_and_tenant
    from app.core.config import settings

    monkeypatch.setattr(settings, "API_INTERNAL_KEY", "k", raising=False)
    header_tid = uuid.uuid4()
    body_tid = uuid.uuid4()
    with pytest.raises(HTTPException) as exc:
        _verify_internal_key_and_tenant(
            body_tenant_id=str(body_tid),
            x_internal_key="k",
            x_tenant_id=header_tid,
        )
    assert exc.value.status_code == 400
    assert "does not match" in exc.value.detail


def test_helper_rejects_bad_internal_key(monkeypatch):
    """Bad internal key → 401 (before tenant check, so an attacker
    without the key cannot probe header behavior)."""
    from fastapi import HTTPException
    from app.api.v1.rl import _verify_internal_key_and_tenant
    from app.core.config import settings

    monkeypatch.setattr(settings, "API_INTERNAL_KEY", "k", raising=False)
    monkeypatch.setattr(settings, "MCP_API_KEY", "mk", raising=False)
    tid = uuid.uuid4()
    with pytest.raises(HTTPException) as exc:
        _verify_internal_key_and_tenant(
            body_tenant_id=str(tid),
            x_internal_key="WRONG",
            x_tenant_id=tid,
        )
    assert exc.value.status_code == 401


def test_helper_happy_path_returns_silently(monkeypatch):
    """Happy: matching header + body + key → no raise."""
    from app.api.v1.rl import _verify_internal_key_and_tenant
    from app.core.config import settings

    monkeypatch.setattr(settings, "API_INTERNAL_KEY", "k", raising=False)
    tid = uuid.uuid4()
    # Should not raise.
    _verify_internal_key_and_tenant(
        body_tenant_id=str(tid),
        x_internal_key="k",
        x_tenant_id=tid,
    )


def test_helper_accepts_mcp_key_alternative(monkeypatch):
    """Both API_INTERNAL_KEY and MCP_API_KEY are accepted."""
    from app.api.v1.rl import _verify_internal_key_and_tenant
    from app.core.config import settings

    monkeypatch.setattr(settings, "API_INTERNAL_KEY", "k", raising=False)
    monkeypatch.setattr(settings, "MCP_API_KEY", "mk", raising=False)
    tid = uuid.uuid4()
    _verify_internal_key_and_tenant(
        body_tenant_id=str(tid),
        x_internal_key="mk",  # the alternate key
        x_tenant_id=tid,
    )


# ── End-to-end tests (require Postgres — integration marker) ──────────


@pytest.fixture
def client():
    from app.main import app
    return TestClient(app)


# Apply the integration marker to ALL TestClient-based tests below
# (per-function so the helper tests above stay in the api job).
pytest_integration = pytest.mark.integration


@pytest.fixture
def stub_internal_keys(monkeypatch):
    """Use deterministic internal keys for the test."""
    from app.core.config import settings
    monkeypatch.setattr(settings, "API_INTERNAL_KEY", "test-api-key")
    monkeypatch.setattr(settings, "MCP_API_KEY", "test-mcp-key")
    return "test-api-key"


@pytest.fixture
def stub_log_experience(monkeypatch):
    """No-op the actual DB write so the test focuses on the auth/header
    gate only — the gate runs BEFORE any DB call."""
    from app.services import rl_experience_service
    calls = []

    def _fake(*a, **kw):
        calls.append(kw)
        return None

    monkeypatch.setattr(
        rl_experience_service, "log_experience", _fake,
    )
    return calls


@pytest.mark.integration
def test_internal_experience_rejects_missing_x_tenant_id(
    client, stub_internal_keys, stub_log_experience,
):
    """Invariant 1: no X-Tenant-Id → 400."""
    response = client.post(
        "/api/v1/rl/internal/experience",
        headers={"X-Internal-Key": stub_internal_keys},
        json={
            "tenant_id": str(uuid.uuid4()),
            "decision_point": "code_task",
            "state": {},
            "action": {},
        },
    )
    assert response.status_code == 400, response.text
    assert "X-Tenant-Id" in response.text
    assert len(stub_log_experience) == 0, (
        "log_experience must NOT fire when the header check fails"
    )


@pytest.mark.integration
def test_internal_experience_rejects_body_header_mismatch(
    client, stub_internal_keys, stub_log_experience,
):
    """Invariant 2: body tenant_id != X-Tenant-Id header → 400. This is
    the actual F9 vulnerability: a malicious caller with the internal
    key writing into a foreign tenant by lying in the body."""
    header_tid = str(uuid.uuid4())
    body_tid = str(uuid.uuid4())  # DIFFERENT tenant
    response = client.post(
        "/api/v1/rl/internal/experience",
        headers={
            "X-Internal-Key": stub_internal_keys,
            "X-Tenant-Id": header_tid,
        },
        json={
            "tenant_id": body_tid,
            "decision_point": "code_task",
            "state": {},
            "action": {},
        },
    )
    assert response.status_code == 400, response.text
    assert "does not match" in response.text
    assert len(stub_log_experience) == 0


@pytest.mark.integration
def test_internal_experience_rejects_bad_internal_key(
    client, stub_internal_keys, stub_log_experience,
):
    """Legacy invariant preserved: bad internal key → 401 (before the
    X-Tenant-Id check, so an attacker without the key can't even probe
    the header behavior)."""
    tid = str(uuid.uuid4())
    response = client.post(
        "/api/v1/rl/internal/experience",
        headers={
            "X-Internal-Key": "WRONG-KEY",
            "X-Tenant-Id": tid,
        },
        json={
            "tenant_id": tid,
            "decision_point": "code_task",
            "state": {},
            "action": {},
        },
    )
    assert response.status_code == 401, response.text


@pytest.mark.integration
def test_internal_experience_happy_path_writes(
    client, stub_internal_keys, stub_log_experience,
):
    """Invariant 4: when header + body + key all match, the write
    succeeds and log_experience fires exactly once."""
    tid = str(uuid.uuid4())
    response = client.post(
        "/api/v1/rl/internal/experience",
        headers={
            "X-Internal-Key": stub_internal_keys,
            "X-Tenant-Id": tid,
        },
        json={
            "tenant_id": tid,
            "decision_point": "code_task",
            "state": {"k": "v"},
            "action": {"platform": "claude_code"},
            "state_text": "test state",
        },
    )
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "logged"
    assert len(stub_log_experience) == 1
    # The recorded tenant_id matches the HEADER (which is the same as
    # body — but the header is the source of truth).
    assert str(stub_log_experience[0]["tenant_id"]) == tid


@pytest.mark.integration
def test_provider_council_rejects_cross_tenant_experience_id(
    client, stub_internal_keys, monkeypatch,
):
    """Invariant 3: even with header+body matching the caller's tenant,
    an experience_id that belongs to a DIFFERENT tenant must return 403.
    Guards against a leaked experience_id from one tenant being mutated
    by the holder of the internal key targeting their own tenant."""
    from app.models.rl_experience import RLExperience

    caller_tid = str(uuid.uuid4())
    foreign_tid = uuid.uuid4()
    foreign_exp_id = uuid.uuid4()

    # Stub the DB query to return an experience with foreign_tid as
    # the owner, regardless of which UUID the caller passed.
    fake_exp = MagicMock(spec=RLExperience)
    fake_exp.tenant_id = foreign_tid
    fake_exp.reward_components = None

    def _query_returns_fake(*a, **kw):
        chained = MagicMock()
        chained.filter.return_value.first.return_value = fake_exp
        return chained

    monkeypatch.setattr(
        "app.api.v1.rl.db", MagicMock(),
        raising=False,
    )
    # Patch the actual SQLAlchemy session method used in the endpoint.
    with patch(
        "app.api.deps.SessionLocal"
    ) as mock_session_local:
        session_instance = MagicMock()
        session_instance.query.side_effect = _query_returns_fake
        mock_session_local.return_value = session_instance

        response = client.post(
            "/api/v1/rl/internal/provider-council",
            headers={
                "X-Internal-Key": stub_internal_keys,
                "X-Tenant-Id": caller_tid,
            },
            json={
                "tenant_id": caller_tid,
                "experience_id": str(foreign_exp_id),
                "provider_council": {"score": 0.5},
            },
        )

    assert response.status_code == 403, response.text
    assert "different tenant" in response.text
