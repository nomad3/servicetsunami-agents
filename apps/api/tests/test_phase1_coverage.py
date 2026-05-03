"""Phase 1 coverage push.

Targeted unit tests for pure-logic modules in `app/`. The goal is not to
exhaustively cover every line — that's what the integration suite is for —
but to ratchet coverage up on small, stable, side-effect-free helpers that
the rest of the codebase already depends on.

Modules touched here:

- app/core/security.py        — password hashing + JWT minting
- app/core/config.py          — strip-strings validator
- app/services/safety_policies — risk taxonomy + default-decision matrix
- app/services/media_utils    — MIME classification
- app/services/commitment_extractor — disabled stubs
- app/memory/feature_flag     — V2 gating logic
- app/memory/types            — dataclass contracts
- app/services/_agent_ordering — agent status rank expression
- app/services/workflow_templates — template metadata helpers
"""
from __future__ import annotations

import os
import uuid
from datetime import timedelta
from unittest.mock import MagicMock

import pytest


# ── app/core/security.py ────────────────────────────────────────────────────

class TestSecurity:
    def test_password_round_trip(self):
        from app.core.security import get_password_hash, verify_password
        hashed = get_password_hash("hunter2")
        assert hashed != "hunter2"
        assert verify_password("hunter2", hashed) is True
        assert verify_password("wrong", hashed) is False

    def test_password_truncated_to_72_bytes(self):
        """bcrypt has a 72-byte ceiling; longer inputs must not raise."""
        from app.core.security import get_password_hash, verify_password
        long_pw = "x" * 200
        hashed = get_password_hash(long_pw)
        # Verifying with the same first 72 chars should still succeed.
        assert verify_password("x" * 72, hashed) is True

    def test_create_access_token_default_expiry(self):
        from jose import jwt
        from app.core.config import settings
        from app.core.security import create_access_token

        token = create_access_token("user-123")
        decoded = jwt.decode(token, settings.SECRET_KEY, algorithms=["HS256"])
        assert decoded["sub"] == "user-123"
        assert "exp" in decoded

    def test_create_access_token_custom_expiry_and_claims(self):
        from jose import jwt
        from app.core.config import settings
        from app.core.security import create_access_token

        token = create_access_token(
            "user-456",
            expires_delta=timedelta(minutes=5),
            additional_claims={"tenant_id": "abc", "role": "admin"},
        )
        decoded = jwt.decode(token, settings.SECRET_KEY, algorithms=["HS256"])
        assert decoded["sub"] == "user-456"
        assert decoded["tenant_id"] == "abc"
        assert decoded["role"] == "admin"


# ── app/core/config.py — strip-strings validator ────────────────────────────

class TestConfigValidator:
    def test_strip_strings_trims_whitespace(self):
        from app.core.config import Settings
        # Use the validator directly. It's classmethod-decorated; calling it
        # without an instance is supported because mode="before".
        # The decorator wraps it; we exercise via Settings construction.
        s = Settings(
            SECRET_KEY="  abc  ",
            MCP_API_KEY="key\n",
            API_INTERNAL_KEY="\tinternal ",
        )
        assert s.SECRET_KEY == "abc"
        assert s.MCP_API_KEY == "key"
        assert s.API_INTERNAL_KEY == "internal"

    def test_strip_strings_passthrough_for_non_strings(self):
        from app.core.config import Settings
        s = Settings(
            SECRET_KEY="x",
            MCP_API_KEY="x",
            API_INTERNAL_KEY="x",
            ACCESS_TOKEN_EXPIRE_MINUTES=15,
            DEFAULT_WORKFLOW_TIMEOUT_SECONDS=120,
        )
        assert s.ACCESS_TOKEN_EXPIRE_MINUTES == 15
        assert s.DEFAULT_WORKFLOW_TIMEOUT_SECONDS == 120


# ── app/services/safety_policies — pure helpers ─────────────────────────────

