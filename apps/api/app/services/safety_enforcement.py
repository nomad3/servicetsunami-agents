"""Central safety enforcement and evidence-pack persistence."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional
import uuid

from sqlalchemy.orm import Session

from app.models.safety_policy import SafetyEvidencePack
from app.schemas.safety_policy import (
    AutonomyTier,
    ActionType,
    PolicyDecision,
    SafetyEnforcementRequest,
    SafetyEnforcementResult,
)
from app.services import safety_policies, safety_trust

AUTOMATED_CHANNELS = {"workflow", "webhook", "local_agent"}
EVIDENCE_PACK_TTL_DAYS = 30
_DECISION_SEVERITY = {
    PolicyDecision.ALLOW: 0,
    PolicyDecision.ALLOW_WITH_LOGGING: 1,
    PolicyDecision.REQUIRE_CONFIRMATION: 2,
    PolicyDecision.REQUIRE_REVIEW: 3,
    PolicyDecision.BLOCK: 4,
}


def _normalize_items(values: Optional[List[Any]]) -> List[Any]:
    return [value for value in (values or []) if value not in (None, "", [], {})]


def _evidence_required(result: SafetyEnforcementResult) -> bool:
    if result.decision in (
        PolicyDecision.REQUIRE_CONFIRMATION,
        PolicyDecision.REQUIRE_REVIEW,
        PolicyDecision.BLOCK,
    ):
        return True
    return result.risk_level.value in {"high", "critical"}


def _evidence_sufficient(request: SafetyEnforcementRequest) -> bool:
    has_context = any(
        (
            _normalize_items(request.world_state_facts),
            _normalize_items(request.recent_observations),
            _normalize_items(request.assumptions),
            _normalize_items(request.uncertainty_notes),
        )
    )
    has_proposed_action = bool(request.proposed_action)
    has_downside = bool((request.expected_downside or "").strip())
    return has_context and has_proposed_action and has_downside


def _resolve_automated_channel_decision(
    result: SafetyEnforcementResult,
    channel: str,
) -> SafetyEnforcementResult:
    if (
        channel in AUTOMATED_CHANNELS
        and result.decision == PolicyDecision.REQUIRE_CONFIRMATION
    ):
        result.decision = PolicyDecision.REQUIRE_REVIEW
        result.rationale = (
            f"{result.rationale} Channel '{channel}' cannot collect inline human confirmation."
        )
    return result


def _escalate_decision(
    result: SafetyEnforcementResult,
    target: PolicyDecision,
    reason: str,
) -> SafetyEnforcementResult:
    if _DECISION_SEVERITY[target] > _DECISION_SEVERITY[result.decision]:
        result.decision = target
        result.rationale = f"{result.rationale} {reason}".strip()
    return result


def _apply_agent_autonomy_restrictions(
    result: SafetyEnforcementResult,
    request: SafetyEnforcementRequest,
    tenant_id: uuid.UUID,
    db: Session,
) -> SafetyEnforcementResult:
    profile = safety_trust.get_agent_trust_profile(
        db,
        tenant_id,
        request.agent_slug,
        commit_on_refresh=False,
    )
    if not profile:
        return result

    result.agent_trust_score = round(float(profile.trust_score or 0.0), 3)
    result.autonomy_tier = AutonomyTier(profile.autonomy_tier)
    result.trust_confidence = round(float(profile.confidence or 0.0), 3)
    result.trust_source = "agent_trust_profile"

    # bounded_autonomous: full access — override any prior BLOCK/REVIEW.
    # High/critical risk actions are logged but not blocked; the agent has
    # earned trust through accumulated evidence and admin promotion.
    if result.autonomy_tier == AutonomyTier.BOUNDED_AUTONOMOUS:
        if result.risk_level.value in {"high", "critical"}:
            result.decision = PolicyDecision.ALLOW_WITH_LOGGING
        else:
            result.decision = PolicyDecision.ALLOW
        result.rationale = f"Agent '{request.agent_slug}' has bounded-autonomous access."
        return result

    if result.autonomy_tier == AutonomyTier.OBSERVE_ONLY:
        # Allow read-only tools even for observe-only agents — they need to
        # search/read to build trust evidence. Blocking everything creates a
        # chicken-and-egg deadlock where the agent can never promote itself.
        if result.risk_class.value == "read_only" and result.side_effect_level.value == "none":
            return _escalate_decision(
                result,
                PolicyDecision.ALLOW_WITH_LOGGING,
                f"Agent '{request.agent_slug}' is restricted to observe-only autonomy (read-only allowed).",
            )
        return _escalate_decision(
            result,
            PolicyDecision.BLOCK,
            f"Agent '{request.agent_slug}' is restricted to observe-only autonomy.",
        )

    if result.autonomy_tier == AutonomyTier.RECOMMEND_ONLY:
        if result.risk_class.value == "read_only" and result.side_effect_level.value == "none":
            return _escalate_decision(
                result,
                PolicyDecision.ALLOW_WITH_LOGGING,
                f"Agent '{request.agent_slug}' is restricted to recommend-only autonomy.",
            )
        return _escalate_decision(
            result,
            PolicyDecision.REQUIRE_REVIEW,
            f"Agent '{request.agent_slug}' is restricted to recommend-only autonomy.",
        )

    if (
        result.autonomy_tier == AutonomyTier.SUPERVISED_EXECUTION
        and result.risk_level.value in {"high", "critical"}
    ):
        return _escalate_decision(
            result,
            PolicyDecision.REQUIRE_REVIEW,
            f"Agent '{request.agent_slug}' requires supervision for high-risk actions.",
        )

    return result


def list_evidence_packs(
    db: Session,
    tenant_id: uuid.UUID,
    limit: int = 100,
) -> List[SafetyEvidencePack]:
    return (
        db.query(SafetyEvidencePack)
        .filter(
            SafetyEvidencePack.tenant_id == tenant_id,
            SafetyEvidencePack.expires_at > datetime.utcnow(),
        )
        .order_by(SafetyEvidencePack.created_at.desc())
        .limit(limit)
        .all()
    )


def get_evidence_pack(
    db: Session,
    tenant_id: uuid.UUID,
    evidence_pack_id: uuid.UUID,
) -> Optional[SafetyEvidencePack]:
    return (
        db.query(SafetyEvidencePack)
        .filter(
            SafetyEvidencePack.id == evidence_pack_id,
            SafetyEvidencePack.tenant_id == tenant_id,
            SafetyEvidencePack.expires_at > datetime.utcnow(),
        )
        .first()
    )


def enforce_action(
    db: Session,
    tenant_id: uuid.UUID,
    request: SafetyEnforcementRequest,
    created_by: Optional[uuid.UUID] = None,
) -> SafetyEnforcementResult:
    evaluation = safety_policies.evaluate_action(
        db,
        tenant_id=tenant_id,
        action_type=request.action_type,
        action_name=request.action_name,
        channel=request.channel,
    )
    evaluation_data = (
        evaluation.model_dump()
        if hasattr(evaluation, "model_dump")
        else evaluation.dict()
    )

    result = SafetyEnforcementResult(
        **evaluation_data,
        evidence_required=False,
        evidence_sufficient=False,
        evidence_pack_id=None,
        agent_trust_score=None,
        autonomy_tier=None,
        trust_confidence=None,
        trust_source=None,
    )
    result = _resolve_automated_channel_decision(result, request.channel)
    result = _apply_agent_autonomy_restrictions(result, request, tenant_id, db)
    result.evidence_required = _evidence_required(result)
    result.evidence_sufficient = (
        _evidence_sufficient(request) if result.evidence_required else True
    )

    if result.evidence_required and not result.evidence_sufficient:
        if result.decision in (
            PolicyDecision.ALLOW,
            PolicyDecision.ALLOW_WITH_LOGGING,
            PolicyDecision.REQUIRE_CONFIRMATION,
        ):
            result.decision = PolicyDecision.REQUIRE_REVIEW
            result.rationale = (
                f"{result.rationale} Evidence pack is incomplete for this sensitive action."
            )

    if result.evidence_required:
        evidence_pack = SafetyEvidencePack(
            tenant_id=tenant_id,
            action_type=request.action_type.value,
            action_name=request.action_name,
            channel=request.channel,
            decision=result.decision.value,
            decision_source=result.decision_source,
            risk_class=result.risk_class.value,
            risk_level=result.risk_level.value,
            evidence_required=result.evidence_required,
            evidence_sufficient=result.evidence_sufficient,
            world_state_facts=_normalize_items(request.world_state_facts),
            recent_observations=_normalize_items(request.recent_observations),
            assumptions=_normalize_items(request.assumptions),
            uncertainty_notes=_normalize_items(request.uncertainty_notes),
            proposed_action=request.proposed_action,
            expected_downside=request.expected_downside,
            context_summary=request.context_summary,
            context_ref=request.context_ref,
            agent_slug=request.agent_slug,
            created_by=created_by,
            expires_at=datetime.utcnow() + timedelta(days=EVIDENCE_PACK_TTL_DAYS),
        )
        db.add(evidence_pack)
        db.commit()
        db.refresh(evidence_pack)
        result.evidence_pack_id = evidence_pack.id

    return result
