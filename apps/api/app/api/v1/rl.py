import uuid
import csv
import io
from datetime import datetime
from typing import Optional
from fastapi import APIRouter, Depends, Query
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from app.api import deps
from app.models.rl_experience import RLExperience
from app.models.rl_policy_state import RLPolicyState
from app.models.tenant_features import TenantFeatures
from app.schemas.rl_experience import RLExperienceInDB, RLFeedbackSubmit
from app.schemas.rl_policy_state import RLPolicyStateInDB, RLSettingsUpdate
from app.services import rl_experience_service, rl_reward_service

router = APIRouter()


@router.get("/overview")
def get_overview(
    db: Session = Depends(deps.get_db),
    current_user=Depends(deps.get_current_active_user),
):
    """Aggregated metrics for Learning Overview tab."""
    tid = current_user.tenant_id
    total = db.query(RLExperience).filter(
        RLExperience.tenant_id == tid, RLExperience.archived_at.is_(None)
    ).count()

    rewarded = db.query(RLExperience).filter(
        RLExperience.tenant_id == tid,
        RLExperience.reward.isnot(None),
        RLExperience.archived_at.is_(None),
    ).all()

    avg_reward = sum(e.reward for e in rewarded) / len(rewarded) if rewarded else 0.0

    features = db.query(TenantFeatures).filter(TenantFeatures.tenant_id == tid).first()
    exploration_rate = features.rl_settings.get("exploration_rate", 0.1) if features and features.rl_settings else 0.1

    latest_policy = (
        db.query(RLPolicyState)
        .filter(RLPolicyState.tenant_id == tid)
        .order_by(RLPolicyState.last_updated_at.desc())
        .first()
    )

    # 30-day rolling average
    from datetime import timedelta
    thirty_days_ago = datetime.utcnow() - timedelta(days=30)
    rewarded_30d = [e for e in rewarded if e.rewarded_at and e.rewarded_at >= thirty_days_ago]
    avg_reward_30d = sum(e.reward for e in rewarded_30d) / len(rewarded_30d) if rewarded_30d else None

    # Top decision points by experience count
    from sqlalchemy import func
    top_dp = (
        db.query(RLExperience.decision_point, func.count(RLExperience.id).label("count"))
        .filter(RLExperience.tenant_id == tid, RLExperience.archived_at.is_(None))
        .group_by(RLExperience.decision_point)
        .order_by(func.count(RLExperience.id).desc())
        .limit(5)
        .all()
    )

    return {
        "total_experiences": total,
        "avg_reward": round(avg_reward, 3),
        "avg_reward_30d": round(avg_reward_30d, 3) if avg_reward_30d is not None else None,
        "exploration_rate": exploration_rate,
        "policy_version": latest_policy.version if latest_policy else "v0",
        "policy_updated_at": latest_policy.last_updated_at.isoformat() if latest_policy and latest_policy.last_updated_at else None,
        "top_decision_points": [{"name": dp, "count": count} for dp, count in top_dp],
    }


@router.get("/experiences")
def list_experiences(
    decision_point: Optional[str] = None,
    from_date: Optional[datetime] = None,
    to_date: Optional[datetime] = None,
    page: int = Query(1, ge=1),
    per_page: int = Query(50, ge=1, le=200),
    db: Session = Depends(deps.get_db),
    current_user=Depends(deps.get_current_active_user),
):
    """Paginated experience list."""
    skip = (page - 1) * per_page
    experiences = rl_experience_service.get_experiences_paginated(
        db, current_user.tenant_id, decision_point, from_date, to_date, skip, per_page
    )
    return [RLExperienceInDB.model_validate(e) for e in experiences]


@router.get("/experiences/{trajectory_id}")
def get_trajectory(
    trajectory_id: uuid.UUID,
    db: Session = Depends(deps.get_db),
    current_user=Depends(deps.get_current_active_user),
):
    """All experiences in a trajectory."""
    experiences = rl_experience_service.get_trajectory(db, trajectory_id)
    return [RLExperienceInDB.model_validate(e) for e in experiences if e.tenant_id == current_user.tenant_id]


