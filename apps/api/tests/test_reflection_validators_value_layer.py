"""Tests for the value-layer gate in reflection_validators (#647 PR 4).

Locks the chain extension: validate_reflection runs the existing
4 validators (citation, entity_grounding, next_move_safety,
creative_opt_in) then calls _validate_against_value_set as the
final step.

Locked properties:
  - Value-layer block on a `next_move` kind reflection that
    matches a protect slug → ValidationResult.fail with
    'value_layer_block' reason. (§8 success criterion #2 from the
    design doc actually fires.)
  - Value-layer block on a `value_proposal` kind (Phase 2) →
    same shape — locks the contract for the upcoming PR.
  - 'risk' / 'idea' / 'tension' / 'creative' kinds → consult sets
    intent='read', protect matches return warn (not block),
    chain passes.
  - Consult crash (DB transient, etc.) → fail-open. The synthesis
    loop must NEVER deadlock around a transient consult error.
  - Value-layer module unavailable → fail-open. Legacy test
    fixtures + historical-replay tools that don't deploy the
    full value-layer surface still produce passing validations.
"""
from __future__ import annotations

import uuid
from unittest.mock import MagicMock, patch

import pytest

from app.schemas.reflection import NightlyReflection
from app.services.reflection_validators import (
    ValidationResult,
    _validate_against_value_set,
    validate_reflection,
)


def _refl(
    *,
    kind: str = "risk",
    content: str = "Pattern observed: deploys to production-main fail at scale",
    source_memory_ids=None,
    confidence: float = 0.7,
    tenant_id: str = "11111111-1111-1111-1111-111111111111",
    agent_id: str = "22222222-2222-2222-2222-222222222222",
    day: str = "2026-05-21",
    ts: str = "2026-05-21T12:00:00+00:00",
) -> NightlyReflection:
    return NightlyReflection(
        tenant_id=tenant_id,
        agent_id=agent_id,
        day=day,
        kind=kind,
        content=content,
        source_memory_ids=source_memory_ids
        or [str(uuid.uuid4())],
        confidence=confidence,
        ts=ts,
    )


# ── Value-layer gate via _validate_against_value_set ──────────────────


def test_value_layer_block_on_next_move_reflection(monkeypatch):
    """next_move kind triggers intent='mutate' in the IO shim. A
    protect match returns block → ValidationResult.fail. This
    locks the §8 success criterion #2 ("reflection mentions protect
    item but proposes touching it gets blocked at write time")."""
    from app.services.agent_value_set import ValueVerdict

    blocked = ValueVerdict.block(
        reason="protect_match: production-main",
        point="reflection",
        item={"slug": "production-main", "description": "prod main",
              "added_at": "x", "added_by": "operator",
              "evidence_memory_ids": []},
    )

    with patch(
        "app.services.agent_value_set_io.consult_reflection",
        return_value=blocked,
    ):
        r = _validate_against_value_set(
            _refl(kind="next_move",
                  content="Tomorrow: merge into production-main"),
            db=MagicMock(),
            current_tenant_id=uuid.uuid4(),
        )
    assert not r.ok
    assert "value_layer_block" in r.reason
    assert "production-main" in r.reason


def test_value_layer_block_on_value_proposal_reflection(monkeypatch):
    """value_proposal kind (Phase 2) also gets intent='mutate' →
    protect match blocks. Locks the contract before PR 7 lands the
    synthesis mechanism."""
    from app.services.agent_value_set import ValueVerdict

    blocked = ValueVerdict.block(
        reason="protect_match: production-main",
        point="reflection",
        item={"slug": "production-main", "description": "prod main",
              "added_at": "x", "added_by": "operator",
              "evidence_memory_ids": []},
    )

    with patch(
        "app.services.agent_value_set_io.consult_reflection",
        return_value=blocked,
    ):
        r = _validate_against_value_set(
            _refl(kind="value_proposal",
                  content="Propose removing protect on production-main"),
            db=MagicMock(),
            current_tenant_id=uuid.uuid4(),
        )
    assert not r.ok


def test_descriptive_kinds_pass_on_warn(monkeypatch):
    """A protect match on a `risk` reflection (descriptive)
    returns warn from the IO layer (intent='read'). The chain
    must NOT fail — warn → pass. Locks §6 invariant "protect
    blocks MUTATION not mention."""
    from app.services.agent_value_set import ValueVerdict

    warned = ValueVerdict.warn(
        reason="protect_match_read_only: production-main",
        point="reflection",
        item={"slug": "production-main", "description": "prod main",
              "added_at": "x", "added_by": "operator",
              "evidence_memory_ids": []},
    )

    with patch(
        "app.services.agent_value_set_io.consult_reflection",
        return_value=warned,
    ):
        for kind in ("risk", "idea", "tension", "creative"):
            r = _validate_against_value_set(
                _refl(kind=kind,
                      content="Discussion about production-main"),
                db=MagicMock(),
                current_tenant_id=uuid.uuid4(),
            )
            assert r.ok, f"warn must not block on descriptive kind={kind}"


