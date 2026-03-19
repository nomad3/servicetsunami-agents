import logging
import uuid
from typing import Optional, Dict, Any, List
from sqlalchemy.orm import Session

from app.models.rl_experience import RLExperience
from app.models.knowledge_entity import KnowledgeEntity
from app.models.tenant_features import TenantFeatures
from app.services import rl_experience_service

logger = logging.getLogger(__name__)


# Feedback type to reward mapping
FEEDBACK_REWARDS = {
    "thumbs_up": 0.6,
    "thumbs_down": -0.6,
    "memory_helpful": 0.4,
    "memory_irrelevant": -0.4,
    "memory_partial": 0.1,
    "memory_recall_positive": 0.2,
    "wrong_agent": -0.7,
    "flag_issue": -0.5,
    "entity_correction": -0.3,
}

# Star rating mapping: 1 -> -0.8, 3 -> 0.0, 5 -> +0.8
def star_to_reward(stars: float) -> float:
    return (stars - 3.0) * 0.4


def get_reward_weights(db: Session, tenant_id: uuid.UUID) -> Dict[str, float]:
    """Get tenant-specific reward weights or defaults."""
    features = db.query(TenantFeatures).filter(TenantFeatures.tenant_id == tenant_id).first()
    if features and features.rl_settings and "reward_weights" in features.rl_settings:
        return features.rl_settings["reward_weights"]
    return {"implicit": 0.3, "explicit": 0.5, "admin": 0.2}


def compute_composite_reward(
    implicit: Optional[float],
    explicit: Optional[float],
    admin: Optional[float],
    weights: Dict[str, float],
) -> float:
    """Compute weighted composite reward, redistributing absent source weights."""
    sources = {}
    if implicit is not None:
        sources["implicit"] = (implicit, weights.get("implicit", 0.3))
    if explicit is not None:
        sources["explicit"] = (explicit, weights.get("explicit", 0.5))
    if admin is not None:
        sources["admin"] = (admin, weights.get("admin", 0.2))

    if not sources:
        return 0.0

    total_weight = sum(w for _, w in sources.values())
    if total_weight == 0:
        return 0.0

    result = sum(val * (w / total_weight) for val, w in sources.values())
    return max(-1.0, min(1.0, result))


def process_explicit_feedback(
    db: Session,
    tenant_id: uuid.UUID,
    trajectory_id: uuid.UUID,
    feedback_type: str,
    step_index: Optional[int] = None,
    value: Optional[float] = None,
) -> int:
    """Process user feedback and assign rewards to the trajectory."""
    if feedback_type == "star_rating" and value is not None:
        reward = star_to_reward(value)
    elif feedback_type in FEEDBACK_REWARDS:
        reward = FEEDBACK_REWARDS[feedback_type]
    else:
        return 0

    weights = get_reward_weights(db, tenant_id)

    if step_index is not None:
        # Target a specific step
        exp = (
            db.query(RLExperience)
            .filter(
                RLExperience.trajectory_id == trajectory_id,
                RLExperience.step_index == step_index,
            )
            .first()
        )
        if exp:
            composite = compute_composite_reward(None, reward, None, weights)
            rl_experience_service.assign_reward(
                db, exp.id, composite,
                {"explicit": reward, "feedback_type": feedback_type},
                "explicit_rating",
            )
            return 1
    else:
        # Propagate backward through entire trajectory
        return rl_experience_service.propagate_reward_backward(
            db, trajectory_id, reward, "explicit_rating"
        )


def process_admin_review(
    db: Session,
    tenant_id: uuid.UUID,
    experience_id: uuid.UUID,
    rating: str,
) -> Optional[RLExperience]:
    """Process admin review rating on a specific experience."""
    rating_rewards = {"good": 0.8, "acceptable": 0.0, "poor": -0.8}
    reward = rating_rewards.get(rating)
    if reward is None:
        return None

    weights = get_reward_weights(db, tenant_id)
    composite = compute_composite_reward(None, None, reward, weights)
    return rl_experience_service.assign_reward(
        db, experience_id, composite,
        {"admin": reward, "rating": rating},
        "admin_review",
    )


def compute_implicit_reward(signals: Dict[str, Any]) -> float:
    """Compute implicit reward from system signals."""
    reward = 0.0
    if signals.get("task_completed"):
        reward += 0.3
    if signals.get("task_failed"):
        reward -= 0.5
    if signals.get("latency_below_p50"):
        reward += 0.1
    if signals.get("user_continued"):
        reward += 0.1
    if signals.get("user_disengaged"):
        reward -= 0.1
    if signals.get("notification_read"):
        reward += 0.1
    if signals.get("notification_dismissed_unread"):
        reward -= 0.2
    if signals.get("entity_referenced"):
        reward += 0.2
    if signals.get("memory_recall_positive_response"):
        reward += 0.2
    if signals.get("deal_advanced"):
        reward += 0.4
    if signals.get("pipeline_succeeded"):
        reward += 0.2
    return max(-1.0, min(1.0, reward))


def compute_cost_adjusted_reward(
    raw_reward: float,
    cost_usd: float,
    max_cost_budget: float = 0.10,
) -> float:
    """Adjust reward based on cost efficiency.

    For positive rewards, cheaper executions get a bonus (up to full reward).
    For negative rewards, expensive executions are penalized more.

    Args:
        raw_reward: The unadjusted reward value.
        cost_usd: Actual cost of the action in USD.
        max_cost_budget: Budget ceiling for cost normalization (default $0.10).

    Returns:
        Cost-adjusted reward clamped to [-1.0, 1.0].
    """
    cost_factor = max(0.0, 1.0 - (cost_usd / max_cost_budget))
    if raw_reward >= 0:
        adjusted = raw_reward * (0.7 + 0.3 * cost_factor)
    else:
        adjusted = raw_reward * (1.3 - 0.3 * cost_factor)
    return max(-1.0, min(1.0, adjusted))


def update_entity_scores_on_reward(
    db: Session,
    tenant_id: uuid.UUID,
    entity_ids: List[uuid.UUID],
    reward: float,
) -> int:
    """Update data_quality_score on entities based on RL reward signal.

    Positive reward (>0): bump score by +0.02.
    Negative reward (<0): drop score by -0.02, flag for review if below 0.3.

    Args:
        db: Database session.
        tenant_id: Tenant UUID for isolation.
        entity_ids: List of entity UUIDs involved in the decision.
        reward: The reward value from feedback.

    Returns:
        Number of entities updated.
    """
    if not entity_ids:
        return 0

    entities = db.query(KnowledgeEntity).filter(
        KnowledgeEntity.id.in_(entity_ids),
        KnowledgeEntity.tenant_id == tenant_id,
    ).all()

    updated = 0
    for entity in entities:
        current_score = entity.data_quality_score if entity.data_quality_score is not None else 0.5
        if reward > 0:
            new_score = min(1.0, current_score + 0.02)
        elif reward < 0:
            new_score = max(0.0, current_score - 0.02)
        else:
            continue

        entity.data_quality_score = new_score

        # Flag for review if quality drops below threshold
        if new_score < 0.3:
            tags = entity.tags if isinstance(entity.tags, list) else []
            if "needs_review" not in tags:
                tags.append("needs_review")
                entity.tags = tags
            logger.info(
                "Entity %s quality score dropped to %.2f — flagged for review",
                entity.id, new_score,
            )

        updated += 1

    if updated:
        db.flush()

    return updated
