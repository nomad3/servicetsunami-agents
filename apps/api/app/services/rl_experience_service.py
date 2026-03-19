import uuid
from datetime import datetime, timedelta
from typing import Optional, List, Dict, Any
from sqlalchemy.orm import Session
from sqlalchemy import text

from app.models.rl_experience import RLExperience
from app.services import embedding_service


# Decision point constants
DECISION_POINTS = [
    "agent_selection", "memory_recall", "skill_routing",
    "orchestration_routing", "triage_classification",
    "response_generation", "tool_selection", "entity_validation",
    "score_weighting", "sync_strategy", "execution_decision",
    "code_strategy", "deal_stage_advance", "change_significance",
    "code_task",
]

# Reward discount factor for backward propagation
TRAJECTORY_DISCOUNT = 0.7


def log_experience(
    db: Session,
    tenant_id: uuid.UUID,
    trajectory_id: uuid.UUID,
    step_index: int,
    decision_point: str,
    state: Dict[str, Any],
    action: Dict[str, Any],
    alternatives: List[Dict[str, Any]] = None,
    explanation: Dict[str, Any] = None,
    policy_version: str = None,
    exploration: bool = False,
    state_text: str = None,
) -> RLExperience:
    """Log a decision as an RL experience. Optionally embeds state text via Gemini."""
    state_embedding = None
    if state_text:
        state_embedding = embedding_service.embed_text(state_text, task_type="RETRIEVAL_DOCUMENT")

    exp = RLExperience(
        tenant_id=tenant_id,
        trajectory_id=trajectory_id,
        step_index=step_index,
        decision_point=decision_point,
        state=state,
        state_embedding=state_embedding,
        action=action,
        alternatives=alternatives or [],
        explanation=explanation,
        policy_version=policy_version,
        exploration=exploration,
    )
    db.add(exp)
    db.commit()
    db.refresh(exp)
    return exp


def assign_reward(
    db: Session,
    experience_id: uuid.UUID,
    reward: float,
    reward_components: Dict[str, Any],
    reward_source: str,
) -> RLExperience:
    """Assign a reward to a specific experience."""
    exp = db.query(RLExperience).filter(RLExperience.id == experience_id).first()
    if not exp:
        return None
    exp.reward = max(-1.0, min(1.0, reward))
    exp.reward_components = reward_components
    exp.reward_source = reward_source
    exp.rewarded_at = datetime.utcnow()
    db.commit()
    db.refresh(exp)
    return exp


def propagate_reward_backward(
    db: Session,
    trajectory_id: uuid.UUID,
    terminal_reward: float,
    reward_source: str,
) -> int:
    """Propagate reward backward through a trajectory with discount factor."""
    experiences = (
        db.query(RLExperience)
        .filter(RLExperience.trajectory_id == trajectory_id)
        .order_by(RLExperience.step_index.desc())
        .all()
    )
    if not experiences:
        return 0

    updated = 0
    downstream_reward = 0.0
    for i, exp in enumerate(experiences):
        if i == 0:
            # Terminal step gets the full reward
            step_reward = terminal_reward
        else:
            step_reward = downstream_reward * TRAJECTORY_DISCOUNT

        # Combine with any pre-existing direct reward (additive)
        if exp.reward is not None:
            step_reward = max(-1.0, min(1.0, exp.reward + step_reward))

        exp.reward = max(-1.0, min(1.0, step_reward))
        exp.reward_components = {
            "propagated": round(step_reward, 4),
            "source_reward": terminal_reward,
            "direct_reward": exp.reward_components.get("direct", 0) if exp.reward_components else 0,
        }
        exp.reward_source = reward_source
        exp.rewarded_at = datetime.utcnow()
        downstream_reward = step_reward
        updated += 1

    db.commit()
    return updated


def find_similar_experiences(
    db: Session,
    tenant_id: uuid.UUID,
    decision_point: str,
    state_text: str,
    limit: int = 200,
) -> List[Dict[str, Any]]:
    """Find similar past experiences using pgvector cosine similarity."""
    query_embedding = embedding_service.embed_text(state_text, task_type="RETRIEVAL_QUERY")
    if not query_embedding:
        return []

    # Inline the vector literal via f-string to avoid SQLAlchemy confusing
    # colon-based named params with PostgreSQL type casts (same pattern as embedding_service.py)
    vector_literal = "[" + ",".join(str(v) for v in query_embedding) + "]"
    sql = text(f"""
        SELECT
            id, trajectory_id, step_index, decision_point,
            state, action, reward, reward_source, explanation,
            exploration, created_at,
            1 - (state_embedding <=> CAST('{vector_literal}' AS vector)) AS similarity
        FROM rl_experiences
        WHERE tenant_id = CAST(:tid AS uuid)
          AND decision_point = :dp
          AND archived_at IS NULL
          AND reward IS NOT NULL
        ORDER BY state_embedding <=> CAST('{vector_literal}' AS vector)
        LIMIT :lim
    """)
    rows = db.execute(sql, {"tid": str(tenant_id), "dp": decision_point, "lim": limit}).fetchall()
    return [
        {
            "id": str(r.id),
            "trajectory_id": str(r.trajectory_id),
            "step_index": r.step_index,
            "state": r.state,
            "action": r.action,
            "reward": r.reward,
            "reward_source": r.reward_source,
            "explanation": r.explanation,
            "exploration": r.exploration,
            "created_at": r.created_at.isoformat(),
            "similarity": float(r.similarity) if r.similarity else 0.0,
        }
        for r in rows
    ]


def get_trajectory(db: Session, trajectory_id: uuid.UUID) -> List[RLExperience]:
    """Get all experiences in a trajectory ordered by step."""
    return (
        db.query(RLExperience)
        .filter(RLExperience.trajectory_id == trajectory_id)
        .order_by(RLExperience.step_index)
        .all()
    )


def get_experiences_paginated(
    db: Session,
    tenant_id: uuid.UUID,
    decision_point: str = None,
    from_date: datetime = None,
    to_date: datetime = None,
    skip: int = 0,
    limit: int = 50,
) -> List[RLExperience]:
    """Paginated experience query for the Learning page."""
    q = db.query(RLExperience).filter(
        RLExperience.tenant_id == tenant_id,
        RLExperience.archived_at.is_(None),
    )
    if decision_point:
        q = q.filter(RLExperience.decision_point == decision_point)
    if from_date:
        q = q.filter(RLExperience.created_at >= from_date)
    if to_date:
        q = q.filter(RLExperience.created_at <= to_date)
    return q.order_by(RLExperience.created_at.desc()).offset(skip).limit(limit).all()


def archive_old_experiences(db: Session, tenant_id: uuid.UUID, days: int = 90) -> int:
    """Archive experiences older than retention window."""
    cutoff = datetime.utcnow() - timedelta(days=days)
    updated = (
        db.query(RLExperience)
        .filter(
            RLExperience.tenant_id == tenant_id,
            RLExperience.created_at < cutoff,
            RLExperience.archived_at.is_(None),
        )
        .update({"archived_at": datetime.utcnow()})
    )
    db.commit()
    return updated
