import uuid
import math
import random
from datetime import datetime
from typing import Optional, List, Dict, Any, Tuple
from sqlalchemy.orm import Session

from app.models.rl_experience import RLExperience
from app.models.rl_policy_state import RLPolicyState
from app.models.tenant_features import TenantFeatures
from app.services import rl_experience_service


# State text generators per decision point
def _state_text_agent_selection(state: Dict) -> str:
    return f"Task: {state.get('task_type', 'unknown')}, capabilities: {state.get('required_capabilities', [])}, urgency: {state.get('urgency', 'normal')}"


def _state_text_memory_recall(state: Dict) -> str:
    return f"Query: {state.get('query_keywords', '')}, agent: {state.get('agent_name', '')}, context: {state.get('context_summary', '')}"


def _state_text_skill_routing(state: Dict) -> str:
    return f"Task: {state.get('task_type', 'unknown')}, available skills: {state.get('available_skills', [])}"


def _state_text_orchestration_routing(state: Dict) -> str:
    return f"Supervisor: {state.get('supervisor', '')}, sub-agents: {state.get('sub_agents', [])}, complexity: {state.get('task_complexity', 'medium')}"


def _state_text_triage(state: Dict) -> str:
    return f"From: {state.get('sender', '')}, subject: {state.get('subject', '')}, entities: {state.get('entity_mentions', [])}"


def _state_text_default(state: Dict) -> str:
    return f"Decision context: {str(state)[:500]}"


def _state_text_response_generation(state: Dict) -> str:
    return f"Agent: {state.get('agent_name', '')}, message_count: {state.get('message_count', 0)}, topic: {state.get('topic', '')}"


def _state_text_tool_selection(state: Dict) -> str:
    return f"Task: {state.get('task_type', '')}, available_tools: {state.get('available_tools', [])}, context: {state.get('context', '')}"


def _state_text_entity_validation(state: Dict) -> str:
    return f"Entity: {state.get('entity_type', '')} '{state.get('entity_name', '')}', source: {state.get('source', '')}"


def _state_text_score_weighting(state: Dict) -> str:
    return f"Lead: {state.get('lead_name', '')}, rubric: {state.get('rubric_name', '')}, signals: {state.get('signal_count', 0)}"


def _state_text_sync_strategy(state: Dict) -> str:
    return f"Dataset: {state.get('dataset_name', '')}, rows: {state.get('row_count', 0)}, destination: {state.get('destination', '')}"


def _state_text_execution_decision(state: Dict) -> str:
    return f"Workflow: {state.get('workflow_type', '')}, priority: {state.get('priority', 'normal')}, retries: {state.get('retry_count', 0)}"


def _state_text_code_strategy(state: Dict) -> str:
    return f"Task: {state.get('task_description', '')[:200]}, repo: {state.get('repo', '')}, branch: {state.get('branch', '')}"


def _state_text_deal_stage_advance(state: Dict) -> str:
    return f"Deal: {state.get('deal_name', '')}, current_stage: {state.get('current_stage', '')}, score: {state.get('score', 0)}"


def _state_text_change_significance(state: Dict) -> str:
    return f"Competitor: {state.get('competitor_name', '')}, change_type: {state.get('change_type', '')}, source: {state.get('source', '')}"


def _state_text_code_task(state: Dict) -> str:
    return (
        f"Task: {state.get('task_type', 'code')}, "
        f"affected_files: {state.get('affected_files', [])}, "
        f"recent_history: {state.get('recent_history', [])}, "
        f"branch: {state.get('branch', '')}, "
        f"PR #{state.get('pr_number', '')}"
    )


STATE_TEXT_GENERATORS = {
    "agent_selection": _state_text_agent_selection,
    "memory_recall": _state_text_memory_recall,
    "skill_routing": _state_text_skill_routing,
    "orchestration_routing": _state_text_orchestration_routing,
    "triage_classification": _state_text_triage,
    "response_generation": _state_text_response_generation,
    "tool_selection": _state_text_tool_selection,
    "entity_validation": _state_text_entity_validation,
    "score_weighting": _state_text_score_weighting,
    "sync_strategy": _state_text_sync_strategy,
    "execution_decision": _state_text_execution_decision,
    "code_strategy": _state_text_code_strategy,
    "deal_stage_advance": _state_text_deal_stage_advance,
    "change_significance": _state_text_change_significance,
    "code_task": _state_text_code_task,
}


def generate_state_text(decision_point: str, state: Dict) -> str:
    gen = STATE_TEXT_GENERATORS.get(decision_point, _state_text_default)
    return gen(state)


def get_policy(db: Session, tenant_id: uuid.UUID, decision_point: str) -> Optional[RLPolicyState]:
    """Get tenant-specific policy with federated blending against global baseline.

    Phase 1: Binary fallback (tenant or global).
    Phase 2+: Alpha-blended scoring where alpha grows with tenant experience count.
    """
    tenant_policy = (
        db.query(RLPolicyState)
        .filter(RLPolicyState.tenant_id == tenant_id, RLPolicyState.decision_point == decision_point)
        .first()
    )
    global_policy = (
        db.query(RLPolicyState)
        .filter(RLPolicyState.tenant_id.is_(None), RLPolicyState.decision_point == decision_point)
        .first()
    )

    if tenant_policy and global_policy:
        # Check if tenant opted into global baseline
        features = db.query(TenantFeatures).filter(TenantFeatures.tenant_id == tenant_id).first()
        use_global = features.rl_settings.get("use_global_baseline", True) if features and features.rl_settings else True
        if use_global:
            # Alpha grows with experience count: alpha = min(1.0, count * blend_alpha_growth)
            growth = features.rl_settings.get("blend_alpha_growth", 0.01) if features and features.rl_settings else 0.01
            alpha = min(1.0, tenant_policy.experience_count * growth)
            # Store blending info for explanation generation
            tenant_policy._blend_alpha = alpha
            tenant_policy._global_weights = global_policy.weights
        return tenant_policy

    return tenant_policy or global_policy