def test_allow_on_no_match_or_empty_value_set(monkeypatch):
    """When the consult returns allow (no protect match OR empty
    value set OR kill-switch OFF), the validator passes
    immediately. This is the default-OFF behavior."""
    from app.services.agent_value_set import ValueVerdict

    for reason in (
        "empty_value_set", "kill_switch_off", "no_match",
        "pursue_match: morning-report",
    ):
        verdict = ValueVerdict(
            decision="allow", reason=reason,
            matched_item=None, consultation_point="reflection",
        )
        with patch(
            "app.services.agent_value_set_io.consult_reflection",
            return_value=verdict,
        ):
            r = _validate_against_value_set(
                _refl(kind="next_move", content="Tomorrow: do X"),
                db=MagicMock(),
                current_tenant_id=uuid.uuid4(),
            )
            assert r.ok, f"allow/{reason} must pass"


def test_consult_crash_fails_open(monkeypatch):
    """A consult crash (DB transient error etc.) MUST NOT block the
    synthesis loop. The reflection gets written; the operator sees
    the error log and can investigate."""
    with patch(
        "app.services.agent_value_set_io.consult_reflection",
        side_effect=RuntimeError("simulated DB transient"),
    ):
        r = _validate_against_value_set(
            _refl(kind="next_move", content="Tomorrow: do X"),
            db=MagicMock(),
            current_tenant_id=uuid.uuid4(),
        )
    assert r.ok  # fail-open


def test_value_layer_module_unavailable_passes(monkeypatch):
    """Lazy-import failure (legacy test fixtures, historical replay)
    must not crash the chain. Pass through cleanly."""
    import sys
    # Force the lazy import to fail by injecting a sentinel that
    # raises when accessed.
    original = sys.modules.get("app.services.agent_value_set_io")
    sys.modules["app.services.agent_value_set_io"] = None
    try:
        # When sys.modules has None for a module, `import X` raises
        # ImportError. The validator's bare except should catch it.
        r = _validate_against_value_set(
            _refl(kind="next_move", content="Tomorrow"),
            db=MagicMock(),
            current_tenant_id=uuid.uuid4(),
        )
        assert r.ok  # fail-open
    finally:
        if original is not None:
            sys.modules["app.services.agent_value_set_io"] = original
        else:
            del sys.modules["app.services.agent_value_set_io"]


# ── Chain integration ─────────────────────────────────────────────────


def test_validate_reflection_runs_value_layer_after_existing_validators(
    monkeypatch,
):
    """Locked: the value-layer gate is the LAST step in the chain.
    A reflection that fails an earlier validator (citation, entity
    grounding, etc.) must NOT reach the value-layer consult.
    Tripwire test."""
    from app.services.agent_value_set import ValueVerdict

    consult_calls = {"n": 0}

    def _consult(*a, **kw):
        consult_calls["n"] += 1
        return ValueVerdict.allow(reason="no_match", point="reflection")

    with patch(
        "app.services.agent_value_set_io.consult_reflection",
        side_effect=_consult,
    ), patch(
        "app.services.reflection_validators.validate_citation",
        return_value=ValidationResult.fail("citation_unknown_ids: [...]"),
    ):
        r = validate_reflection(
            _refl(kind="next_move"),
            db=MagicMock(),
            current_tenant_id=uuid.uuid4(),
        )
    assert not r.ok
    assert "citation_unknown_ids" in r.reason
    # Value-layer consult should NOT have been called
    assert consult_calls["n"] == 0, (
        "value-layer gate fired even though an earlier validator failed"
    )


def test_validate_reflection_passes_when_all_validators_clean(monkeypatch):
    """End-to-end: all 5 validators pass → ValidationResult.pass_().
    Locks the chain order doesn't get rearranged accidentally."""
    from app.services.agent_value_set import ValueVerdict

    with patch(
        "app.services.reflection_validators.validate_citation",
        return_value=ValidationResult.pass_(),
    ), patch(
        "app.services.reflection_validators.validate_next_move_safety",
        return_value=ValidationResult.pass_(),
    ), patch(
        "app.services.reflection_validators.validate_creative_opt_in",
        return_value=ValidationResult.pass_(),
    ), patch(
        "app.services.agent_value_set_io.consult_reflection",
        return_value=ValueVerdict.allow(
            reason="kill_switch_off", point="reflection",
        ),
    ):
        r = validate_reflection(
            _refl(kind="next_move"),
            db=MagicMock(),
            current_tenant_id=uuid.uuid4(),
            source_memory_contents=["the production-main branch"],
        )
    assert r.ok