class TestSafetyPoliciesPure:
    def test_classify_mcp_tool_special_case(self):
        from app.services.safety_policies import _classify_mcp_tool
        from app.schemas.safety_policy import RiskClass, RiskLevel, SideEffectLevel

        prof = _classify_mcp_tool("execute_shell", "shell")
        assert prof.risk_class == RiskClass.EXECUTION_CONTROL
        assert prof.risk_level == RiskLevel.CRITICAL
        assert prof.side_effect_level == SideEffectLevel.CODE_EXECUTION

    def test_classify_mcp_tool_read_prefixes(self):
        from app.services.safety_policies import _classify_mcp_tool
        from app.schemas.safety_policy import RiskClass, RiskLevel

        for name in (
            "search_knowledge",
            "list_agents",
            "get_thing",
            "read_doc",
            "find_lead",
        ):
            prof = _classify_mcp_tool(name, "default")
            assert prof.risk_class == RiskClass.READ_ONLY, name
            assert prof.risk_level == RiskLevel.LOW

    def test_classify_mcp_tool_internal_mutation_prefixes(self):
        from app.services.safety_policies import _classify_mcp_tool
        from app.schemas.safety_policy import RiskClass

        for name in ("create_thing", "update_thing", "record_event", "merge_x"):
            assert _classify_mcp_tool(name, "default").risk_class == \
                RiskClass.INTERNAL_MUTATION

    def test_classify_mcp_tool_external_write_prefixes(self):
        from app.services.safety_policies import _classify_mcp_tool
        from app.schemas.safety_policy import RiskClass

        for name in ("send_thing", "register_x", "delete_y", "run_z"):
            assert _classify_mcp_tool(name, "default").risk_class == \
                RiskClass.EXTERNAL_WRITE

    def test_classify_mcp_tool_unknown_falls_to_orchestration(self):
        from app.services.safety_policies import _classify_mcp_tool
        from app.schemas.safety_policy import RiskClass

        prof = _classify_mcp_tool("flapdoodle_thing", "weird")
        assert prof.risk_class == RiskClass.ORCHESTRATION_CONTROL

    def test_classify_execute_prefix_is_critical(self):
        from app.services.safety_policies import _classify_mcp_tool
        from app.schemas.safety_policy import RiskClass, RiskLevel

        prof = _classify_mcp_tool("execute_remote_thing", "shell")
        assert prof.risk_class == RiskClass.EXECUTION_CONTROL
        assert prof.risk_level == RiskLevel.CRITICAL

    def test_normalize_channel(self):
        from app.services.safety_policies import _normalize_channel
        assert _normalize_channel(None) == "web"
        assert _normalize_channel("") == "web"
        assert _normalize_channel("  WEB ") == "web"
        assert _normalize_channel("Workflow") == "workflow"

    def test_default_decision_local_agent_low_risk_allowed(self):
        from app.services.safety_policies import (
            _default_decision_for,
            ActionRiskProfile,
        )
        from app.schemas.safety_policy import (
            ActionType, PolicyDecision, RiskClass, RiskLevel,
            SideEffectLevel, Reversibility,
        )
        prof = ActionRiskProfile(
            action_type=ActionType.MCP_TOOL,
            action_name="search_x",
            category="default",
            risk_class=RiskClass.READ_ONLY,
            risk_level=RiskLevel.LOW,
            side_effect_level=SideEffectLevel.NONE,
            reversibility=Reversibility.REVERSIBLE,
        )
        decision, reason = _default_decision_for(prof, "local_agent")
        assert decision == PolicyDecision.ALLOW_WITH_LOGGING
        assert "Local" in reason

    def test_default_decision_local_agent_blocks_mutations(self):
        from app.services.safety_policies import (
            _default_decision_for,
            ActionRiskProfile,
        )
        from app.schemas.safety_policy import (
            ActionType, PolicyDecision, RiskClass, RiskLevel,
            SideEffectLevel, Reversibility,
        )
        prof = ActionRiskProfile(
            action_type=ActionType.MCP_TOOL,
            action_name="create_thing",
            category="default",
            risk_class=RiskClass.INTERNAL_MUTATION,
            risk_level=RiskLevel.MEDIUM,
            side_effect_level=SideEffectLevel.INTERNAL_STATE,
            reversibility=Reversibility.PARTIAL,
        )
        decision, _ = _default_decision_for(prof, "local_agent")
        assert decision == PolicyDecision.BLOCK

    def test_default_decision_code_execution_workflow_blocked(self):
        from app.services.safety_policies import (
            _default_decision_for,
            ActionRiskProfile,
        )
        from app.schemas.safety_policy import (
            ActionType, PolicyDecision, RiskClass, RiskLevel,
            SideEffectLevel, Reversibility,
        )
        prof = ActionRiskProfile(
            action_type=ActionType.MCP_TOOL,
            action_name="execute_shell",
            category="shell",
            risk_class=RiskClass.EXECUTION_CONTROL,
            risk_level=RiskLevel.CRITICAL,
            side_effect_level=SideEffectLevel.CODE_EXECUTION,
            reversibility=Reversibility.UNKNOWN,
        )
        decision, _ = _default_decision_for(prof, "workflow")
        assert decision == PolicyDecision.BLOCK
        # On web, the default is REQUIRE_REVIEW instead.
        decision_web, _ = _default_decision_for(prof, "web")
        assert decision_web == PolicyDecision.REQUIRE_REVIEW

    def test_default_decision_external_write_paths(self):
        from app.services.safety_policies import (
            _default_decision_for,
            ActionRiskProfile,
        )
        from app.schemas.safety_policy import (
            ActionType, PolicyDecision, RiskClass, RiskLevel,
            SideEffectLevel, Reversibility,
        )
        prof = ActionRiskProfile(
            action_type=ActionType.MCP_TOOL,
            action_name="send_email",
            category="email",
            risk_class=RiskClass.EXTERNAL_WRITE,
            risk_level=RiskLevel.HIGH,
            side_effect_level=SideEffectLevel.EXTERNAL_WRITE,
            reversibility=Reversibility.PARTIAL,
        )
        wf, _ = _default_decision_for(prof, "workflow")
        assert wf == PolicyDecision.REQUIRE_REVIEW
        web, _ = _default_decision_for(prof, "web")
        assert web == PolicyDecision.REQUIRE_CONFIRMATION

    def test_default_decision_internal_state_paths(self):
        from app.services.safety_policies import (
            _default_decision_for,
            ActionRiskProfile,
        )
        from app.schemas.safety_policy import (
            ActionType, PolicyDecision, RiskClass, RiskLevel,
            SideEffectLevel, Reversibility,
        )
        prof = ActionRiskProfile(
            action_type=ActionType.MCP_TOOL,
            action_name="create_entity",
            category="knowledge",
            risk_class=RiskClass.INTERNAL_MUTATION,
            risk_level=RiskLevel.MEDIUM,
            side_effect_level=SideEffectLevel.INTERNAL_STATE,
            reversibility=Reversibility.PARTIAL,
        )
        wf, _ = _default_decision_for(prof, "webhook")
        assert wf == PolicyDecision.REQUIRE_REVIEW
        web, _ = _default_decision_for(prof, "web")
        assert web == PolicyDecision.REQUIRE_CONFIRMATION

    def test_default_decision_low_risk_read_allowed(self):
        from app.services.safety_policies import (
            _default_decision_for,
            ActionRiskProfile,
        )
        from app.schemas.safety_policy import (
            ActionType, PolicyDecision, RiskClass, RiskLevel,
            SideEffectLevel, Reversibility,
        )
        prof = ActionRiskProfile(
            action_type=ActionType.MCP_TOOL,
            action_name="search_thing",
            category="default",
            risk_class=RiskClass.READ_ONLY,
            risk_level=RiskLevel.LOW,
            side_effect_level=SideEffectLevel.NONE,
            reversibility=Reversibility.REVERSIBLE,
        )
        decision, _ = _default_decision_for(prof, "web")
        assert decision == PolicyDecision.ALLOW_WITH_LOGGING

    def test_action_key_format(self):
        from app.services.safety_policies import _action_key
        from app.schemas.safety_policy import ActionType
        assert _action_key(ActionType.MCP_TOOL, "search_x") == "mcp_tool:search_x"

    def test_get_profile_returns_workflow_profile(self):
        from app.services.safety_policies import _get_profile
        from app.schemas.safety_policy import ActionType, RiskClass

        prof = _get_profile(ActionType.WORKFLOW_ACTION, "agent")
        assert prof.risk_class == RiskClass.ORCHESTRATION_CONTROL

    def test_get_profile_unknown_workflow_falls_back_to_dynamic(self):
        from app.services.safety_policies import _get_profile
        from app.schemas.safety_policy import ActionType, RiskClass

        prof = _get_profile(ActionType.WORKFLOW_ACTION, "totally_made_up")
        assert prof.risk_class == RiskClass.ORCHESTRATION_CONTROL
        assert prof.category == "dynamic"

    def test_get_profile_unknown_mcp_falls_back_to_classifier(self):
        from app.services.safety_policies import _get_profile
        from app.schemas.safety_policy import ActionType, RiskClass

        # Unknown but matches a read prefix → READ_ONLY
        prof = _get_profile(ActionType.MCP_TOOL, "search_unicorn")
        assert prof.risk_class == RiskClass.READ_ONLY

    def test_get_profile_unknown_unsupported_action_type_raises(self):
        """Action types other than MCP_TOOL or WORKFLOW_ACTION must raise."""
        from app.services.safety_policies import _get_profile
        from app.schemas.safety_policy import ActionType

        # Find an action type that's not MCP_TOOL or WORKFLOW_ACTION.
        other = next(
            (t for t in ActionType
             if t not in (ActionType.MCP_TOOL, ActionType.WORKFLOW_ACTION)),
            None,
        )
        if other is None:
            pytest.skip("No third ActionType — nothing to test")
        with pytest.raises(ValueError):
            _get_profile(other, "no_such_thing")

    def test_validate_override_ceiling_low_risk_returns_none(self):
        from app.services.safety_policies import (
            _validate_override_ceiling, ActionRiskProfile,
        )
        from app.schemas.safety_policy import (
            ActionType, PolicyDecision, RiskClass, RiskLevel,
            SideEffectLevel, Reversibility,
        )

        prof = ActionRiskProfile(
            action_type=ActionType.MCP_TOOL,
            action_name="search_x",
            category="default",
            risk_class=RiskClass.READ_ONLY,
            risk_level=RiskLevel.LOW,
            side_effect_level=SideEffectLevel.NONE,
            reversibility=Reversibility.REVERSIBLE,
        )
        # Even the most permissive override is fine for low-risk actions.
        _validate_override_ceiling(prof, "web", PolicyDecision.ALLOW)

    def test_validate_override_ceiling_blocks_relaxing_high_risk(self):
        from app.services.safety_policies import (
            _validate_override_ceiling, ActionRiskProfile,
        )
        from app.schemas.safety_policy import (
            ActionType, PolicyDecision, RiskClass, RiskLevel,
            SideEffectLevel, Reversibility,
        )

        prof = ActionRiskProfile(
            action_type=ActionType.MCP_TOOL,
            action_name="send_email",
            category="email",
            risk_class=RiskClass.EXTERNAL_WRITE,
            risk_level=RiskLevel.HIGH,
            side_effect_level=SideEffectLevel.EXTERNAL_WRITE,
            reversibility=Reversibility.PARTIAL,
        )
        # Default for HIGH/EXTERNAL_WRITE on web is REQUIRE_CONFIRMATION.
        # Asking for ALLOW would relax it → must raise.
        with pytest.raises(ValueError, match="cannot relax"):
            _validate_override_ceiling(prof, "web", PolicyDecision.ALLOW)

    def test_validate_override_ceiling_allows_tightening(self):
        from app.services.safety_policies import (
            _validate_override_ceiling, ActionRiskProfile,
        )
        from app.schemas.safety_policy import (
            ActionType, PolicyDecision, RiskClass, RiskLevel,
            SideEffectLevel, Reversibility,
        )

        prof = ActionRiskProfile(
            action_type=ActionType.MCP_TOOL,
            action_name="send_email",
            category="email",
            risk_class=RiskClass.EXTERNAL_WRITE,
            risk_level=RiskLevel.HIGH,
            side_effect_level=SideEffectLevel.EXTERNAL_WRITE,
            reversibility=Reversibility.PARTIAL,
        )
        # BLOCK is the strictest — must succeed for any high-risk action.
        _validate_override_ceiling(prof, "web", PolicyDecision.BLOCK)

    def test_validate_override_ceiling_all_channels(self):
        from app.services.safety_policies import (
            _validate_override_ceiling, ActionRiskProfile, ALL_CHANNEL,
        )
        from app.schemas.safety_policy import (
            ActionType, PolicyDecision, RiskClass, RiskLevel,
            SideEffectLevel, Reversibility,
        )

        prof = ActionRiskProfile(
            action_type=ActionType.MCP_TOOL,
            action_name="execute_shell",
            category="shell",
            risk_class=RiskClass.EXECUTION_CONTROL,
            risk_level=RiskLevel.CRITICAL,
            side_effect_level=SideEffectLevel.CODE_EXECUTION,
            reversibility=Reversibility.UNKNOWN,
        )
        # ALL_CHANNEL expands across every known channel, and BLOCK on
        # workflow / webhook is the default — REQUIRE_REVIEW would relax
        # those. The validator raises on the first violating channel.
        with pytest.raises(ValueError):
            _validate_override_ceiling(
                prof, ALL_CHANNEL, PolicyDecision.REQUIRE_REVIEW,
            )

    def test_discover_mcp_profiles_returns_dict_of_profiles(self):
        """Sanity: the lru-cached discovery returns at least one profile."""
        from app.services.safety_policies import (
            _discover_mcp_profiles, ActionRiskProfile,
        )
        profiles = _discover_mcp_profiles()
        assert isinstance(profiles, dict)
        assert len(profiles) > 0
        for v in profiles.values():
            assert isinstance(v, ActionRiskProfile)


# ── app/services/safety_enforcement — pure helpers ──────────────────────────

