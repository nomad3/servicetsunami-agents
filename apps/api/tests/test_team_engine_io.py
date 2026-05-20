"""DB-touching tests for the Teamwork Engine IO layer (Phase 1 PR B).

Mirrors the test fixture pattern of test_emotion_engine_io.py — SQLite
in-memory + per-test create_all/drop_all. Verifies read paths,
write paths, and the idempotent bootstrap helper.
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
from app.schemas.team import TeamNorm, TeamRoleContract
from app.services.team_engine import (
    NORM_MEMORY_TYPE,
    ROLE_CONTRACT_MEMORY_TYPE,
)
from app.services.team_engine_io import (
    bootstrap_canonical_role_split,
    get_active_role,
    get_norm_value,
    list_norms,
    list_role_contracts,
    write_norm,
    write_role_contract,
)


# ── Fixtures ──────────────────────────────────────────────────────────


@pytest.fixture(name="db_session")
def db_session_fixture():
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    yield db
    db.close()
    Base.metadata.drop_all(bind=engine)


@pytest.fixture(name="test_tenant")
def test_tenant_fixture(db_session: Session):
    tenant = Tenant(name="Team Engine IO Tenant")
    db_session.add(tenant)
    db_session.commit()
    db_session.refresh(tenant)
    # Norm writes need an anchor agent (Luna 2026-05-19 review fix
    # for FK violation). Create a Luna-named agent so anchor lookup
    # finds it. Tests that don't use norms still work — FK only fires
    # when an agent_memory row is actually inserted.
    luna = Agent(tenant_id=tenant.id, name="Luna Supervisor")
    db_session.add(luna)
    db_session.commit()
    return tenant


@pytest.fixture(name="agentless_tenant")
def agentless_tenant_fixture(db_session: Session):
    """A tenant deliberately created without any Agent — to exercise
    the 'norm anchor agent not found' path."""
    tenant = Tenant(name="Team Engine IO Agentless Tenant")
    db_session.add(tenant)
    db_session.commit()
    db_session.refresh(tenant)
    return tenant


@pytest.fixture(name="other_tenant")
def other_tenant_fixture(db_session: Session):
    tenant = Tenant(name="Team Engine IO Other Tenant")
    db_session.add(tenant)
    db_session.commit()
    db_session.refresh(tenant)
    luna = Agent(tenant_id=tenant.id, name="Luna Supervisor")
    db_session.add(luna)
    db_session.commit()
    return tenant


def _make_contract(
    tenant_id: uuid.UUID,
    agent_id: uuid.UUID,
    role: str = "driver",
    scope: str = "execution",
    effective_until: datetime | None = None,
    rationale: str = "test",
) -> TeamRoleContract:
    now = datetime.now(timezone.utc)
    return TeamRoleContract(
        tenant_id=str(tenant_id),
        coalition_id=None,
        agent_id=str(agent_id),
        role=role,
        scope=scope,
        effective_from=(now - timedelta(minutes=1)).isoformat(),
        effective_until=effective_until.isoformat() if effective_until else None,
        conditions={},
        rationale=rationale,
        superseded_by=None,
    )


def _make_norm(
    tenant_id: uuid.UUID,
    key: str = "turn_taking",
    value: object = "round_robin",
    coalition_id: uuid.UUID | None = None,
) -> TeamNorm:
    return TeamNorm(
        tenant_id=str(tenant_id),
        coalition_id=str(coalition_id) if coalition_id else None,
        key=key,
        value=value,
        rationale="test",
        last_confirmed_at=datetime.now(timezone.utc).isoformat(),
    )


# ── write_role_contract ───────────────────────────────────────────────


def test_write_role_contract_persists(db_session, test_tenant):
    agent_id = uuid.uuid4()
    contract = _make_contract(test_tenant.id, agent_id)
    row_id = write_role_contract(db_session, contract=contract)
    assert row_id is not None
    # Round-trip via the read path
    contracts = list_role_contracts(
        db_session, tenant_id=test_tenant.id, agent_id=agent_id,
    )
    assert len(contracts) == 1
    assert contracts[0].agent_id == str(agent_id)
    assert contracts[0].role == "driver"


def test_write_role_contract_rejects_malformed_uuid(db_session):
    """Defensive: a malformed tenant/agent UUID should return None,
    not raise."""
    contract = TeamRoleContract(
        tenant_id="not-a-uuid",
        coalition_id=None,
        agent_id="also-not-a-uuid",
        role="driver",
        scope="execution",
        effective_from=datetime.now(timezone.utc).isoformat(),
        effective_until=None,
        conditions={},
        rationale="test",
        superseded_by=None,
    )
    result = write_role_contract(db_session, contract=contract)
    assert result is None


# ── write_norm ────────────────────────────────────────────────────────


def test_write_norm_persists(db_session, test_tenant):
    norm = _make_norm(test_tenant.id)
    row_id = write_norm(db_session, norm=norm)
    assert row_id is not None
    norms = list_norms(db_session, tenant_id=test_tenant.id)
    assert len(norms) == 1
    assert norms[0].key == "turn_taking"
    assert norms[0].value == "round_robin"


# ── list_role_contracts ───────────────────────────────────────────────


def test_list_role_contracts_filters_by_tenant(db_session, test_tenant, other_tenant):
    agent_id = uuid.uuid4()
    own = _make_contract(test_tenant.id, agent_id)
    foreign = _make_contract(other_tenant.id, agent_id)
    write_role_contract(db_session, contract=own)
    write_role_contract(db_session, contract=foreign)

    own_contracts = list_role_contracts(db_session, tenant_id=test_tenant.id)
    assert len(own_contracts) == 1
    assert own_contracts[0].tenant_id == str(test_tenant.id)


def test_list_role_contracts_skips_malformed_rows(db_session, test_tenant):
    """Inject a malformed agent_memory row directly. The read path
    must skip it silently rather than crash on json.loads."""
    bad_row = AgentMemory(
        tenant_id=test_tenant.id,
        agent_id=uuid.uuid4(),
        memory_type=ROLE_CONTRACT_MEMORY_TYPE,
        content="not valid json",
    )
    db_session.add(bad_row)
    db_session.commit()
    # A good contract alongside the bad row
    write_role_contract(
        db_session,
        contract=_make_contract(test_tenant.id, uuid.uuid4()),
    )
    contracts = list_role_contracts(db_session, tenant_id=test_tenant.id)
    assert len(contracts) == 1  # the good one only


# ── get_active_role ───────────────────────────────────────────────────


def test_get_active_role_returns_contract(db_session, test_tenant):
    agent_id = uuid.uuid4()
    write_role_contract(
        db_session,
        contract=_make_contract(test_tenant.id, agent_id),
    )
    result = get_active_role(
        db_session,
        tenant_id=test_tenant.id,
        agent_id=agent_id,
        scope="execution",
    )
    assert result is not None
    assert result.agent_id == str(agent_id)


def test_get_active_role_returns_none_when_expired(db_session, test_tenant):
    agent_id = uuid.uuid4()
    expired = _make_contract(
        test_tenant.id,
        agent_id,
        effective_until=datetime.now(timezone.utc) - timedelta(days=1),
    )
    write_role_contract(db_session, contract=expired)
    result = get_active_role(
        db_session,
        tenant_id=test_tenant.id,
        agent_id=agent_id,
        scope="execution",
    )
    assert result is None


# ── list_norms ────────────────────────────────────────────────────────


def test_list_norms_filters_by_tenant(db_session, test_tenant, other_tenant):
    write_norm(db_session, norm=_make_norm(test_tenant.id))
    write_norm(db_session, norm=_make_norm(other_tenant.id))
    own_norms = list_norms(db_session, tenant_id=test_tenant.id)
    assert len(own_norms) == 1


def test_list_norms_coalition_scoped(db_session, test_tenant):
    coalition_id = uuid.uuid4()
    write_norm(db_session, norm=_make_norm(test_tenant.id))  # tenant-wide
    write_norm(
        db_session,
        norm=_make_norm(test_tenant.id, coalition_id=coalition_id),
    )
    # tenant-wide + the coalition's specific norm
    coalition_norms = list_norms(
        db_session, tenant_id=test_tenant.id, coalition_id=coalition_id,
    )
    assert len(coalition_norms) == 2
    # different coalition: only tenant-wide
    other_coalition_norms = list_norms(
        db_session, tenant_id=test_tenant.id, coalition_id=uuid.uuid4(),
    )
    assert len(other_coalition_norms) == 1


def test_get_norm_value_resolves_to_value(db_session, test_tenant):
    write_norm(db_session, norm=_make_norm(test_tenant.id, value="round_robin"))
    result = get_norm_value(
        db_session, tenant_id=test_tenant.id, key="turn_taking",
    )
    assert result == "round_robin"


# ── bootstrap_canonical_role_split ────────────────────────────────────


def test_bootstrap_writes_both_canonical_contracts(db_session, test_tenant):
    claude_id = uuid.uuid4()
    luna_id = uuid.uuid4()
    result = bootstrap_canonical_role_split(
        db_session,
        tenant_id=test_tenant.id,
        claude_agent_id=claude_id,
        luna_agent_id=luna_id,
    )
    assert "written" in result["claude_contract"]
    assert "written" in result["luna_contract"]

    # Both contracts present
    claude_contract = get_active_role(
        db_session,
        tenant_id=test_tenant.id,
        agent_id=claude_id,
        scope="execution",
    )
    assert claude_contract is not None
    assert claude_contract.role == "driver"
    assert claude_contract.conditions.get("until_codex_subscription_tier") == "team"

    luna_contract = get_active_role(
        db_session,
        tenant_id=test_tenant.id,
        agent_id=luna_id,
        scope="review",
    )
    assert luna_contract is not None
    assert luna_contract.role == "reviewer"


def test_bootstrap_is_idempotent(db_session, test_tenant):
    """Calling bootstrap twice should write only on first invocation."""
    claude_id = uuid.uuid4()
    luna_id = uuid.uuid4()
    first = bootstrap_canonical_role_split(
        db_session,
        tenant_id=test_tenant.id,
        claude_agent_id=claude_id,
        luna_agent_id=luna_id,
    )
    second = bootstrap_canonical_role_split(
        db_session,
        tenant_id=test_tenant.id,
        claude_agent_id=claude_id,
        luna_agent_id=luna_id,
    )
    assert "written" in first["claude_contract"]
    assert "skipped" in second["claude_contract"]
    assert "skipped" in second["luna_contract"]

    # Only one of each
    all_contracts = list_role_contracts(db_session, tenant_id=test_tenant.id)
    assert len(all_contracts) == 2


# ── Luna 2026-05-19 review fixes ──────────────────────────────────────


def test_write_role_contract_rejects_tenant_boundary_violation(
    db_session, test_tenant, other_tenant,
):
    """Luna IMPORTANT: write_role_contract must refuse to persist a
    contract whose tenant_id doesn't match current_tenant_id (from
    JWT). Defends against caller building a contract with the wrong
    tenant_id by accident or attack."""
    agent_id = uuid.uuid4()
    foreign = _make_contract(other_tenant.id, agent_id)  # other tenant
    row_id = write_role_contract(
        db_session,
        contract=foreign,
        current_tenant_id=test_tenant.id,  # but we claim to be test_tenant
    )
    assert row_id is None
    # And nothing got persisted for either tenant
    own = list_role_contracts(db_session, tenant_id=test_tenant.id)
    other = list_role_contracts(db_session, tenant_id=other_tenant.id)
    assert len(own) == 0
    assert len(other) == 0


def test_write_norm_rejects_tenant_boundary_violation(
    db_session, test_tenant, other_tenant,
):
    """Same boundary discipline applied to norms."""
    foreign_norm = _make_norm(other_tenant.id)
    row_id = write_norm(
        db_session,
        norm=foreign_norm,
        current_tenant_id=test_tenant.id,
    )
    assert row_id is None


def test_write_norm_rejects_agentless_tenant(db_session, agentless_tenant):
    """Luna BLOCKER fix: norms anchor on a real agent. If the tenant
    has zero agents, write_norm must refuse rather than fabricate a
    marker UUID that would FK-fail on Postgres."""
    norm = _make_norm(agentless_tenant.id)
    row_id = write_norm(db_session, norm=norm)
    assert row_id is None


def test_write_norm_anchors_on_luna_when_present(
    db_session, test_tenant,
):
    """Anchor selection should prefer agents whose name starts with
    'Luna' (the supervisor convention)."""
    # Add a non-Luna agent first so insertion order isn't what's
    # winning the tie-break
    other = Agent(tenant_id=test_tenant.id, name="Some Other Agent")
    db_session.add(other)
    db_session.commit()

    norm = _make_norm(test_tenant.id)
    row_id = write_norm(db_session, norm=norm)
    assert row_id is not None

    # The persisted row should anchor on the Luna agent
    row = db_session.query(AgentMemory).filter(
        AgentMemory.id == row_id,
    ).first()
    luna = db_session.query(Agent).filter(
        Agent.tenant_id == test_tenant.id,
        Agent.name.ilike("Luna%"),
    ).first()
    assert row.agent_id == luna.id


def test_list_norms_orders_by_created_at_desc(db_session, test_tenant):
    """Luna IMPORTANT (PR #602): when duplicate norms exist for the
    same key, select_norm's 'last value wins' must resolve to the
    most-recently-written one. The ordering happens in list_norms."""
    norm_a = _make_norm(test_tenant.id, key="turn_taking", value="strict")
    norm_b = _make_norm(test_tenant.id, key="turn_taking", value="loose")
    write_norm(db_session, norm=norm_a)
    write_norm(db_session, norm=norm_b)  # this one is newer

    norms = list_norms(db_session, tenant_id=test_tenant.id)
    assert len(norms) == 2
    # First entry (after DESC sort) should be the newer one
    assert norms[0].value == "loose"