@router.post("/feedback")
def submit_feedback(
    feedback: RLFeedbackSubmit,
    db: Session = Depends(deps.get_db),
    current_user=Depends(deps.get_current_active_user),
):
    """Submit explicit feedback (thumbs up/down, flags, ratings)."""
    updated = rl_reward_service.process_explicit_feedback(
        db, current_user.tenant_id,
        feedback.trajectory_id, feedback.feedback_type,
        feedback.step_index, feedback.value,
    )
    return {"updated_experiences": updated}


@router.get("/decision-points")
def list_decision_points(
    db: Session = Depends(deps.get_db),
    current_user=Depends(deps.get_current_active_user),
):
    """List all decision points with current scores and experience counts."""
    tid = current_user.tenant_id
    policies = db.query(RLPolicyState).filter(RLPolicyState.tenant_id == tid).all()
    return [RLPolicyStateInDB.model_validate(p) for p in policies]


@router.get("/decision-points/{name}")
def get_decision_point(
    name: str,
    db: Session = Depends(deps.get_db),
    current_user=Depends(deps.get_current_active_user),
):
    """Detail for one decision point."""
    policy = (
        db.query(RLPolicyState)
        .filter(RLPolicyState.tenant_id == current_user.tenant_id, RLPolicyState.decision_point == name)
        .first()
    )
    recent_exp = rl_experience_service.get_experiences_paginated(
        db, current_user.tenant_id, name, limit=20
    )
    return {
        "policy": RLPolicyStateInDB.model_validate(policy) if policy else None,
        "recent_experiences": [RLExperienceInDB.model_validate(e) for e in recent_exp],
    }


@router.get("/reviews/pending")
def get_pending_reviews(
    decision_point: Optional[str] = None,
    page: int = Query(1, ge=1),
    per_page: int = Query(50, ge=1, le=200),
    db: Session = Depends(deps.get_db),
    current_user=Depends(deps.get_current_active_user),
):
    """Admin review queue sorted by uncertainty (unrewarded experiences)."""
    skip = (page - 1) * per_page
    q = db.query(RLExperience).filter(
        RLExperience.tenant_id == current_user.tenant_id,
        RLExperience.reward.is_(None),
        RLExperience.archived_at.is_(None),
    )
    if decision_point:
        q = q.filter(RLExperience.decision_point == decision_point)
    experiences = q.order_by(RLExperience.created_at.desc()).offset(skip).limit(per_page).all()
    return [RLExperienceInDB.model_validate(e) for e in experiences]


@router.post("/reviews/{experience_id}/rate")
def rate_experience(
    experience_id: uuid.UUID,
    rating: str = Query(..., pattern="^(good|acceptable|poor)$"),
    db: Session = Depends(deps.get_db),
    current_user=Depends(deps.get_current_active_user),
):
    """Admin rates an experience."""
    result = rl_reward_service.process_admin_review(
        db, current_user.tenant_id, experience_id, rating
    )
    if not result:
        return {"error": "Experience not found or invalid rating"}
    return RLExperienceInDB.model_validate(result)


@router.get("/settings")
def get_settings(
    db: Session = Depends(deps.get_db),
    current_user=Depends(deps.get_current_active_user),
):
    """Get tenant RL settings."""
    features = db.query(TenantFeatures).filter(TenantFeatures.tenant_id == current_user.tenant_id).first()
    return {
        "rl_enabled": features.rl_enabled if features else False,
        "settings": features.rl_settings if features and features.rl_settings else {},
    }


@router.put("/settings")
def update_settings(
    settings: RLSettingsUpdate,
    db: Session = Depends(deps.get_db),
    current_user=Depends(deps.get_current_active_user),
):
    """Update tenant RL settings."""
    features = db.query(TenantFeatures).filter(TenantFeatures.tenant_id == current_user.tenant_id).first()
    if not features:
        return {"error": "Tenant features not found"}

    current = features.rl_settings or {}
    updates = settings.model_dump(exclude_none=True)
    current.update(updates)
    features.rl_settings = current
    db.commit()
    return {"rl_enabled": features.rl_enabled, "settings": features.rl_settings}