class TestSafetyEnforcementPure:
    def _make_result(self, decision_value="allow", risk="low"):
        from app.schemas.safety_policy import (
            ActionType, PolicyDecision, RiskClass, RiskLevel,
            SideEffectLevel, Reversibility,
        )
        from app.services.safety_enforcement import SafetyEnforcementResult

        return SafetyEnforcementResult(
            action_key="mcp_tool:foo",
            action_type=ActionType.MCP_TOOL,
            action_name="foo",
            category="default",
            channel="web",
            risk_class=RiskClass.READ_ONLY,
            risk_level=RiskLevel(risk),
            side_effect_level=SideEffectLevel.NONE,
            reversibility=Reversibility.REVERSIBLE,
            default_decision=PolicyDecision(decision_value),
            decision=PolicyDecision(decision_value),
            decision_source="default",
            rationale="initial",
            evidence_required=False,
            evidence_sufficient=False,
        )

    def test_normalize_items_drops_falsy(self):
        from app.services.safety_enforcement import _normalize_items
        assert _normalize_items(None) == []
        assert _normalize_items([]) == []
        assert _normalize_items(
            ["a", "", None, [], {}, "b", 0]
        ) == ["a", "b", 0]

    def test_evidence_required_for_blocking_decisions(self):
        from app.schemas.safety_policy import PolicyDecision
        from app.services.safety_enforcement import _evidence_required

        r = self._make_result(decision_value="require_confirmation")
        assert _evidence_required(r) is True
        r.decision = PolicyDecision.REQUIRE_REVIEW
        assert _evidence_required(r) is True
        r.decision = PolicyDecision.BLOCK
        assert _evidence_required(r) is True

    def test_evidence_required_for_high_or_critical_risk(self):
        from app.schemas.safety_policy import RiskLevel
        from app.services.safety_enforcement import _evidence_required

        r = self._make_result(risk="high")
        assert _evidence_required(r) is True
        r.risk_level = RiskLevel.CRITICAL
        assert _evidence_required(r) is True

    def test_evidence_required_false_for_safe_path(self):
        from app.services.safety_enforcement import _evidence_required
        r = self._make_result()  # allow / low — both excluded
        assert _evidence_required(r) is False

    def test_evidence_sufficient_full_request(self):
        from app.services.safety_enforcement import (
            _evidence_sufficient, SafetyEnforcementRequest,
        )
        from app.schemas.safety_policy import ActionType

        req = SafetyEnforcementRequest(
            action_type=ActionType.MCP_TOOL,
            action_name="x",
            channel="web",
            world_state_facts=["fact 1"],
            proposed_action={"action": "do thing"},
            expected_downside="email goes out",
        )
        assert _evidence_sufficient(req) is True

    def test_evidence_sufficient_missing_proposed_action(self):
        from app.services.safety_enforcement import (
            _evidence_sufficient, SafetyEnforcementRequest,
        )
        from app.schemas.safety_policy import ActionType

        req = SafetyEnforcementRequest(
            action_type=ActionType.MCP_TOOL,
            action_name="x",
            channel="web",
            world_state_facts=["fact 1"],
            expected_downside="email goes out",
        )
        assert _evidence_sufficient(req) is False

    def test_evidence_sufficient_no_context(self):
        from app.services.safety_enforcement import (
            _evidence_sufficient, SafetyEnforcementRequest,
        )
        from app.schemas.safety_policy import ActionType

        req = SafetyEnforcementRequest(
            action_type=ActionType.MCP_TOOL,
            action_name="x",
            channel="web",
            proposed_action={"action": "do thing"},
            expected_downside="email goes out",
        )
        assert _evidence_sufficient(req) is False

    def test_resolve_automated_channel_lifts_confirmation_to_review(self):
        from app.schemas.safety_policy import PolicyDecision
        from app.services.safety_enforcement import (
            _resolve_automated_channel_decision, AUTOMATED_CHANNELS,
        )

        r = self._make_result(decision_value="require_confirmation")
        # Pick any automated channel that exists
        auto_channel = next(iter(AUTOMATED_CHANNELS))
        out = _resolve_automated_channel_decision(r, auto_channel)
        assert out.decision == PolicyDecision.REQUIRE_REVIEW
        assert auto_channel in out.rationale

    def test_resolve_automated_channel_no_op_for_web(self):
        from app.schemas.safety_policy import PolicyDecision
        from app.services.safety_enforcement import (
            _resolve_automated_channel_decision,
        )

        r = self._make_result(decision_value="require_confirmation")
        before_decision = r.decision
        out = _resolve_automated_channel_decision(r, "web")
        assert out.decision == before_decision

    def test_escalate_decision_lifts_only_when_strictly_higher(self):
        from app.schemas.safety_policy import PolicyDecision
        from app.services.safety_enforcement import _escalate_decision

        r = self._make_result(decision_value="allow")
        # Lifting to REQUIRE_CONFIRMATION should succeed
        out = _escalate_decision(r, PolicyDecision.REQUIRE_CONFIRMATION, "needs review")
        assert out.decision == PolicyDecision.REQUIRE_CONFIRMATION
        assert "needs review" in out.rationale

    def test_escalate_decision_no_op_when_lower(self):
        from app.schemas.safety_policy import PolicyDecision
        from app.services.safety_enforcement import _escalate_decision

        r = self._make_result(decision_value="block")
        # Tries to "escalate" to a less severe decision — no-op
        out = _escalate_decision(r, PolicyDecision.ALLOW, "irrelevant")
        assert out.decision == PolicyDecision.BLOCK

    def test_agent_declared_floor_supervised(self):
        from app.services.safety_enforcement import _agent_declared_floor
        from app.schemas.safety_policy import AutonomyTier

        agent = MagicMock()
        agent.name = "Cardio Bot"
        agent.autonomy_level = "supervised"
        db = MagicMock()
        db.query.return_value.filter.return_value.all.return_value = [agent]
        out = _agent_declared_floor(db, uuid.uuid4(), "cardio_bot")
        assert out == AutonomyTier.SUPERVISED_EXECUTION

    def test_agent_declared_floor_full(self):
        from app.services.safety_enforcement import _agent_declared_floor
        from app.schemas.safety_policy import AutonomyTier

        agent = MagicMock()
        agent.name = "Luna"
        agent.autonomy_level = "full"
        db = MagicMock()
        db.query.return_value.filter.return_value.all.return_value = [agent]
        out = _agent_declared_floor(db, uuid.uuid4(), "luna")
        assert out == AutonomyTier.BOUNDED_AUTONOMOUS_EXECUTION

    def test_agent_declared_floor_unknown_returns_none(self):
        from app.services.safety_enforcement import _agent_declared_floor
        agent = MagicMock()
        agent.name = "Other"
        agent.autonomy_level = "supervised"
        db = MagicMock()
        db.query.return_value.filter.return_value.all.return_value = [agent]
        # No matching slug
        assert _agent_declared_floor(db, uuid.uuid4(), "luna") is None

    def test_agent_declared_floor_db_error(self):
        from app.services.safety_enforcement import _agent_declared_floor
        db = MagicMock()
        db.query.side_effect = RuntimeError("oops")
        # Must not raise — returns None.
        assert _agent_declared_floor(db, uuid.uuid4(), "luna") is None


# ── app/memory/validation_metrics ───────────────────────────────────────────

class TestValidationMetrics:
    def test_initial_report(self):
        from app.memory.validation_metrics import ValidationMetrics
        m = ValidationMetrics()
        rep = m.report()
        assert rep["reads"]["total"] == 0
        assert rep["reads"]["match_rate"] is None
        assert rep["writes"]["total"] == 0
        assert rep["writes"]["success_rate"] is None
        assert "started_at" in rep

    def test_record_read_match(self):
        from app.memory.validation_metrics import ValidationMetrics
        m = ValidationMetrics()
        m.record_read(matched=True)
        m.record_read(matched=True)
        m.record_read(matched=False)
        rep = m.report()
        assert rep["reads"]["total"] == 3
        assert rep["reads"]["matching"] == 2
        assert rep["reads"]["divergent"] == 1
        assert rep["reads"]["match_rate"] == pytest.approx(2 / 3)

    def test_record_write(self):
        from app.memory.validation_metrics import ValidationMetrics
        m = ValidationMetrics()
        m.record_write(success=True)
        m.record_write(success=False)
        m.record_write(success=True)
        rep = m.report()
        assert rep["writes"]["total"] == 3
        assert rep["writes"]["successful"] == 2
        assert rep["writes"]["failed"] == 1
        assert rep["writes"]["success_rate"] == pytest.approx(2 / 3)


# ── app/memory/adapters/registry ────────────────────────────────────────────

class TestAdapterRegistry:
    def test_register_and_get(self):
        from app.memory.adapters import registry as reg
        snap = reg.snapshot_registry()
        try:
            adapter = MagicMock()
            adapter.source_type = "test_source"
            reg.register_adapter(adapter)
            assert reg.get_adapter("test_source") is adapter
            assert "test_source" in reg.list_source_types()
        finally:
            reg.restore_registry(snap)

    def test_register_empty_source_type_raises(self):
        from app.memory.adapters import registry as reg
        adapter = MagicMock()
        adapter.source_type = ""
        with pytest.raises(ValueError):
            reg.register_adapter(adapter)

    def test_get_unknown_raises_key_error(self):
        from app.memory.adapters import registry as reg
        with pytest.raises(KeyError):
            reg.get_adapter("__never_registered__")

    def test_unregister_is_idempotent(self):
        from app.memory.adapters import registry as reg
        # Unregistering an unknown source must NOT raise.
        reg.unregister_adapter("__never_registered__")

    def test_snapshot_restore(self):
        from app.memory.adapters import registry as reg
        snap = reg.snapshot_registry()
        try:
            adapter = MagicMock()
            adapter.source_type = "snap_test"
            reg.register_adapter(adapter)
            assert "snap_test" in reg.list_source_types()
        finally:
            reg.restore_registry(snap)
        # Restored to pre-test state
        assert "snap_test" not in reg.list_source_types()


# ── app/memory/dispatch — fire-and-forget Temporal call ─────────────────────

class TestPostChatMemoryDispatch:
    def test_dispatch_starts_background_thread(self, monkeypatch):
        """The dispatch helper must spawn a daemon thread; it should NOT
        block on Temporal connection or raise on its absence."""
        import threading
        from app.memory import dispatch as d

        captured = {}
        original_thread = threading.Thread

        def fake_thread(*args, **kwargs):
            t = original_thread(*args, **kwargs)
            captured["thread"] = t
            return t

        monkeypatch.setattr(d.threading, "Thread", fake_thread)

        d.dispatch_post_chat_memory(
            tenant_id=uuid.uuid4(),
            session_id=uuid.uuid4(),
            user_message_id=uuid.uuid4(),
            assistant_message_id=uuid.uuid4(),
        )
        assert "thread" in captured
        assert captured["thread"].daemon is True
        # Wait briefly for the background coroutine to fail-and-log without
        # affecting the test outcome (Temporal is intentionally absent).
        captured["thread"].join(timeout=1.5)


