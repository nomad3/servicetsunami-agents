"""Service layer for the self-improvement pipeline."""

from datetime import datetime, timedelta
from typing import Dict, List, Optional
import uuid

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.models.learning_experiment import LearningExperiment, PolicyCandidate
from app.schemas.learning_experiment import (
    LearningExperimentCreate,
    PolicyCandidateCreate,
)


# --- Policy Candidates ---

def create_candidate(
    db: Session,
    tenant_id: uuid.UUID,
    candidate_in: PolicyCandidateCreate,
) -> PolicyCandidate:
    candidate = PolicyCandidate(
        tenant_id=tenant_id,
        policy_type=candidate_in.policy_type.value,
        decision_point=candidate_in.decision_point,
        description=candidate_in.description,
        current_policy=candidate_in.current_policy,
        proposed_policy=candidate_in.proposed_policy,
        rationale=candidate_in.rationale,
        source_experience_count=candidate_in.source_experience_count,
        source_query=candidate_in.source_query,
        baseline_reward=candidate_in.baseline_reward,
        expected_improvement=candidate_in.expected_improvement,
    )
    db.add(candidate)
    db.commit()
    db.refresh(candidate)
    return candidate


def get_candidate(
    db: Session,
    tenant_id: uuid.UUID,
    candidate_id: uuid.UUID,
) -> Optional[PolicyCandidate]:
    return (
        db.query(PolicyCandidate)
        .filter(PolicyCandidate.id == candidate_id, PolicyCandidate.tenant_id == tenant_id)
        .first()
    )


def list_candidates(
    db: Session,
    tenant_id: uuid.UUID,
    status: Optional[str] = None,
    policy_type: Optional[str] = None,
    limit: int = 50,
) -> List[PolicyCandidate]:
    q = db.query(PolicyCandidate).filter(PolicyCandidate.tenant_id == tenant_id)
    if status:
        q = q.filter(PolicyCandidate.status == status)
    if policy_type:
        q = q.filter(PolicyCandidate.policy_type == policy_type)
    return q.order_by(PolicyCandidate.created_at.desc()).limit(limit).all()


def promote_candidate(
    db: Session,
    tenant_id: uuid.UUID,
    candidate_id: uuid.UUID,
) -> Optional[PolicyCandidate]:
    candidate = get_candidate(db, tenant_id, candidate_id)
    if not candidate or candidate.status not in ("proposed", "evaluating"):
        return None
    candidate.status = "promoted"
    candidate.promoted_at = datetime.utcnow()
    candidate.updated_at = datetime.utcnow()
    db.commit()
    db.refresh(candidate)
    return candidate


def reject_candidate(
    db: Session,
    tenant_id: uuid.UUID,
    candidate_id: uuid.UUID,
    reason: str,
) -> Optional[PolicyCandidate]:
    candidate = get_candidate(db, tenant_id, candidate_id)
    if not candidate or candidate.status not in ("proposed", "evaluating"):
        return None
    candidate.status = "rejected"
    candidate.rejected_at = datetime.utcnow()
    candidate.rejection_reason = reason
    candidate.updated_at = datetime.utcnow()
    db.commit()
    db.refresh(candidate)
    return candidate


# --- Experiments ---

def create_experiment(
    db: Session,
    tenant_id: uuid.UUID,
    experiment_in: LearningExperimentCreate,
) -> LearningExperiment:
    candidate = get_candidate(db, tenant_id, experiment_in.candidate_id)
    if not candidate:
        raise ValueError(f"Policy candidate {experiment_in.candidate_id} not found in this tenant")

    candidate.status = "evaluating"

    experiment = LearningExperiment(
        tenant_id=tenant_id,
        candidate_id=experiment_in.candidate_id,
        experiment_type=experiment_in.experiment_type.value,
        rollout_pct=experiment_in.rollout_pct,
        min_sample_size=experiment_in.min_sample_size,
        max_duration_hours=experiment_in.max_duration_hours,
        status="pending",
    )
    db.add(experiment)
    db.commit()
    db.refresh(experiment)
    return experiment


