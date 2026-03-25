"""Policy rollout service — shadow mode, traffic splitting, auto-rollback."""

import logging
import random
from datetime import datetime, timedelta
from typing import Dict, List, Optional
import uuid

from sqlalchemy.orm import Session

from app.models.learning_experiment import LearningExperiment, PolicyCandidate

logger = logging.getLogger(__name__)

# Auto-rollback thresholds
ROLLBACK_MIN_SAMPLES = 10
ROLLBACK_REGRESSION_THRESHOLD = -0.15  # -15% reward regression triggers rollback


def get_active_rollout(
    db: Session,
    tenant_id: uuid.UUID,
    decision_point: str,
) -> Optional[Dict]:
    """Check if there's an active rollout experiment for a decision point.

    Returns the rollout config if a promoted candidate has a running
    split experiment, or None if no rollout is active.
    """
    experiment = (
        db.query(LearningExperiment)
        .join(PolicyCandidate, LearningExperiment.candidate_id == PolicyCandidate.id)
        .filter(
            LearningExperiment.tenant_id == tenant_id,
            LearningExperiment.status == "running",
            LearningExperiment.experiment_type == "split",
            PolicyCandidate.decision_point == decision_point,
            PolicyCandidate.status.in_(["evaluating", "promoted"]),
        )
        .first()
    )
    if not experiment:
        return None

    candidate = (
        db.query(PolicyCandidate)
        .filter(PolicyCandidate.id == experiment.candidate_id)
        .first()
    )
    if not candidate:
        return None

    return {
        "experiment_id": str(experiment.id),
        "candidate_id": str(candidate.id),
        "experiment_type": experiment.experiment_type,
        "rollout_pct": experiment.rollout_pct,
        "proposed_policy": candidate.proposed_policy,
        "policy_type": candidate.policy_type,
    }


def should_apply_rollout(rollout: Dict) -> tuple:
    """Decide whether to apply the rollout policy for this request.

    Returns (apply_policy: bool, is_treatment_arm: bool).

    Only split mode is supported in Phase 2. Shadow mode (parallel
    execution of both policies) requires background CLI execution
    infrastructure and is planned for Phase 3.
    """
    is_treatment = random.random() < rollout["rollout_pct"]
    return is_treatment, is_treatment


def record_rollout_observation(
    db: Session,
    tenant_id: uuid.UUID,
    experiment_id: uuid.UUID,
    is_treatment: bool,
    reward: Optional[float] = None,
) -> None:
    """Record an observation for a running rollout experiment.

    Updates sample sizes and running reward averages.
    """
    experiment = (
        db.query(LearningExperiment)
        .filter(
            LearningExperiment.id == experiment_id,
            LearningExperiment.tenant_id == tenant_id,
            LearningExperiment.status == "running",
        )
        .first()
    )
    if not experiment:
        return

    now = datetime.utcnow()

    if is_treatment:
        n = experiment.treatment_sample_size
        experiment.treatment_sample_size = n + 1
        if reward is not None and experiment.treatment_avg_reward is not None:
            experiment.treatment_avg_reward = (
                (experiment.treatment_avg_reward * n + reward) / (n + 1)
            )
        elif reward is not None:
            experiment.treatment_avg_reward = reward
    else:
        n = experiment.control_sample_size
        experiment.control_sample_size = n + 1
        if reward is not None and experiment.control_avg_reward is not None:
            experiment.control_avg_reward = (
                (experiment.control_avg_reward * n + reward) / (n + 1)
            )
        elif reward is not None:
            experiment.control_avg_reward = reward

    experiment.updated_at = now
    db.flush()

    # Check auto-rollback conditions
    _check_auto_rollback(db, experiment)

    # Check experiment completion
    _check_experiment_completion(db, experiment)

    db.commit()


def _check_auto_rollback(db: Session, experiment: LearningExperiment) -> None:
    """Auto-rollback if treatment shows significant regression."""
    if (
        experiment.treatment_sample_size < ROLLBACK_MIN_SAMPLES
        or experiment.control_avg_reward is None
        or experiment.treatment_avg_reward is None
        or experiment.control_avg_reward == 0
    ):
        return

    regression = (
        (experiment.treatment_avg_reward - experiment.control_avg_reward)
        / abs(experiment.control_avg_reward)
    )

    if regression < ROLLBACK_REGRESSION_THRESHOLD:
        logger.warning(
            "Auto-rollback: experiment %s shows %.1f%% regression (threshold: %.1f%%)",
            experiment.id, regression * 100, ROLLBACK_REGRESSION_THRESHOLD * 100,
        )
        experiment.status = "aborted"
        experiment.completed_at = datetime.utcnow()
        experiment.improvement_pct = round(regression * 100, 2)
        experiment.is_significant = "regression"
        experiment.conclusion = (
            f"Auto-rolled back: {regression * 100:.1f}% reward regression "
            f"after {experiment.treatment_sample_size} treatment samples"
        )

        # Reject the candidate
        candidate = (
            db.query(PolicyCandidate)
            .filter(PolicyCandidate.id == experiment.candidate_id)
            .first()
        )
        if candidate and candidate.status == "evaluating":
            candidate.status = "rejected"
            candidate.rejected_at = datetime.utcnow()
            candidate.rejection_reason = experiment.conclusion
            candidate.updated_at = datetime.utcnow()