# ── app/services/media_utils ────────────────────────────────────────────────

class TestMediaUtils:
    def test_classify_image(self):
        from app.services.media_utils import classify_media
        for mime in ("image/jpeg", "image/png", "IMAGE/PNG", "image/heic"):
            assert classify_media(mime) == "image"

    def test_classify_audio_known_and_generic(self):
        from app.services.media_utils import classify_media
        assert classify_media("audio/ogg") == "audio"
        # Generic audio/* prefix path
        assert classify_media("audio/x-mythical") == "audio"
        # MIME type with codec parameter
        assert classify_media("audio/ogg; codecs=opus") == "audio"

    def test_classify_pdf(self):
        from app.services.media_utils import classify_media
        assert classify_media("application/pdf") == "pdf"

    def test_classify_spreadsheet(self):
        from app.services.media_utils import classify_media
        assert classify_media("text/csv") == "spreadsheet"
        assert classify_media(
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        ) == "spreadsheet"

    def test_classify_unknown(self):
        from app.services.media_utils import classify_media
        assert classify_media("video/mp4") == "unsupported"
        assert classify_media("text/plain") == "unsupported"


# ── app/services/commitment_extractor — disabled module stubs ───────────────

class TestCommitmentExtractorStubs:
    def test_extract_returns_empty(self):
        from app.services.commitment_extractor import (
            extract_commitments_from_response,
        )
        # The function takes a Session but never touches it; pass a mock.
        out = extract_commitments_from_response(
            db=MagicMock(), tenant_id=uuid.uuid4(), response_text="anything"
        )
        assert out == []

    def test_build_stakes_context_empty(self):
        from app.services.commitment_extractor import build_stakes_context
        assert build_stakes_context(MagicMock(), uuid.uuid4()) == ""

    def test_get_commitment_stats_no_rows(self):
        from app.services.commitment_extractor import get_commitment_stats
        db = MagicMock()
        db.query.return_value.filter.return_value.all.return_value = []
        assert get_commitment_stats(db, uuid.uuid4(), days=30) == {}

    def test_get_commitment_stats_with_rows(self):
        from app.services.commitment_extractor import get_commitment_stats

        rows = [
            MagicMock(state="fulfilled"),
            MagicMock(state="fulfilled"),
            MagicMock(state="open"),
            MagicMock(state="broken"),
        ]
        db = MagicMock()
        db.query.return_value.filter.return_value.all.return_value = rows
        stats = get_commitment_stats(db, uuid.uuid4(), days=7)
        assert stats["total"] == 4
        assert stats["fulfilled"] == 2
        assert stats["broken"] == 1
        assert stats["open"] == 1
        assert stats["fulfillment_rate"] == 0.5


# ── app/memory/feature_flag ─────────────────────────────────────────────────

class TestMemoryFeatureFlag:
    def _patch_settings(self, monkeypatch, *, v2: bool, allowlist: list[str]):
        from app.core import config as cfg
        monkeypatch.setattr(cfg.settings, "USE_MEMORY_V2", v2, raising=False)
        monkeypatch.setattr(
            cfg.settings,
            "USE_MEMORY_V2_TENANT_ALLOWLIST",
            allowlist,
            raising=False,
        )

    def test_disabled_globally(self, monkeypatch):
        from app.memory.feature_flag import is_v2_enabled
        self._patch_settings(monkeypatch, v2=False, allowlist=[])
        assert is_v2_enabled(uuid.uuid4()) is False

    def test_enabled_with_empty_allowlist_means_all(self, monkeypatch):
        from app.memory.feature_flag import is_v2_enabled
        self._patch_settings(monkeypatch, v2=True, allowlist=[])
        assert is_v2_enabled(uuid.uuid4()) is True

    def test_allowlist_gates_per_tenant(self, monkeypatch):
        from app.memory.feature_flag import is_v2_enabled
        tid = uuid.uuid4()
        self._patch_settings(monkeypatch, v2=True, allowlist=[str(tid)])
        assert is_v2_enabled(tid) is True
        assert is_v2_enabled(uuid.uuid4()) is False


# ── app/memory/types — dataclass contracts ──────────────────────────────────

class TestMemoryTypes:
    def test_recall_request_defaults(self):
        from app.memory.types import RecallRequest
        req = RecallRequest(
            tenant_id=uuid.uuid4(),
            agent_slug="luna",
            query="hello",
        )
        assert req.user_id is None
        assert req.chat_session_id is None
        assert req.top_k_per_type == 5
        assert req.total_token_budget == 8000
        assert req.source_filter is None

    def test_recall_request_explicit_fields(self):
        from app.memory.types import RecallRequest
        tid = uuid.uuid4()
        uid = uuid.uuid4()
        sid = uuid.uuid4()
        req = RecallRequest(
            tenant_id=tid,
            agent_slug="data_analyst",
            query="customer health",
            user_id=uid,
            chat_session_id=sid,
            top_k_per_type=10,
            total_token_budget=4000,
            source_filter=["entities", "observations"],
        )
        assert req.user_id == uid
        assert req.chat_session_id == sid
        assert req.top_k_per_type == 10
        assert req.source_filter == ["entities", "observations"]


# ── app/services/_agent_ordering ────────────────────────────────────────────

class TestAgentOrdering:
    def test_status_rank_is_callable_case_expression(self):
        # Sanity: the module-level CASE expression compiles to SQL.
        from sqlalchemy.dialects import postgresql
        from app.services._agent_ordering import agent_status_rank

        compiled = str(agent_status_rank.compile(
            dialect=postgresql.dialect(),
            compile_kwargs={"literal_binds": True},
        ))
        # It should mention each lifecycle status.
        for token in ("production", "staging", "draft", "deprecated"):
            assert token in compiled


# ── app/services/workflow_templates — metadata helpers ──────────────────────

class TestWorkflowTemplates:
    def test_native_templates_nonempty(self):
        from app.services.workflow_templates import NATIVE_TEMPLATES
        assert isinstance(NATIVE_TEMPLATES, list)
        assert len(NATIVE_TEMPLATES) > 0

    def test_native_templates_have_required_keys(self):
        from app.services.workflow_templates import NATIVE_TEMPLATES
        required = {"name", "description", "tier", "definition", "trigger_config"}
        for t in NATIVE_TEMPLATES:
            missing = required - set(t.keys())
            assert not missing, (
                f"Template '{t.get('name', '?')}' missing keys: {missing}"
            )

    def test_native_templates_definition_has_steps(self):
        from app.services.workflow_templates import NATIVE_TEMPLATES
        for t in NATIVE_TEMPLATES:
            steps = t["definition"].get("steps")
            assert isinstance(steps, list) and steps, (
                f"Template '{t['name']}' has no steps"
            )

    def test_native_templates_step_ids_are_unique_per_template(self):
        from app.services.workflow_templates import NATIVE_TEMPLATES
        for t in NATIVE_TEMPLATES:
            ids = [s.get("id") for s in t["definition"]["steps"] if s.get("id")]
            assert len(ids) == len(set(ids)), (
                f"Template '{t['name']}' has duplicate step IDs: {ids}"
            )


# ── app/services/agent_router — pure helpers ────────────────────────────────