def get_experiment(
    db: Session,
    tenant_id: uuid.UUID,
    experiment_id: uuid.UUID,
) -> Optional[LearningExperiment]:
    return (
        db.query(LearningExperiment)
        .filter(LearningExperiment.id == experiment_id, LearningExperiment.tenant_id == tenant_id)
        .first()
    )


def list_experiments(
    db: Session,
    tenant_id: uuid.UUID,
    status: Optional[str] = None,
    candidate_id: Optional[uuid.UUID] = None,
    limit: int = 50,
) -> List[LearningExperiment]:
    q = db.query(LearningExperiment).filter(LearningExperiment.tenant_id == tenant_id)
    if status:
        q = q.filter(LearningExperiment.status == status)
    if candidate_id:
        q = q.filter(LearningExperiment.candidate_id == candidate_id)
    return q.order_by(LearningExperiment.created_at.desc()).limit(limit).all()


def run_offline_evaluation(
    db: Session,
    tenant_id: uuid.UUID,
    experiment_id: uuid.UUID,
) -> Optional[Dict]:
    """Run an offline evaluation of a policy candidate against historical RL data.

    Compares the candidate's decision_point reward distribution against
    the current baseline using the existing rl_experiences table.
    """
    experiment = get_experiment(db, tenant_id, experiment_id)
    if not experiment or experiment.status not in ("pending", "running"):
        return None

    candidate = get_candidate(db, tenant_id, experiment.candidate_id)
    if not candidate:
        return None

    now = datetime.utcnow()
    experiment.status = "running"
    experiment.started_at = now

    # Query RL experiences for this decision point
    # Control: experiences matching current policy
    # Treatment: experiences that would match proposed policy
    control_sql = text("""
        SELECT COUNT(*) AS cnt, AVG(reward) AS avg_reward
        FROM rl_experiences
        WHERE tenant_id = CAST(:tid AS uuid)
          AND decision_point = :dp
          AND reward IS NOT NULL
          AND archived_at IS NULL
    """)
    control_row = db.execute(control_sql, {
        "tid": str(tenant_id),
        "dp": candidate.decision_point,
    }).one()

    experiment.control_sample_size = int(control_row.cnt or 0)
    experiment.control_avg_reward = float(control_row.avg_reward) if control_row.avg_reward is not None else None

    # For offline evaluation, treatment is simulated from the proposed policy params
    # Use experiences that match the proposed policy's target (e.g., platform=codex)
    proposed = candidate.proposed_policy or {}
    treatment_filters = []
    params = {"tid": str(tenant_id), "dp": candidate.decision_point}

    if "platform" in proposed:
        treatment_filters.append("action->>'platform' = :platform")
        params["platform"] = proposed["platform"]
    if "agent_slug" in proposed:
        treatment_filters.append("COALESCE(action->>'agent_slug', state->>'agent_slug') = :agent")
        params["agent"] = proposed["agent_slug"]

    where_clause = " AND ".join(treatment_filters) if treatment_filters else "1=1"
    treatment_sql = text(f"""
        SELECT COUNT(*) AS cnt, AVG(reward) AS avg_reward
        FROM rl_experiences
        WHERE tenant_id = CAST(:tid AS uuid)
          AND decision_point = :dp
          AND reward IS NOT NULL
          AND archived_at IS NULL
          AND {where_clause}
    """)
    treatment_row = db.execute(treatment_sql, params).one()

    experiment.treatment_sample_size = int(treatment_row.cnt or 0)
    experiment.treatment_avg_reward = float(treatment_row.avg_reward) if treatment_row.avg_reward is not None else None

    # Evaluate
    if (
        experiment.control_avg_reward is not None
        and experiment.treatment_avg_reward is not None
        and experiment.treatment_sample_size >= experiment.min_sample_size
    ):
        if experiment.control_avg_reward > 0:
            experiment.improvement_pct = round(
                (experiment.treatment_avg_reward - experiment.control_avg_reward)
                / experiment.control_avg_reward * 100, 2
            )
        else:
            experiment.improvement_pct = 0.0

        # Simple significance: require min sample size and positive improvement
        experiment.is_significant = (
            "yes" if experiment.improvement_pct > 5.0
            and experiment.treatment_sample_size >= experiment.min_sample_size
            else "no"
        )

        experiment.conclusion = (
            f"Treatment avg_reward={experiment.treatment_avg_reward:.3f} vs "
            f"control={experiment.control_avg_reward:.3f} "
            f"({experiment.improvement_pct:+.1f}%, n={experiment.treatment_sample_size}). "
            f"Significant: {experiment.is_significant}"
        )
    else:
        experiment.is_significant = "insufficient_data"
        experiment.conclusion = (
            f"Insufficient data: control={experiment.control_sample_size}, "
            f"treatment={experiment.treatment_sample_size} "
            f"(need {experiment.min_sample_size})"
        )

    experiment.status = "completed"
    experiment.completed_at = datetime.utcnow()
    experiment.updated_at = datetime.utcnow()
    db.commit()
    db.refresh(experiment)

    return {
        "experiment_id": str(experiment.id),
        "status": experiment.status,
        "control": {"n": experiment.control_sample_size, "avg_reward": experiment.control_avg_reward},
        "treatment": {"n": experiment.treatment_sample_size, "avg_reward": experiment.treatment_avg_reward},
        "improvement_pct": experiment.improvement_pct,
        "is_significant": experiment.is_significant,
        "conclusion": experiment.conclusion,
    }


