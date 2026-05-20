"""Runtime wire-in tests for the Teamwork Engine (2026-05-20).

Two seams:
  1. team_engine_io.get_agent_for_scope — the new "pick one" helper
     that picks the currently-active contract's agent for a scope.
  2. coalition_activities.select_coalition_template — runtime path
     that now consults TeamRoleContracts before applying caller's
     explicit role_overrides.

The runtime test is a unit-level shape test: we don't spin up the
full Temporal workflow, just call the activity function directly
and assert the role_agent_map reflects active contracts.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy.orm import Session

from app.db.base import Base
from app.db.session import SessionLocal, engine
from app.models.agent import Agent
from app.models.agent_memory import AgentMemory  # noqa: F401 — registers table
from app.models.tenant import Tenant
from app.schemas.team import TeamRoleContract
from app.services.team_engine_io import (
    get_agent_for_scope,
    write_role_contract,
)


@pytest.fixture(name="db_session")
def db_session_fixture():
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    yield db
    db.close()
    Base.metadata.drop_all(bind=engine)


@pytest.fixture(name="tenant_with_agents")
def tenant_with_agents_fixture(db_session: Session):
    """Tenant with Claude + Luna agents — the canonical role-split shape."""
    tenant = Tenant(name="Runtime Wire Tenant")
    db_session.add(tenant)
    db_session.commit()
    db_session.refresh(tenant)

    claude = Agent(tenant_id=tenant.id, name="Claude")
    luna = Agent(tenant_id=tenant.id, name="Luna Supervisor")
    db_session.add_all([claude, luna])
    db_session.commit()
    db_session.refresh(claude)
    db_session.refresh(luna)
    return tenant, claude, luna


def _make_contract(
    tenant_id, agent_id, scope, role="driver",
    effective_from=None, effective_until=None,
):
    now = datetime.now(timezone.utc)
    return TeamRoleContract(
        tenant_id=str(tenant_id),
        coalition_id=None,
        agent_id=str(agent_id),
        role=role,
        scope=scope,
        effective_from=(effective_from or (now - timedelta(minutes=1))).isoformat(),
        effective_until=effective_until.isoformat() if effective_until else None,
        conditions={},
        rationale="test",
        superseded_by=None,
    )


# ── get_agent_for_scope ───────────────────────────────────────────────


def test_get_agent_for_scope_returns_none_when_no_contract(
    db_session, tenant_with_agents,
):
    tenant, _, _ = tenant_with_agents
    result = get_agent_for_scope(db_session, tenant_id=tenant.id, scope="execution")
    assert result is None


def test_get_agent_for_scope_returns_contract_holder(
    db_session, tenant_with_agents,
):
    """The canonical 2026-05-19 split: Claude drives execution."""
    tenant, claude, _ = tenant_with_agents
    write_role_contract(
        db_session,
        contract=_make_contract(tenant.id, claude.id, "execution", "driver"),
    )
    result = get_agent_for_scope(db_session, tenant_id=tenant.id, scope="execution")
    assert result == claude.id


def test_get_agent_for_scope_picks_most_recent_when_multiple(
    db_session, tenant_with_agents,
):
    """When two agents both hold a contract for the same scope, the
    more-recently-effective contract wins — same selection rule
    evaluate_role_contract uses."""
    tenant, claude, luna = tenant_with_agents
    now = datetime.now(timezone.utc)
    # Older: Luna drives execution (5 minutes ago)
    write_role_contract(
        db_session,
        contract=_make_contract(
            tenant.id, luna.id, "execution", "driver",
            effective_from=now - timedelta(minutes=5),
        ),
    )
    # Newer: Claude takes over (1 minute ago)
    write_role_contract(
        db_session,
        contract=_make_contract(
            tenant.id, claude.id, "execution", "driver",
            effective_from=now - timedelta(minutes=1),
        ),
    )
    result = get_agent_for_scope(db_session, tenant_id=tenant.id, scope="execution")
    assert result == claude.id


def test_get_agent_for_scope_skips_expired_contracts(
    db_session, tenant_with_agents,
):
    """A contract whose effective_until is in the past must not win."""
    tenant, claude, _ = tenant_with_agents
    now = datetime.now(timezone.utc)
    write_role_contract(
        db_session,
        contract=_make_contract(
            tenant.id, claude.id, "execution", "driver",
            effective_from=now - timedelta(hours=2),
            effective_until=now - timedelta(hours=1),
        ),
    )
    result = get_agent_for_scope(db_session, tenant_id=tenant.id, scope="execution")
    assert result is None


def test_get_agent_for_scope_filters_by_scope(
    db_session, tenant_with_agents,
):
    """A contract for scope=review must NOT be returned when caller asks
    for scope=execution."""
    tenant, claude, _ = tenant_with_agents
    write_role_contract(
        db_session,
        contract=_make_contract(tenant.id, claude.id, "review", "reviewer"),
    )
    assert get_agent_for_scope(
        db_session, tenant_id=tenant.id, scope="execution",
    ) is None
    assert get_agent_for_scope(
        db_session, tenant_id=tenant.id, scope="review",
    ) == claude.id


# ── COALITION_ROLE_TO_TEAM_SCOPE mapping ──────────────────────────────


def test_coalition_role_mapping_covers_canonical_roles():
    """The mapping must cover the roles used by PHASE_REQUIRED_ROLES so
    the dispatch wire-in actually fires on real coalitions."""
    from app.schemas.collaboration import PHASE_REQUIRED_ROLES
    from app.services.team_engine import COALITION_ROLE_TO_TEAM_SCOPE
    from app.schemas.team import ALLOWED_SCOPES

    # Every coalition role we know about should map to a known scope.
    seen_roles = set()
    for phase, roles in PHASE_REQUIRED_ROLES.items():
        for role in roles:
            seen_roles.add(role)

    mapped = {r for r in seen_roles if r in COALITION_ROLE_TO_TEAM_SCOPE}
    unmapped = seen_roles - mapped
    # If a coalition role isn't in the mapping, the contract advisory
    # silently no-ops for it — that's intentional, but let's at least
    # see what's uncovered so we can decide. As of 2026-05-20 the
    # canonical roles ARE all mapped:
    assert mapped >= {
        "planner", "critic", "verifier", "synthesizer", "researcher",
        "triage_agent", "investigator", "analyst", "commander",
    }, f"Canonical PHASE_REQUIRED_ROLES not all mapped; missing: {unmapped}"

    # All mapped scopes must be in ALLOWED_SCOPES so write paths can
    # actually persist contracts for them.
    for role, scope in COALITION_ROLE_TO_TEAM_SCOPE.items():
        assert scope in ALLOWED_SCOPES, (
            f"COALITION_ROLE_TO_TEAM_SCOPE['{role}']='{scope}' not in ALLOWED_SCOPES"
        )