def get_exploration_rate(db: Session, tenant_id: uuid.UUID, decision_point: str) -> float:
    """Get exploration rate for a tenant+decision point."""
    features = db.query(TenantFeatures).filter(TenantFeatures.tenant_id == tenant_id).first()
    if features and features.rl_settings:
        overrides = features.rl_settings.get("per_decision_overrides", {})
        if decision_point in overrides and "exploration_rate" in overrides[decision_point]:
            return overrides[decision_point]["exploration_rate"]
        return features.rl_settings.get("exploration_rate", 0.1)
    return 0.1


def get_exploration_rate_with_decay(
    db: Session,
    tenant_id: uuid.UUID,
    decision_point: str,
) -> float:
    """Get exploration rate with automatic decay as sample count grows.

    Formula: base_rate * max(0.05, 1.0 - sample_count / (min_samples * 4))

    At 30 samples (min_samples): 0.25 * 0.75 = 0.1875
    At 120 samples: 0.25 * 0.05 = 0.0125 (floor)
    """
    base_rate = get_exploration_rate(db, tenant_id, decision_point)

    min_samples = 30  # default
    # Check for per-decision override in tenant RL settings
    features = db.query(TenantFeatures).filter(
        TenantFeatures.tenant_id == tenant_id
    ).first()
    if features and features.rl_settings:
        overrides = features.rl_settings.get("per_decision_overrides", {})
        if decision_point in overrides:
            min_samples = overrides[decision_point].get(
                "min_samples_before_exploit", min_samples
            )

    # Count samples for this decision point
    sample_count = db.query(RLExperience).filter(
        RLExperience.tenant_id == tenant_id,
        RLExperience.decision_point == decision_point,
    ).count()

    decay_factor = max(0.05, 1.0 - sample_count / (min_samples * 4))
    return base_rate * decay_factor


def score_candidates(
    db: Session,
    tenant_id: uuid.UUID,
    decision_point: str,
    state: Dict[str, Any],
    candidates: List[Dict[str, Any]],
    state_text: str = None,
) -> List[Dict[str, Any]]:
    """Score candidates using reward-weighted regression over similar experiences."""
    if not state_text:
        state_text = generate_state_text(decision_point, state)

    similar = rl_experience_service.find_similar_experiences(
        db, tenant_id, decision_point, state_text, limit=200
    )

    if not similar:
        # Cold start: return candidates with default scores
        for c in candidates:
            c["rl_score"] = 0.5
            c["experience_count"] = 0
        return candidates

    # Score each candidate by matching against similar experiences
    now = datetime.utcnow()
    lambda_decay = 0.05  # ~14 day half-life

    for candidate in candidates:
        candidate_id = str(candidate.get("id", candidate.get("name", "")))
        weighted_sum = 0.0
        weight_total = 0.0
        match_count = 0

        for exp in similar:
            # Check if this experience chose the same candidate
            exp_action_id = str(exp["action"].get("id", exp["action"].get("name", "")))
            if exp_action_id != candidate_id:
                continue

            days_old = (now - datetime.fromisoformat(exp["created_at"])).days
            recency = math.exp(-lambda_decay * days_old)
            sim = exp.get("similarity", 0.5)
            w = recency * sim
            weighted_sum += exp["reward"] * w
            weight_total += w
            match_count += 1

        candidate["rl_score"] = weighted_sum / weight_total if weight_total > 0 else 0.5
        candidate["experience_count"] = match_count

    return candidates


def select_action(
    db: Session,
    tenant_id: uuid.UUID,
    decision_point: str,
    state: Dict[str, Any],
    candidates: List[Dict[str, Any]],
) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """Select an action using explore/exploit, returns (chosen_action, explanation)."""
    state_text = generate_state_text(decision_point, state)
    scored = score_candidates(db, tenant_id, decision_point, state, candidates, state_text)

    exploration_rate = get_exploration_rate(db, tenant_id, decision_point)
    is_exploration = random.random() < exploration_rate

    if is_exploration:
        # Explore: sample by uncertainty (less-tried candidates more likely)
        safe_candidates = [c for c in scored if c.get("rl_score", 0.5) > -0.5 or c.get("experience_count", 0) == 0]
        if not safe_candidates:
            safe_candidates = scored

        weights = [1.0 / max(c.get("experience_count", 0), 1) for c in safe_candidates]
        total = sum(weights)
        weights = [w / total for w in weights]
        chosen = random.choices(safe_candidates, weights=weights, k=1)[0]
    else:
        # Exploit: pick highest score
        chosen = max(scored, key=lambda c: c.get("rl_score", 0.5))

    alternatives = [
        {"id": str(c.get("id", c.get("name", ""))), "score": round(c.get("rl_score", 0.5), 3)}
        for c in scored if c != chosen
    ]

    explanation = {
        "decision": decision_point,
        "chosen": str(chosen.get("id", chosen.get("name", ""))),
        "score": round(chosen.get("rl_score", 0.5), 3),
        "reason": f"{'Exploration' if is_exploration else 'Highest reward-weighted score'} for {decision_point}",
        "experience_count": chosen.get("experience_count", 0),
        "alternatives": alternatives[:5],
        "exploration": is_exploration,
    }

    return chosen, explanation