class TestAgentRouterPure:
    def test_infer_task_type_general_default(self):
        from app.services.agent_router import _infer_task_type
        assert _infer_task_type("just saying hi") == "general"

    def test_infer_task_type_keyword_match(self):
        from app.services.agent_router import _infer_task_type, _TASK_TYPE_KEYWORDS
        # Pick the first keyword from the first non-general bucket.
        for task_type, keywords in _TASK_TYPE_KEYWORDS.items():
            if not keywords:
                continue
            kw = keywords[0]
            assert _infer_task_type(f"please {kw} something") == task_type
            break

    def test_looks_like_greeting_spanish_and_english(self):
        from app.services.agent_router import _looks_like_greeting
        assert _looks_like_greeting("hola") is True
        assert _looks_like_greeting("Hola, cómo estás") is True
        assert _looks_like_greeting("hi there") is True
        assert _looks_like_greeting("HEY!") is True
        assert _looks_like_greeting("buenos días") is True

    def test_looks_like_greeting_negatives(self):
        from app.services.agent_router import _looks_like_greeting
        assert _looks_like_greeting("") is False
        assert _looks_like_greeting("   ") is False
        # "holiday" must NOT match "hola" — boundary check
        assert _looks_like_greeting("holiday plans") is False
        assert _looks_like_greeting("can you do X") is False

    def test_greeting_template_intent_match(self):
        from app.services.agent_router import _greeting_template
        out = _greeting_template(
            {"name": "greeting or small talk"}, "hi", "luna"
        )
        assert out is not None
        assert "Luna" in out

    def test_greeting_template_keyword_fallback(self):
        from app.services.agent_router import _greeting_template
        # No intent (cold-start), but message keyword-matches.
        out = _greeting_template(None, "hola", "luna")
        assert out is not None
        assert "Luna" in out

    def test_greeting_template_spanish_response(self):
        from app.services.agent_router import _greeting_template
        out = _greeting_template(None, "buenas", "luna")
        assert out is not None
        assert out.startswith("¡Hola!")

    def test_greeting_template_english_response(self):
        from app.services.agent_router import _greeting_template
        out = _greeting_template(None, "hi", "luna")
        assert out is not None
        assert out.startswith("Hi!")

    def test_greeting_template_question_disqualifies(self):
        from app.services.agent_router import _greeting_template
        out = _greeting_template(
            {"name": "greeting or small talk"}, "hola, qué tal?", "luna"
        )
        assert out is None

    def test_greeting_template_long_message_disqualifies(self):
        from app.services.agent_router import _greeting_template
        long_msg = "hola " + "x" * 100
        out = _greeting_template(
            {"name": "greeting or small talk"}, long_msg, "luna"
        )
        assert out is None

    def test_greeting_template_intent_other_disqualifies(self):
        from app.services.agent_router import _greeting_template
        out = _greeting_template({"name": "task: bookkeeping"}, "hi", "luna")
        assert out is None

    def test_greeting_template_custom_agent_name(self):
        from app.services.agent_router import _greeting_template
        out = _greeting_template(None, "hi", "data_analyst")
        assert out is not None
        assert "Data Analyst" in out

    def test_should_use_local_path_pin_disables(self):
        from app.services.agent_router import _should_use_local_path
        assert _should_use_local_path(None, "hi", pin_to_cli=True) is False

    def test_should_use_local_path_intent_disables(self):
        from app.services.agent_router import _should_use_local_path
        assert _should_use_local_path(
            {"name": "task: x"}, "hi", pin_to_cli=False
        ) is False

    def test_should_use_local_path_short_message(self):
        from app.services.agent_router import _should_use_local_path
        assert _should_use_local_path(None, "hi", pin_to_cli=False) is True

    def test_should_use_local_path_long_message(self):
        from app.services.agent_router import _should_use_local_path
        assert _should_use_local_path(
            None, "x" * 200, pin_to_cli=False,
        ) is False

    def test_format_memory_for_local_empty(self):
        from app.services.agent_router import _format_memory_for_local
        assert _format_memory_for_local(None) == ""
        assert _format_memory_for_local({}) == ""
        assert _format_memory_for_local({"relevant_entities": []}) == ""

    def test_format_memory_for_local_with_entities(self):
        from app.services.agent_router import _format_memory_for_local
        ctx = {
            "relevant_entities": [
                {"name": "Acme", "category": "customer"},
                {"name": "Bob", "category": "contact"},
                {"name": "Q4 deal", "category": "opportunity"},
                {"name": "extra", "category": "ignored"},
            ]
        }
        out = _format_memory_for_local(ctx)
        assert "Acme" in out
        assert "Bob" in out
        # Only top 3 entities included
        assert "extra" not in out

    def test_build_routing_summary_basic(self):
        from app.services.agent_router import _build_routing_summary
        s = _build_routing_summary(
            served_by="claude_code",
            requested="claude_code",
            chain_length=1,
            fallback_reason=None,
        )
        assert s["served_by_platform"] == "claude_code"
        assert s["served_by"] == "Claude Code"
        assert s["chain_length"] == 1
        # No fallback fired -> no requested_platform key
        assert "requested_platform" not in s

    def test_build_routing_summary_fallback(self):
        from app.services.agent_router import _build_routing_summary
        s = _build_routing_summary(
            served_by="copilot_cli",
            requested="claude_code",
            chain_length=2,
            fallback_reason="quota",
        )
        assert s["served_by_platform"] == "copilot_cli"
        assert s["served_by"] == "GitHub Copilot CLI"
        assert s["requested_platform"] == "claude_code"
        assert s["requested"] == "Claude Code"
        assert s["fallback_reason"] == "quota"
        assert "rate limit" in s["fallback_explanation"]

    def test_build_routing_summary_chain_length_minimum_one(self):
        from app.services.agent_router import _build_routing_summary
        s = _build_routing_summary(
            served_by=None,
            requested=None,
            chain_length=0,
            fallback_reason=None,
        )
        # min chain_length is bumped to 1
        assert s["chain_length"] == 1
        assert s["served_by"] == "—"
        assert s["served_by_platform"] is None

    def test_build_routing_summary_unknown_fallback_reason(self):
        from app.services.agent_router import _build_routing_summary
        s = _build_routing_summary(
            served_by="copilot_cli",
            requested="claude_code",
            chain_length=2,
            fallback_reason=None,
        )
        # No reason supplied — default labels still flow through
        assert s["fallback_reason"] == "unknown"
        assert s["fallback_explanation"] == "fell back to the next available CLI"


# ── app/services/confidence_scorer ──────────────────────────────────────────

class TestConfidenceScorer:
    def test_default_neutral_score(self):
        from app.services.confidence_scorer import score_response_confidence
        # Plain text with no markers stays around the 0.65 default.
        s = score_response_confidence("The answer is 42.")
        assert 0.5 <= s <= 0.8

    def test_confident_phrases_boost(self):
        from app.services.confidence_scorer import score_response_confidence
        plain = score_response_confidence("Result is here.")
        confident = score_response_confidence(
            "Based on the records, I just checked and confirmed the value."
        )
        assert confident > plain

    def test_uncertain_phrases_penalty(self):
        from app.services.confidence_scorer import score_response_confidence
        plain = score_response_confidence("Result is here.")
        hedged = score_response_confidence(
            "I'm not sure, but I think it might be unclear. You may want to verify."
        )
        assert hedged < plain

    def test_uncertain_topic_penalty(self):
        from app.services.confidence_scorer import score_response_confidence
        s = score_response_confidence(
            "The stock price will go up.",
            question="What's the market doing?",
        )
        assert s < 0.65

    def test_short_response_to_long_question_penalty(self):
        from app.services.confidence_scorer import score_response_confidence
        long_q = "Could you give me an extremely detailed breakdown of " + "x" * 80
        short_a = "yes"
        s = score_response_confidence(short_a, question=long_q)
        # Should drop below default
        assert s < 0.65

    def test_score_clamped_to_unit_interval(self):
        from app.services.confidence_scorer import score_response_confidence
        # Stack many uncertain phrases — result must not go negative.
        text = " ".join([
            "I'm not sure about this.",
            "I think this might be unclear.",
            "It could probably be correct, but maybe not.",
            "I don't have any way to verify.",
            "You should check with someone else.",
        ])
        s = score_response_confidence(text)
        assert 0.0 <= s <= 1.0

    def test_build_uncertainty_instruction_high_confidence_empty(self):
        from app.services.confidence_scorer import build_uncertainty_instruction
        assert build_uncertainty_instruction(0.9) == ""
        assert build_uncertainty_instruction(0.55) == ""

    def test_build_uncertainty_instruction_low_confidence(self):
        from app.services.confidence_scorer import build_uncertainty_instruction
        text = build_uncertainty_instruction(0.2)
        assert text != ""
        assert "LOW" in text or "low" in text.lower()

    def test_build_uncertainty_instruction_medium_confidence(self):
        from app.services.confidence_scorer import build_uncertainty_instruction
        text = build_uncertainty_instruction(0.4)
        assert text != ""
        assert "moderately" in text.lower() or "hedging" in text.lower()

    def test_inject_uncertainty_context_skip_when_confident(self):
        from app.services.confidence_scorer import inject_uncertainty_context
        prompt = "You are Luna."
        out = inject_uncertainty_context(prompt, 0.9)
        assert out == prompt

    def test_inject_uncertainty_context_appends_when_low(self):
        from app.services.confidence_scorer import inject_uncertainty_context
        prompt = "You are Luna."
        out = inject_uncertainty_context(prompt, 0.2)
        assert out != prompt
        assert prompt in out
        assert "Confidence" in out


# ── app/services/tool_groups ────────────────────────────────────────────────

class TestToolGroups:
    def test_resolve_none_returns_none(self):
        from app.services.tool_groups import resolve_tool_names
        assert resolve_tool_names(None) is None

    def test_resolve_empty_list(self):
        from app.services.tool_groups import resolve_tool_names
        assert resolve_tool_names([]) == []

    def test_resolve_unknown_group_skipped(self):
        from app.services.tool_groups import resolve_tool_names
        assert resolve_tool_names(["definitely_not_a_real_group"]) == []

    def test_resolve_known_group(self):
        from app.services.tool_groups import resolve_tool_names, TOOL_GROUPS
        # Pick a group that's guaranteed to exist
        any_group = next(iter(TOOL_GROUPS))
        names = resolve_tool_names([any_group])
        assert names is not None and len(names) > 0
        # Result is sorted
        assert names == sorted(names)

    def test_resolve_multi_group_dedupes(self):
        from app.services.tool_groups import resolve_tool_names
        # email + bookings overlap on send_email/search_emails
        names = resolve_tool_names(["email", "bookings"])
        assert names is not None
        # No duplicates
        assert len(names) == len(set(names))

    def test_format_allowed_tools(self):
        from app.services.tool_groups import format_allowed_tools
        out = format_allowed_tools(["search_emails", "send_email"])
        assert out == "mcp__agentprovision__search_emails,mcp__agentprovision__send_email"

    def test_format_allowed_tools_empty(self):
        from app.services.tool_groups import format_allowed_tools
        assert format_allowed_tools([]) == ""


# ── app/services/scoring_rubrics ────────────────────────────────────────────

class TestScoringRubrics:
    def test_get_existing_rubric(self):
        from app.services.scoring_rubrics import get_rubric, RUBRICS
        # Pick whichever rubric ships in the registry.
        assert RUBRICS, "No rubrics registered — registry should be non-empty"
        rid = next(iter(RUBRICS))
        rubric = get_rubric(rid)
        assert rubric is not None
        assert "name" in rubric

    def test_get_unknown_rubric_returns_none(self):
        from app.services.scoring_rubrics import get_rubric
        assert get_rubric("__unknown_rubric__") is None

    def test_list_rubrics_returns_summary(self):
        from app.services.scoring_rubrics import list_rubrics
        listing = list_rubrics()
        assert isinstance(listing, dict)
        for rid, summary in listing.items():
            assert "name" in summary
            assert "description" in summary


# ── app/services/agent_importer — pure conversion logic ─────────────────────

