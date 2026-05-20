"""Unit tests for the Teamwork Engine pure-functional layer.

Covers the test plan in docs/plans/2026-05-19-teamwork-engine-design.md
§ "Test plan (Phase 1)":

- evaluate_role_contract returns the active contract when one is in
  effect, None when none active.
- select_norm honours coalition-specific override > tenant-wide default.
- TeamRoleContract / TeamNorm dataclass validation rejects bad inputs.
- Serialise/deserialise round-trip preserves fields.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.schemas.team import (
    ALLOWED_NORM_KEYS,
    ALLOWED_ROLES,
    TeamNorm,
    TeamRoleContract,
)
from app.services.team_engine import (
    deserialize_norm,
    deserialize_role_contract,
    describe_role_split,
    evaluate_role_contract,
    select_norm,
    serialize_norm,
    serialize_role_contract,
)


# ── Fixtures ──────────────────────────────────────────────────────────


NOW = datetime(2026, 5, 19, 12, 0, 0, tzinfo=timezone.utc)


def _make_contract(
    agent_id: str = "claude",
    role: str = "driver",
    scope: str = "execution",
    effective_from: datetime | None = None,
    effective_until: datetime | None = None,
    superseded_by: str | None = None,
    coalition_id: str | None = None,
    conditions: dict | None = None,
    rationale: str = "test rationale",
) -> TeamRoleContract:
    return TeamRoleContract(
        tenant_id="tenant-1",
        coalition_id=coalition_id,
        agent_id=agent_id,
        role=role,
        scope=scope,
        effective_from=(effective_from or NOW - timedelta(days=1)).isoformat(),
        effective_until=effective_until.isoformat() if effective_until else None,
        conditions=conditions or {},
        rationale=rationale,
        superseded_by=superseded_by,
    )


def _make_norm(
    key: str = "turn_taking",
    value: object = "round_robin",
    coalition_id: str | None = None,
    last_confirmed_at: datetime | None = None,
    rationale: str = "test",
) -> TeamNorm:
    return TeamNorm(
        tenant_id="tenant-1",
        coalition_id=coalition_id,
        key=key,
        value=value,
        rationale=rationale,
        last_confirmed_at=(last_confirmed_at or NOW).isoformat(),
    )


# ── Dataclass validation ──────────────────────────────────────────────


def test_role_contract_rejects_bad_role():
    with pytest.raises(ValueError, match="role must be one of"):
        _make_contract(role="boss")


def test_role_contract_rejects_bad_scope():
    with pytest.raises(ValueError, match="scope must be one of"):
        _make_contract(scope="universe")


def test_norm_rejects_bad_key():
    with pytest.raises(ValueError, match="key must be one of"):
        _make_norm(key="unknown_norm")


def test_all_allowed_roles_constructable():
    """Each ALLOWED_ROLES value should be valid input."""
    for role in ALLOWED_ROLES:
        c = _make_contract(role=role)
        assert c.role == role


def test_all_allowed_norm_keys_constructable():
    for key in ALLOWED_NORM_KEYS:
        n = _make_norm(key=key)
        assert n.key == key


# ── is_active_at ──────────────────────────────────────────────────────


def test_contract_active_when_in_effect():
    c = _make_contract(effective_from=NOW - timedelta(days=1))
    assert c.is_active_at(NOW) is True


def test_contract_inactive_before_effective_from():
    c = _make_contract(effective_from=NOW + timedelta(days=1))
    assert c.is_active_at(NOW) is False


def test_contract_inactive_after_effective_until():
    c = _make_contract(
        effective_from=NOW - timedelta(days=10),
        effective_until=NOW - timedelta(days=1),
    )
    assert c.is_active_at(NOW) is False


def test_contract_inactive_when_superseded():
    c = _make_contract(
        effective_from=NOW - timedelta(days=1),
        superseded_by="some-other-uuid",
    )
    assert c.is_active_at(NOW) is False


# ── evaluate_role_contract ────────────────────────────────────────────


def test_evaluate_returns_active_contract():
    active = _make_contract(agent_id="claude", scope="execution")
    inactive = _make_contract(
        agent_id="claude",
        scope="execution",
        effective_until=NOW - timedelta(days=1),
    )
    result = evaluate_role_contract(
        [inactive, active], agent_id="claude", scope="execution", now=NOW,
    )
    assert result is active


def test_evaluate_returns_none_when_none_match():
    inactive = _make_contract(
        agent_id="claude",
        effective_from=NOW + timedelta(days=1),
    )
    result = evaluate_role_contract(
        [inactive], agent_id="claude", scope="execution", now=NOW,
    )
    assert result is None


def test_evaluate_prefers_most_recent_effective_from():
    """Tie-breaking: most recent amendment wins."""
    old = _make_contract(
        rationale="older",
        effective_from=NOW - timedelta(days=10),
    )
    new = _make_contract(
        rationale="newer",
        effective_from=NOW - timedelta(days=1),
    )
    result = evaluate_role_contract(
        [old, new], agent_id="claude", scope="execution", now=NOW,
    )
    assert result is new
    assert result.rationale == "newer"


def test_evaluate_filters_by_scope():
    exec_contract = _make_contract(agent_id="claude", scope="execution")
    review_contract = _make_contract(agent_id="claude", scope="review")
    result = evaluate_role_contract(
        [exec_contract, review_contract],
        agent_id="claude", scope="execution", now=NOW,
    )
    assert result is exec_contract


def test_evaluate_filters_by_agent_id():
    claude = _make_contract(agent_id="claude")
    luna = _make_contract(agent_id="luna")
    result = evaluate_role_contract(
        [claude, luna], agent_id="luna", scope="execution", now=NOW,
    )
    assert result is luna


# ── select_norm ───────────────────────────────────────────────────────


def test_select_norm_coalition_specific_wins_over_tenant_wide():
    tenant_default = _make_norm(value="default_value")
    coalition_specific = _make_norm(
        value="override_value",
        coalition_id="coalition-1",
    )
    result = select_norm(
        [tenant_default, coalition_specific],
        key="turn_taking",
        coalition_id="coalition-1",
    )
    assert result is coalition_specific
    assert result.value == "override_value"


def test_select_norm_tenant_wide_used_when_no_coalition_specific():
    tenant_default = _make_norm(value="default_value")
    result = select_norm(
        [tenant_default],
        key="turn_taking",
        coalition_id="coalition-1",
    )
    assert result is tenant_default


def test_select_norm_returns_none_when_no_match():
    norms = [_make_norm(key="reciprocity")]
    result = select_norm(norms, key="turn_taking")
    assert result is None


def test_select_norm_with_no_coalition_id_picks_tenant_wide():
    """Caller may pass coalition_id=None for the tenant-wide query."""
    tenant_default = _make_norm()
    coalition_specific = _make_norm(coalition_id="other-coalition")
    result = select_norm(
        [tenant_default, coalition_specific],
        key="turn_taking",
        coalition_id=None,
    )
    assert result is tenant_default


# ── Serialization round-trip ──────────────────────────────────────────


def test_role_contract_serialize_roundtrip():
    original = _make_contract(
        conditions={"until_codex_subscription_tier": "team"},
        rationale="The 2026-05-19 role split.",
    )
    blob = serialize_role_contract(original)
    hydrated = deserialize_role_contract(blob)
    assert hydrated is not None
    assert hydrated.agent_id == original.agent_id
    assert hydrated.role == original.role
    assert hydrated.scope == original.scope
    assert hydrated.conditions == original.conditions
    assert hydrated.rationale == original.rationale


def test_norm_serialize_roundtrip():
    original = _make_norm(
        value={"order": ["claude", "luna"]},
        rationale="rotate driver every PR",
    )
    blob = serialize_norm(original)
    hydrated = deserialize_norm(blob)
    assert hydrated is not None
    assert hydrated.key == original.key
    assert hydrated.value == original.value
    assert hydrated.rationale == original.rationale


def test_deserialize_role_contract_returns_none_for_malformed_blob():
    """Read path must not crash on a single bad row."""
    assert deserialize_role_contract("not valid json") is None
    assert deserialize_role_contract('{"missing_required_fields": true}') is None
    # Wrong types
    assert deserialize_role_contract('{"tenant_id": null}') is None


def test_deserialize_norm_returns_none_for_malformed_blob():
    assert deserialize_norm("not valid json") is None
    assert deserialize_norm('{"no_fields": true}') is None


# ── Staleness ─────────────────────────────────────────────────────────


def test_norm_is_stale_after_max_age_days():
    old = _make_norm(last_confirmed_at=NOW - timedelta(days=100))
    assert old.is_stale(now=NOW, max_age_days=90) is True


def test_norm_is_not_stale_within_window():
    fresh = _make_norm(last_confirmed_at=NOW - timedelta(days=30))
    assert fresh.is_stale(now=NOW, max_age_days=90) is False


# ── describe_role_split (rendering helper) ────────────────────────────


def test_describe_role_split_renders_canonical_2026_05_19_example():
    """The 2026-05-19 role split as a typed contract."""
    contract = _make_contract(
        agent_id="claude",
        role="driver",
        scope="execution",
        conditions={"until_codex_subscription_tier": "team"},
        rationale="Opus heavy lift while Codex tier is bumped.",
    )
    description = describe_role_split(contract)
    assert "claude" in description
    assert "driver" in description
    assert "execution" in description
    assert "until_codex_subscription_tier" in description
    assert "Opus heavy lift" in description