def _check_experiment_completion(db: Session, experiment: LearningExperiment) -> None:
    """Complete experiment if min sample size reached or max duration exceeded."""
    if experiment.status != "running":
        return

    now = datetime.utcnow()
    total_samples = experiment.control_sample_size + experiment.treatment_sample_size

    # Check max duration
    if experiment.started_at:
        elapsed_hours = (now - experiment.started_at).total_seconds() / 3600
        if elapsed_hours >= experiment.max_duration_hours:
            _complete_experiment(experiment, "max_duration_reached")
            return

    # Check min sample size for both arms
    if (
        experiment.control_sample_size >= experiment.min_sample_size
        and experiment.treatment_sample_size >= experiment.min_sample_size
    ):
        _complete_experiment(experiment, "min_samples_reached")


def _complete_experiment(experiment: LearningExperiment, reason: str) -> None:
    """Finalize a running experiment with results."""
    experiment.status = "completed"
    experiment.completed_at = datetime.utcnow()

    if (
        experiment.control_avg_reward is not None
        and experiment.treatment_avg_reward is not None
        and experiment.control_avg_reward != 0
    ):
        experiment.improvement_pct = round(
            (experiment.treatment_avg_reward - experiment.control_avg_reward)
            / abs(experiment.control_avg_reward) * 100, 2
        )
        experiment.is_significant = (
            "yes" if experiment.improvement_pct > 5.0
            and experiment.treatment_sample_size >= experiment.min_sample_size
            else "no"
        )
    else:
        experiment.is_significant = "insufficient_data"

    if (
        experiment.control_avg_reward is not None
        and experiment.treatment_avg_reward is not None
        and experiment.improvement_pct is not None
    ):
        experiment.conclusion = (
            f"Completed ({reason}): treatment={experiment.treatment_avg_reward:.3f} "
            f"vs control={experiment.control_avg_reward:.3f} "
            f"({experiment.improvement_pct:+.1f}%) "
            f"n_control={experiment.control_sample_size}, n_treatment={experiment.treatment_sample_size}. "
            f"Significant: {experiment.is_significant}"
        )
    else:
        experiment.conclusion = (
            f"Completed ({reason}): insufficient reward data "
            f"(control={experiment.control_sample_size} samples, "
            f"treatment={experiment.treatment_sample_size} samples)"
        )


def start_rollout(
    db: Session,
    tenant_id: uuid.UUID,
    candidate_id: uuid.UUID,
    rollout_pct: float = 0.1,
    experiment_type: str = "split",
    min_sample_size: int = 30,
    max_duration_hours: int = 168,
) -> LearningExperiment:
    """Start a controlled rollout for a promoted or evaluating candidate."""
    candidate = (
        db.query(PolicyCandidate)
        .filter(PolicyCandidate.id == candidate_id, PolicyCandidate.tenant_id == tenant_id)
        .first()
    )
    if not candidate:
        raise ValueError(f"Candidate {candidate_id} not found")
    if candidate.status not in ("evaluating", "promoted"):
        raise ValueError(f"Candidate must be evaluating or promoted, got '{candidate.status}'")
    if experiment_type != "split":
        raise ValueError(
            "Only 'split' rollouts are supported in Phase 2. "
            "Shadow mode (parallel execution) is planned for Phase 3."
        )

    # Enforce one active rollout per decision point
    existing = get_active_rollout(db, tenant_id, candidate.decision_point)
    if existing:
        raise ValueError(
            f"An active rollout already exists for decision_point='{candidate.decision_point}' "
            f"(experiment_id={existing['experiment_id']}). Stop it first."
        )

    experiment = LearningExperiment(
        tenant_id=tenant_id,
        candidate_id=candidate_id,
        decision_point=candidate.decision_point,
        experiment_type=experiment_type,
        rollout_pct=max(0.01, min(1.0, rollout_pct)),
        min_sample_size=min_sample_size,
        max_duration_hours=max_duration_hours,
        status="running",
        started_at=datetime.utcnow(),
        control_avg_reward=None,
        treatment_avg_reward=None,
    )
    db.add(experiment)
    db.commit()
    db.refresh(experiment)
    return experiment


def stop_rollout(
    db: Session,
    tenant_id: uuid.UUID,
    experiment_id: uuid.UUID,
) -> Optional[LearningExperiment]:
    """Manually stop a running rollout."""
    experiment = (
        db.query(LearningExperiment)
        .filter(
            LearningExperiment.id == experiment_id,
            LearningExperiment.tenant_id == tenant_id,
            LearningExperiment.status == "running",
        )
        .first()
    )
    if not experiment:
        return None

    _complete_experiment(experiment, "manual_stop")
    db.commit()
    db.refresh(experiment)
    return experiment