class TestAgentImporter:
    def test_detect_format_copilot_studio_via_schema(self):
        from app.services.agent_importer import detect_format
        assert detect_format(
            {"schemaName": "Microsoft.CopilotStudio.Bot.v1"}
        ) == "copilot_studio"

    def test_detect_format_copilot_studio_via_kind(self):
        from app.services.agent_importer import detect_format
        assert detect_format({"kind": "copilot_studio"}) == "copilot_studio"

    def test_detect_format_copilot_studio_via_credentials(self):
        from app.services.agent_importer import detect_format
        assert detect_format(
            {"botId": "abc", "directLineSecret": "s"}
        ) == "copilot_studio"

    def test_detect_format_ai_foundry_via_kind(self):
        from app.services.agent_importer import detect_format
        assert detect_format({"kind": "ai_foundry"}) == "ai_foundry"

    def test_detect_format_ai_foundry_via_assistant_shape(self):
        from app.services.agent_importer import detect_format
        assert detect_format(
            {
                "model": "gpt-4o",
                "instructions": "Be helpful.",
                "tools": [],
                "id": "asst_123",
            }
        ) == "ai_foundry"

    def test_detect_format_crewai_array(self):
        from app.services.agent_importer import detect_format
        assert detect_format(
            {"agents": [{"role": "researcher", "goal": "find data"}]}
        ) == "crewai"

    def test_detect_format_crewai_single(self):
        from app.services.agent_importer import detect_format
        assert detect_format(
            {"role": "analyst", "goal": "explain"}
        ) == "crewai"

    def test_detect_format_langchain_agent_type(self):
        from app.services.agent_importer import detect_format
        assert detect_format({"agent_type": "react"}) == "langchain"

    def test_detect_format_langchain_underscore_type(self):
        from app.services.agent_importer import detect_format
        assert detect_format({"_type": "openai-agent"}) == "langchain"

    def test_detect_format_autogen(self):
        from app.services.agent_importer import detect_format
        assert detect_format(
            {"name": "AssistantBot", "system_message": "Be helpful"}
        ) == "autogen"

    def test_detect_format_unknown(self):
        from app.services.agent_importer import detect_format
        assert detect_format({}) == "unknown"
        assert detect_format("not a dict") == "unknown"

    def test_import_crewai_array(self):
        from app.services.agent_importer import import_crewai
        out = import_crewai(
            {
                "agents": [
                    {
                        "role": "Researcher",
                        "goal": "Discover insights",
                        "backstory": "A seasoned analyst.",
                        "tools": ["search", "summarise"],
                    }
                ]
            }
        )
        assert out["name"] == "Researcher"
        assert out["description"] == "Discover insights"
        assert "seasoned analyst" in out["persona_prompt"]
        assert out["capabilities"] == ["search", "summarise"]
        assert out["config"]["metadata"]["source"] == "crewai"

    def test_import_crewai_single(self):
        from app.services.agent_importer import import_crewai
        out = import_crewai(
            {"role": "Analyst", "goal": "Crunch numbers"}
        )
        assert out["name"] == "Analyst"

    def test_import_crewai_dict_tools_extract_name(self):
        from app.services.agent_importer import import_crewai
        out = import_crewai(
            {
                "role": "X",
                "goal": "Y",
                "tools": [{"name": "search"}, {"name": "summarise"}],
            }
        )
        assert out["capabilities"] == ["search", "summarise"]

    def test_import_langchain_basic(self):
        from app.services.agent_importer import import_langchain
        out = import_langchain(
            {
                "name": "LCAgent",
                "agent_type": "react",
                "tools": ["search", {"name": "calc"}],
            }
        )
        assert out["name"] == "LCAgent"
        assert "search" in out["capabilities"]
        assert "calc" in out["capabilities"]


# ── app/services/chat_import ────────────────────────────────────────────────

class TestChatImport:
    def test_parse_chatgpt_export_minimal(self):
        from app.services.chat_import import chat_import_service
        import json
        data = json.dumps([
            {
                "title": "My Chat",
                "id": "abc-123",
                "mapping": {
                    "n1": {"message": {
                        "author": {"role": "user"},
                        "content": {"parts": ["Hello"]},
                        "create_time": 1.0,
                    }},
                    "n2": {"message": {
                        "author": {"role": "assistant"},
                        "content": {"parts": ["Hi back"]},
                        "create_time": 2.0,
                    }},
                },
            }
        ]).encode()
        out = chat_import_service.parse_chatgpt_export(data)
        assert len(out) == 1
        s = out[0]
        assert s["title"] == "My Chat"
        assert s["external_id"] == "abc-123"
        assert s["source"] == "chatgpt_import"
        assert len(s["messages"]) == 2
        assert s["messages"][0]["role"] == "user"
        assert s["messages"][0]["content"] == "Hello"

    def test_parse_chatgpt_export_skips_system_and_empty(self):
        from app.services.chat_import import chat_import_service
        import json
        data = json.dumps([
            {
                "title": "T",
                "mapping": {
                    "n1": {"message": {
                        "author": {"role": "system"},
                        "content": {"parts": ["sys"]},
                        "create_time": 1.0,
                    }},
                    "n2": {"message": {
                        "author": {"role": "user"},
                        "content": {"parts": []},
                        "create_time": 2.0,
                    }},
                    "n3": {"message": {
                        "author": {"role": "user"},
                        "content": {"parts": ["real"]},
                        "create_time": 3.0,
                    }},
                },
            }
        ]).encode()
        out = chat_import_service.parse_chatgpt_export(data)
        assert len(out) == 1
        # Only the "real" message survives
        assert len(out[0]["messages"]) == 1
        assert out[0]["messages"][0]["content"] == "real"

    def test_parse_chatgpt_export_invalid_json_raises(self):
        from app.services.chat_import import chat_import_service
        with pytest.raises(ValueError):
            chat_import_service.parse_chatgpt_export(b"not json")

    def test_parse_claude_export_basic(self):
        from app.services.chat_import import chat_import_service
        import json
        data = json.dumps([
            {
                "name": "Claude Chat",
                "uuid": "uuid-1",
                "chat_messages": [
                    {"sender": "human", "text": "Hi"},
                    {"sender": "assistant", "text": "Hello"},
                    {"sender": "human", "text": ""},
                ],
            }
        ]).encode()
        out = chat_import_service.parse_claude_export(data)
        assert len(out) == 1
        s = out[0]
        assert s["title"] == "Claude Chat"
        assert s["external_id"] == "uuid-1"
        # Empty-text message excluded
        assert len(s["messages"]) == 2
        assert s["messages"][0]["role"] == "user"
        assert s["messages"][1]["role"] == "assistant"

    def test_parse_claude_export_invalid_json_raises(self):
        from app.services.chat_import import chat_import_service
        with pytest.raises(ValueError):
            chat_import_service.parse_claude_export(b"not json")


# ── app/core/logging ────────────────────────────────────────────────────────

class TestJsonLogging:
    def test_json_formatter_basic(self):
        import json as _json
        import logging as _logging
        from app.core.logging import JsonFormatter

        record = _logging.LogRecord(
            name="test",
            level=_logging.INFO,
            pathname=__file__,
            lineno=1,
            msg="hello %s",
            args=("world",),
            exc_info=None,
        )
        formatter = JsonFormatter()
        out = formatter.format(record)
        parsed = _json.loads(out)
        assert parsed["message"] == "hello world"
        assert parsed["level"] == "INFO"
        assert parsed["logger"] == "test"

    def test_json_formatter_with_extra_fields(self):
        import json as _json
        import logging as _logging
        from app.core.logging import JsonFormatter

        record = _logging.LogRecord(
            name="t", level=_logging.WARNING, pathname=__file__, lineno=2,
            msg="m", args=None, exc_info=None,
        )
        record.extra_fields = {"request_id": "abc", "tenant": "t1"}
        out = JsonFormatter().format(record)
        parsed = _json.loads(out)
        assert parsed["request_id"] == "abc"
        assert parsed["tenant"] == "t1"

    def test_json_formatter_with_exception(self):
        import json as _json
        import logging as _logging
        import sys as _sys
        from app.core.logging import JsonFormatter

        try:
            raise RuntimeError("boom")
        except RuntimeError:
            exc_info = _sys.exc_info()
        record = _logging.LogRecord(
            name="t", level=_logging.ERROR, pathname=__file__, lineno=3,
            msg="m", args=None, exc_info=exc_info,
        )
        out = JsonFormatter().format(record)
        parsed = _json.loads(out)
        assert "RuntimeError" in parsed.get("exception", "")

    def test_log_request_helper(self, caplog):
        import logging as _logging
        from app.core.logging import log_request

        caplog.set_level(_logging.INFO, logger="apptest")
        logger = _logging.getLogger("apptest")
        log_request(logger, "GET", "/api/health", 200, 12.3, extra={"actor": "system"})
        assert any("/api/health" in r.message for r in caplog.records)


# ── app/services/dynamic_workflows — validator + helpers ────────────────────