def generate_routing_candidates(
    db: Session,
    tenant_id: uuid.UUID,
) -> List[PolicyCandidate]:
    """Auto-generate policy candidates from RL experience patterns.

    Analyzes per-platform reward distributions and proposes routing
    changes when one platform consistently outperforms another.
    """
    sql = text("""
        SELECT
            action->>'platform' AS platform,
            COUNT(*) AS total,
            AVG(reward) FILTER (WHERE reward IS NOT NULL) AS avg_reward,
            COUNT(*) FILTER (WHERE reward IS NOT NULL) AS rated
        FROM rl_experiences
        WHERE tenant_id = CAST(:tid AS uuid)
          AND decision_point = 'chat_response'
          AND archived_at IS NULL
          AND action->>'platform' IS NOT NULL
        GROUP BY action->>'platform'
        HAVING COUNT(*) >= 10
        ORDER BY avg_reward DESC NULLS LAST
    """)
    rows = db.execute(sql, {"tid": str(tenant_id)}).fetchall()

    if len(rows) < 2:
        return []

    candidates = []
    best = rows[0]
    for other in rows[1:]:
        if (
            best.avg_reward is not None
            and other.avg_reward is not None
            and best.avg_reward > other.avg_reward
            and best.rated >= 10
        ):
            improvement = (best.avg_reward - other.avg_reward) / max(abs(other.avg_reward), 0.01) * 100
            if improvement > 10:  # Only propose if >10% improvement
                candidate = PolicyCandidate(
                    tenant_id=tenant_id,
                    policy_type="routing",
                    decision_point="chat_response",
                    description=(
                        f"Route more traffic to {best.platform} (avg_reward={best.avg_reward:.3f}) "
                        f"away from {other.platform} (avg_reward={other.avg_reward:.3f})"
                    ),
                    current_policy={"platform": other.platform, "avg_reward": float(other.avg_reward)},
                    proposed_policy={"platform": best.platform, "avg_reward": float(best.avg_reward)},
                    rationale=(
                        f"{best.platform} outperforms {other.platform} by {improvement:.1f}% "
                        f"over {best.rated} rated experiences"
                    ),
                    source_experience_count=int(best.rated + other.rated),
                    source_query={"decision_point": "chat_response", "platforms": [best.platform, other.platform]},
                    baseline_reward=float(other.avg_reward),
                    expected_improvement=round(improvement, 1),
                )
                db.add(candidate)
                candidates.append(candidate)

    if candidates:
        db.commit()
        for c in candidates:
            db.refresh(c)

    return candidates