@router.get("/policy/versions")
def get_policy_versions(
    db: Session = Depends(deps.get_db),
    current_user=Depends(deps.get_current_active_user),
):
    """Policy version history."""
    policies = (
        db.query(RLPolicyState)
        .filter(RLPolicyState.tenant_id == current_user.tenant_id)
        .order_by(RLPolicyState.last_updated_at.desc())
        .all()
    )
    return [RLPolicyStateInDB.model_validate(p) for p in policies]


@router.post("/policy/rollback")
def rollback_policy(
    decision_point: str,
    version: str,
    db: Session = Depends(deps.get_db),
    current_user=Depends(deps.get_current_active_user),
):
    """Restore a previous policy version."""
    policy = (
        db.query(RLPolicyState)
        .filter(RLPolicyState.tenant_id == current_user.tenant_id, RLPolicyState.decision_point == decision_point)
        .first()
    )
    if not policy:
        return {"error": "Policy not found"}
    policy.version = version
    policy.last_updated_at = datetime.utcnow()
    db.commit()
    return RLPolicyStateInDB.model_validate(policy)


@router.get("/experiments")
def list_experiments(
    db: Session = Depends(deps.get_db),
    current_user=Depends(deps.get_current_active_user),
):
    """Active and recent exploration actions with outcomes."""
    explorations = (
        db.query(RLExperience)
        .filter(
            RLExperience.tenant_id == current_user.tenant_id,
            RLExperience.exploration == True,
            RLExperience.archived_at.is_(None),
        )
        .order_by(RLExperience.created_at.desc())
        .limit(100)
        .all()
    )
    return [RLExperienceInDB.model_validate(e) for e in explorations]


@router.post("/experiments/trigger")
def trigger_experiment(
    decision_point: str,
    db: Session = Depends(deps.get_db),
    current_user=Depends(deps.get_current_active_user),
):
    """Manually trigger exploration for a decision point."""
    features = db.query(TenantFeatures).filter(TenantFeatures.tenant_id == current_user.tenant_id).first()
    if not features:
        return {"error": "Tenant features not found"}
    current = features.rl_settings or {}
    overrides = current.get("per_decision_overrides", {})
    overrides[decision_point] = {"exploration_rate": 1.0, "triggered_at": datetime.utcnow().isoformat()}
    current["per_decision_overrides"] = overrides
    features.rl_settings = current
    db.commit()
    return {"decision_point": decision_point, "exploration_rate": 1.0, "status": "triggered"}


@router.post("/reviews/batch-rate")
def batch_rate_experiences(
    ratings: list,
    db: Session = Depends(deps.get_db),
    current_user=Depends(deps.get_current_active_user),
):
    """Batch rate multiple experiences. Body: [{experience_id, rating}]."""
    results = []
    for item in ratings:
        result = rl_reward_service.process_admin_review(
            db, current_user.tenant_id, uuid.UUID(item["experience_id"]), item["rating"]
        )
        results.append({"experience_id": item["experience_id"], "success": result is not None})
    return {"rated": len([r for r in results if r["success"]]), "results": results}


@router.get("/export")
def export_experiences(
    decision_point: Optional[str] = None,
    db: Session = Depends(deps.get_db),
    current_user=Depends(deps.get_current_active_user),
):
    """Export experience data as CSV."""
    q = db.query(RLExperience).filter(
        RLExperience.tenant_id == current_user.tenant_id,
        RLExperience.archived_at.is_(None),
    )
    if decision_point:
        q = q.filter(RLExperience.decision_point == decision_point)
    experiences = q.order_by(RLExperience.created_at.desc()).limit(10000).all()

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["id", "trajectory_id", "step_index", "decision_point", "reward", "reward_source", "exploration", "created_at"])
    for e in experiences:
        writer.writerow([str(e.id), str(e.trajectory_id), e.step_index, e.decision_point, e.reward, e.reward_source, e.exploration, e.created_at.isoformat()])
    output.seek(0)
    return StreamingResponse(output, media_type="text/csv", headers={"Content-Disposition": "attachment; filename=rl_experiences.csv"})