class TestDynamicWorkflowsValidator:
    def test_empty_definition_reports_error(self):
        from app.services.dynamic_workflows import validate_workflow_definition
        out = validate_workflow_definition({"steps": []})
        assert out["step_count"] == 0
        assert out["steps_planned"] == []
        assert any("no steps" in e.lower() for e in out["validation_errors"])

    def test_single_mcp_tool_step_resolves_integration(self):
        from app.services.dynamic_workflows import validate_workflow_definition
        out = validate_workflow_definition(
            {
                "steps": [
                    {
                        "id": "fetch",
                        "type": "mcp_tool",
                        "tool": "search_emails",
                        "params": {"query": "x"},
                        "output": "emails",
                    }
                ]
            }
        )
        assert out["validation_errors"] == []
        assert out["step_count"] == 1
        assert "gmail" in out["integrations_required"]

    def test_unknown_mcp_tool_reports_error(self):
        from app.services.dynamic_workflows import validate_workflow_definition
        out = validate_workflow_definition(
            {
                "steps": [
                    {"id": "x", "type": "mcp_tool", "tool": "fake_tool"}
                ]
            }
        )
        assert any("Unrecognized" in e for e in out["validation_errors"])

    def test_duplicate_step_ids_reported(self):
        from app.services.dynamic_workflows import validate_workflow_definition
        out = validate_workflow_definition(
            {
                "steps": [
                    {"id": "a", "type": "wait", "duration": "1s"},
                    {"id": "a", "type": "wait", "duration": "2s"},
                ]
            }
        )
        assert any("Duplicate" in e for e in out["validation_errors"])

    def test_template_var_references_valid_output(self):
        from app.services.dynamic_workflows import validate_workflow_definition
        out = validate_workflow_definition(
            {
                "steps": [
                    {
                        "id": "fetch",
                        "type": "mcp_tool",
                        "tool": "search_emails",
                        "output": "emails",
                    },
                    {
                        "id": "use",
                        "type": "transform",
                        "operation": "concat",
                        "input": "{{emails.body}}",
                    },
                ]
            }
        )
        # Only known outputs / "input" are valid sources, so no errors here
        assert all("Template variable" not in e for e in out["validation_errors"])

    def test_template_var_unknown_source_reports_error(self):
        from app.services.dynamic_workflows import validate_workflow_definition
        out = validate_workflow_definition(
            {
                "steps": [
                    {
                        "id": "x",
                        "type": "transform",
                        "operation": "noop",
                        "value": "{{nowhere.field}}",
                    }
                ]
            }
        )
        assert any("Template variable" in e for e in out["validation_errors"])

    def test_template_var_input_reference_allowed(self):
        from app.services.dynamic_workflows import validate_workflow_definition
        out = validate_workflow_definition(
            {
                "steps": [
                    {
                        "id": "x",
                        "type": "transform",
                        "operation": "echo",
                        "value": "{{input.foo}}",
                    }
                ]
            }
        )
        assert all("Template variable" not in e for e in out["validation_errors"])

    def test_nested_steps_collected(self):
        from app.services.dynamic_workflows import validate_workflow_definition
        out = validate_workflow_definition(
            {
                "steps": [
                    {
                        "id": "outer",
                        "type": "for_each",
                        "collection": "items",
                        "steps": [
                            {"id": "inner", "type": "wait", "duration": "1s"}
                        ],
                    }
                ]
            }
        )
        # Both steps tracked
        assert out["step_count"] == 2
        # `items` (no source) is itself a template-style placeholder; the
        # validator only inspects {{...}} references, so this doesn't trip.
        plans = " ".join(out["steps_planned"])
        assert "outer" in plans and "inner" in plans

    def test_step_descriptions_cover_each_type(self):
        from app.services.dynamic_workflows import _describe_step
        # Hit every branch of the if/elif tree
        assert "MCP tool" in _describe_step({"id": "a", "type": "mcp_tool", "tool": "t"})
        assert "delegate to agent" in _describe_step({"id": "b", "type": "agent", "agent": "luna"})
        assert "conditional" in _describe_step({"id": "c", "type": "condition"})
        assert "loop over" in _describe_step({"id": "d", "type": "for_each", "collection": "x"})
        assert "parallel" in _describe_step({"id": "e", "type": "parallel", "steps": [{}, {}]})
        assert "wait" in _describe_step({"id": "f", "type": "wait", "duration": "1s"})
        assert "transform" in _describe_step({"id": "g", "type": "transform", "operation": "concat"})
        assert "human approval" in _describe_step({"id": "h", "type": "human_approval"})
        assert "webhook" in _describe_step({"id": "i", "type": "webhook_trigger"})
        assert "sub-workflow" in _describe_step({"id": "j", "type": "workflow"})
        # Unknown type falls through
        assert "[k] custom_type" in _describe_step({"id": "k", "type": "custom_type"})

    def test_extract_template_refs_handles_nested_structures(self):
        from app.services.dynamic_workflows import _extract_template_refs
        obj = {
            "a": "{{first.x}}",
            "b": ["{{second.y}}", {"c": "{{third.z}}"}],
            "d": 42,
        }
        refs = _extract_template_refs(obj)
        assert refs == {"first", "second", "third"}

    def test_extract_template_refs_no_matches(self):
        from app.services.dynamic_workflows import _extract_template_refs
        assert _extract_template_refs("plain string") == set()
        assert _extract_template_refs({"x": 1, "y": [None, True]}) == set()


# ── app/services/cli_platform_resolver — error classifier + cooldown ────────

class TestCliPlatformResolverPure:
    def test_classify_error_none(self):
        from app.services.cli_platform_resolver import classify_error
        assert classify_error(None) is None
        assert classify_error("") is None

    def test_classify_error_quota_variants(self):
        from app.services.cli_platform_resolver import classify_error
        for msg in (
            "quota exceeded",
            "rate limit hit",
            "RATE_LIMIT",
            "insufficient quota",
            "out of credits",
            "Too many requests",
            "HTTP 429",
            "credit balance is 0",
        ):
            assert classify_error(msg) == "quota", msg

    def test_classify_error_auth_variants(self):
        from app.services.cli_platform_resolver import classify_error
        for msg in (
            "unauthorized",
            "invalid grant",
            "token expired",
            "HTTP 401",
            "Authentication failed",
        ):
            assert classify_error(msg) == "auth", msg

    def test_classify_error_missing_cred_long_form(self):
        from app.services.cli_platform_resolver import classify_error
        msg = "Claude Code subscription is not connected. Please connect."
        assert classify_error(msg) == "missing_credential"

    def test_classify_error_missing_cred_short_form(self):
        from app.services.cli_platform_resolver import classify_error
        for msg in (
            "Claude Code not connected",
            "Codex not connected",
            "Gemini CLI not connected",
            "GitHub Copilot CLI not connected",
        ):
            assert classify_error(msg) == "missing_credential", msg

    def test_classify_error_unknown_returns_none(self):
        from app.services.cli_platform_resolver import classify_error
        assert classify_error("the user typed something weird") is None
        assert classify_error("ImportError: numpy not found") is None

    def test_cooldown_seconds_default(self, monkeypatch):
        from app.services.cli_platform_resolver import _cooldown_seconds
        monkeypatch.delenv("CLI_COOLDOWN_SECONDS", raising=False)
        assert _cooldown_seconds() == 600

    def test_cooldown_seconds_override(self, monkeypatch):
        from app.services.cli_platform_resolver import _cooldown_seconds
        monkeypatch.setenv("CLI_COOLDOWN_SECONDS", "120")
        assert _cooldown_seconds() == 120

    def test_cooldown_seconds_invalid_falls_back(self, monkeypatch):
        from app.services.cli_platform_resolver import _cooldown_seconds
        monkeypatch.setenv("CLI_COOLDOWN_SECONDS", "not-a-number")
        assert _cooldown_seconds() == 600

    def test_cooldown_key_format(self):
        from app.services.cli_platform_resolver import _cooldown_key
        tid = uuid.uuid4()
        assert _cooldown_key(tid, "claude_code") == f"cli_cooldown:{tid}:claude_code"

    def test_is_in_cooldown_opencode_never_cooled(self):
        from app.services.cli_platform_resolver import is_in_cooldown
        # Local floor is the universal fallback — must never be cooled.
        assert is_in_cooldown(uuid.uuid4(), "opencode") is False

    def test_local_cooldown_dict_path(self, monkeypatch):
        """When Redis is unreachable, cooldown state lives in a process dict."""
        import time as _time
        from app.services import cli_platform_resolver as r

        # Force Redis path off
        monkeypatch.setattr(r, "_redis_singleton", None, raising=False)
        monkeypatch.setattr(r, "_redis_init_failed", True, raising=False)
        # Reset the in-process dict for a clean test
        r._local_cooldown.clear()

        tid = uuid.uuid4()
        assert r.is_in_cooldown(tid, "claude_code") is False
        # Forge an entry already expired
        r._local_cooldown[r._cooldown_key(tid, "claude_code")] = _time.time() - 10
        assert r.is_in_cooldown(tid, "claude_code") is False
        # Forge an entry far in the future
        r._local_cooldown[r._cooldown_key(tid, "claude_code")] = _time.time() + 120
        assert r.is_in_cooldown(tid, "claude_code") is True

    def test_mark_cooldown_skips_invalid_platforms(self, monkeypatch):
        from app.services import cli_platform_resolver as r

        monkeypatch.setattr(r, "_redis_singleton", None, raising=False)
        monkeypatch.setattr(r, "_redis_init_failed", True, raising=False)
        r._local_cooldown.clear()
        # opencode is excluded
        r.mark_cooldown(uuid.uuid4(), "opencode", reason="quota")
        # invented platform is excluded
        r.mark_cooldown(uuid.uuid4(), "no_such_platform", reason="quota")
        assert r._local_cooldown == {}

    def test_mark_cooldown_local_path(self, monkeypatch):
        from app.services import cli_platform_resolver as r

        monkeypatch.setattr(r, "_redis_singleton", None, raising=False)
        monkeypatch.setattr(r, "_redis_init_failed", True, raising=False)
        r._local_cooldown.clear()

        tid = uuid.uuid4()
        r.mark_cooldown(tid, "claude_code", reason="quota")
        assert r.is_in_cooldown(tid, "claude_code") is True


# ── app/services/agent_identity ─────────────────────────────────────────────

class TestAgentIdentity:
    def test_default_returns_luna(self):
        from app.services.agent_identity import resolve_primary_agent_slug
        db = MagicMock()
        db.query.return_value.filter.return_value.first.return_value = None
        assert resolve_primary_agent_slug(db, uuid.uuid4()) == "luna"

    def test_default_when_branding_says_ai_assistant(self):
        from app.services.agent_identity import resolve_primary_agent_slug
        db = MagicMock()
        branding = MagicMock()
        branding.ai_assistant_name = "AI Assistant"
        db.query.return_value.filter.return_value.first.return_value = branding
        assert resolve_primary_agent_slug(db, uuid.uuid4()) == "luna"

    def test_custom_agent_name_slugified(self):
        from app.services.agent_identity import resolve_primary_agent_slug
        db = MagicMock()
        branding = MagicMock()
        branding.ai_assistant_name = "Cardio Bot"
        db.query.return_value.filter.return_value.first.return_value = branding
        assert resolve_primary_agent_slug(db, uuid.uuid4()) == "cardio-bot"

    def test_db_exception_falls_back_to_luna(self):
        from app.services.agent_identity import resolve_primary_agent_slug
        db = MagicMock()
        db.query.side_effect = RuntimeError("oops")
        # Should not raise; should fall back to luna
        assert resolve_primary_agent_slug(db, uuid.uuid4()) == "luna"


# ── app/services/behavioral_signals — pure helpers ──────────────────────────

class TestBehavioralSignalsPure:
    def test_make_tag_short_text(self):
        from app.services.behavioral_signals import _make_tag
        assert _make_tag("hello") == "hello"

    def test_make_tag_takes_first_five_words(self):
        from app.services.behavioral_signals import _make_tag
        out = _make_tag("one two three four five six seven")
        assert out == "one two three four five"

    def test_make_tag_strips_punctuation(self):
        from app.services.behavioral_signals import _make_tag
        assert _make_tag("Hi there.") == "Hi there"
        assert _make_tag("Wait!") == "Wait"
        assert _make_tag("Really?") == "Really"

    def test_parse_suggestions_finds_follow_up(self):
        from app.services.behavioral_signals import _parse_suggestions
        text = "Want me to send an email to the customer with the updates?"
        results = _parse_suggestions(text)
        assert results, "Expected at least one suggestion match"
        # The first entry is (sentence, suggestion_type)
        sentence, stype = results[0]
        assert "send" in sentence.lower()
        assert stype in ("follow_up", "send_email")

    def test_parse_suggestions_dedupes(self):
        from app.services.behavioral_signals import _parse_suggestions
        text = "Want me to send an email? Want me to send an email?"
        results = _parse_suggestions(text)
        # Same sentence shouldn't appear twice
        assert len(results) == 1

    def test_parse_suggestions_skips_too_short(self):
        from app.services.behavioral_signals import _parse_suggestions
        # 14-char sentence is below the 15-char threshold
        assert _parse_suggestions("Send it. Yes.") == []

    def test_parse_suggestions_no_match_returns_empty(self):
        from app.services.behavioral_signals import _parse_suggestions
        text = "The weather today is sunny and bright with light wind."
        assert _parse_suggestions(text) == []

    def test_cosine_similarity_identical_vectors(self):
        from app.services.behavioral_signals import _cosine_similarity
        v = [0.1] * 10
        assert _cosine_similarity(v, v) == pytest.approx(1.0, rel=1e-3)

    def test_cosine_similarity_orthogonal(self):
        from app.services.behavioral_signals import _cosine_similarity
        a = [1.0, 0.0]
        b = [0.0, 1.0]
        assert _cosine_similarity(a, b) == pytest.approx(0.0, abs=1e-6)

    def test_cosine_similarity_zero_vector(self):
        from app.services.behavioral_signals import _cosine_similarity
        a = [0.0, 0.0]
        b = [1.0, 1.0]
        assert _cosine_similarity(a, b) == 0.0

    def test_cosine_similarity_invalid_input(self):
        from app.services.behavioral_signals import _cosine_similarity
        # Non-numeric input must not raise — falls back to 0.0
        assert _cosine_similarity("foo", "bar") == 0.0


# ── app/memory/visibility — query filter shape ──────────────────────────────

class TestRecallResponseToLegacyDict:
    """Translation of typed RecallResponse → legacy dict shape the
    CLI prompt-builder still consumes."""

    def _resp(self, **overrides):
        from types import SimpleNamespace
        defaults = dict(
            entities=[],
            observations=[],
            relations=[],
            episodes=[],
            commitments=[],
            goals=[],
            past_conversations=[],
        )
        defaults.update(overrides)
        return SimpleNamespace(**defaults)

    def test_empty_response_produces_empty_lists(self):
        from app.services.agent_router import _recall_response_to_legacy_dict
        out = _recall_response_to_legacy_dict(self._resp())
        assert out["recalled_entity_names"] == []
        assert out["relevant_entities"] == []
        assert out["relevant_relations"] == []
        assert out["recent_episodes"] == []
        assert out["commitments"] == []
        assert out["goals"] == []
        assert out["past_conversations"] == []

    def test_entity_translates_to_legacy_shape(self):
        from types import SimpleNamespace
        from app.services.agent_router import _recall_response_to_legacy_dict

        ent = SimpleNamespace(
            id="e1", name="Acme", category="customer",
            description="A buyer", similarity=0.92,
        )
        obs = SimpleNamespace(
            entity_id="e1", content="Bought 3 widgets", source_ref="invoice-123",
        )
        out = _recall_response_to_legacy_dict(self._resp(
            entities=[ent], observations=[obs],
        ))
        assert out["recalled_entity_names"] == ["Acme"]
        assert out["relevant_entities"][0]["name"] == "Acme"
        assert out["relevant_entities"][0]["type"] == "customer"
        assert out["relevant_entities"][0]["similarity"] == 0.92
        # Observation grouped under entity name
        assert "Acme" in out["entity_observations"]
        assert out["entity_observations"]["Acme"][0]["text"] == "Bought 3 widgets"
        assert out["entity_observations"]["Acme"][0]["source_ref"] == "invoice-123"

    def test_observation_for_unknown_entity_dropped(self):
        from types import SimpleNamespace
        from app.services.agent_router import _recall_response_to_legacy_dict

        obs = SimpleNamespace(entity_id="unknown", content="orphan")
        out = _recall_response_to_legacy_dict(self._resp(observations=[obs]))
        assert out["entity_observations"] == {}

    def test_entity_without_category_defaults_to_general(self):
        from types import SimpleNamespace
        from app.services.agent_router import _recall_response_to_legacy_dict

        ent = SimpleNamespace(
            id="e1", name="X", category=None, description=None, similarity=0.5,
        )
        out = _recall_response_to_legacy_dict(self._resp(entities=[ent]))
        assert out["relevant_entities"][0]["type"] == "general"

    def test_relations_translated(self):
        from types import SimpleNamespace
        from app.services.agent_router import _recall_response_to_legacy_dict

        rel = SimpleNamespace(
            from_entity="A", to_entity="B", relation_type="bought_from",
        )
        out = _recall_response_to_legacy_dict(self._resp(relations=[rel]))
        assert out["relevant_relations"][0] == {
            "from": "A", "to": "B", "type": "bought_from",
        }

    def test_episodes_format_date_when_present(self):
        from datetime import datetime
        from types import SimpleNamespace
        from app.services.agent_router import _recall_response_to_legacy_dict

        ep_with_date = SimpleNamespace(
            summary="Demo call", created_at=datetime(2026, 5, 3, 14, 30),
        )
        ep_without_date = SimpleNamespace(summary="Other", created_at=None)
        out = _recall_response_to_legacy_dict(
            self._resp(episodes=[ep_with_date, ep_without_date])
        )
        assert out["recent_episodes"][0]["date"] == "2026-05-03 14:30"
        assert out["recent_episodes"][1]["date"] == ""

    def test_commitments_format_due_date_when_present(self):
        from datetime import datetime
        from types import SimpleNamespace
        from app.services.agent_router import _recall_response_to_legacy_dict

        c_with = SimpleNamespace(
            title="Send report", state="open",
            due_at=datetime(2026, 5, 10, 9, 0), priority="high",
        )
        c_without = SimpleNamespace(
            title="Maybe later", state="open", due_at=None, priority="low",
        )
        out = _recall_response_to_legacy_dict(
            self._resp(commitments=[c_with, c_without])
        )
        assert out["commitments"][0]["due_at"] == "2026-05-10 09:00"
        assert out["commitments"][1]["due_at"] == "No deadline"


class TestMemoryVisibility:
    def test_apply_visibility_uses_real_model_columns(self):
        """Smoke test: applying the filter to a real SQLAlchemy query should
        produce a compiled WHERE clause that mentions all three branches."""
        from sqlalchemy import Column, String
        from sqlalchemy.dialects.postgresql import ARRAY
        from sqlalchemy.orm import declarative_base, Query
        from sqlalchemy import create_engine
        from sqlalchemy.orm import sessionmaker
        from app.memory.visibility import apply_visibility

        Base = declarative_base()

        class Doc(Base):  # noqa: D401
            __tablename__ = "_visibility_smoke"
            id = Column(String, primary_key=True)
            visibility = Column(String)
            owner_agent_slug = Column(String)
            visible_to = Column(ARRAY(String))

        engine = create_engine("sqlite:///:memory:")
        Session = sessionmaker(bind=engine)
        session = Session()
        try:
            base_q = session.query(Doc)
            filtered = apply_visibility(base_q, Doc, "luna")
            sql = str(filtered.statement.compile(compile_kwargs={"literal_binds": True}))
            assert "tenant_wide" in sql
            assert "agent_scoped" in sql
            assert "agent_group" in sql
            assert "luna" in sql
        finally:
            session.close()

