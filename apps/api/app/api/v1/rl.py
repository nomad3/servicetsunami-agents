import uuid
import csv
import io
from datetime import datetime
from typing import Optional
from fastapi import APIRouter, Depends, Header, HTTPException, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.api import deps
from app.core.config import settings
from app.models.rl_experience import RLExperience
from app.models.rl_policy_state import RLPolicyState
from app.models.tenant_features import TenantFeatures
from app.schemas.rl_experience import RLExperienceInDB, RLFeedbackSubmit
from app.schemas.rl_policy_state import RLPolicyStateInDB, RLSettingsUpdate
from app.services import rl_experience_service, rl_reward_service

router = APIRouter()


def _verify_internal_key(
    x_internal_key: Optional[str] = Header(None, alias="X-Internal-Key"),
):
    if x_internal_key not in (settings.API_INTERNAL_KEY, settings.MCP_API_KEY):
        raise HTTPException(status_code=401, detail="Invalid internal key")


class InternalExperienceCreate(BaseModel):
    tenant_id: str
    decision_point: str
    state: dict = {}
    action: dict = {}
    state_text: str = ""


@router.post("/internal/experience")
def create_internal_experience(
    payload: InternalExperienceCreate,
    db: Session = Depends(deps.get_db),
    _auth: None = Depends(_verify_internal_key),
):
    """Create an RL experience from internal services (code-worker, MCP tools)."""
    tid = uuid.UUID(payload.tenant_id)
    trajectory_id = uuid.uuid4()
    rl_experience_service.log_experience(
        db,
        tenant_id=tid,
        trajectory_id=trajectory_id,
        step_index=0,
        decision_point=payload.decision_point,
        state=payload.state,
        action=payload.action,
        state_text=payload.state_text,
    )
    return {"status": "logged", "trajectory_id": str(trajectory_id)}


DP_DESCRIPTIONS = {
    "agent_selection": "Which agent team handles each user request (supervisor routing)",
    "tool_selection": "Which tools the agent chooses for a given task",
    "response_generation": "Quality and relevance of the agent's response to the user",
    "skill_routing": "Which skill is selected for task execution",
    "memory_recall": "What memories are recalled for context",
    "triage_classification": "How incoming items are prioritized and classified",
    "orchestration_routing": "How tasks are routed across the orchestration engine",
}


def _get_dp_description(name: str) -> str:
    return DP_DESCRIPTIONS.get(name, f"Decision point: {name}")


def _format_action(action: dict) -> str:
    """Format an RL action dict into a human-readable string."""
    if not action:
        return ""
    if "selected_agent" in action:
        return f"Routed to {action['selected_agent']}"
    if "tools_used" in action:
        tools = action["tools_used"]
        return f"Used {', '.join(tools[:3])}" + (f" +{len(tools)-3} more" if len(tools) > 3 else "")
    if "response_preview" in action:
        return action["response_preview"][:80]
    return str(action)[:80]


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

    # Top decision points with avg reward and count
    from sqlalchemy import func
    top_dp = (
        db.query(
            RLExperience.decision_point,
            func.count(RLExperience.id).label("count"),
            func.avg(RLExperience.reward).label("avg_reward"),
        )
        .filter(RLExperience.tenant_id == tid, RLExperience.archived_at.is_(None))
        .group_by(RLExperience.decision_point)
        .order_by(func.count(RLExperience.id).desc())
        .limit(5)
        .all()
    )

    # Recent experiences for activity feed
    recent = (
        db.query(RLExperience)
        .filter(RLExperience.tenant_id == tid, RLExperience.archived_at.is_(None))
        .order_by(RLExperience.created_at.desc())
        .limit(10)
        .all()
    )

    return {
        "total_experiences": total,
        "avg_reward": round(avg_reward, 3),
        "avg_reward_30d": round(avg_reward_30d, 3) if avg_reward_30d is not None else None,
        "exploration_rate": exploration_rate,
        "policy_version": latest_policy.version if latest_policy else "v0",
        "policy_updated_at": latest_policy.last_updated_at.isoformat() if latest_policy and latest_policy.last_updated_at else None,
        "top_decision_points": [
            {
                "name": dp,
                "experience_count": count,
                "avg_reward": round(float(ar), 3) if ar is not None else None,
            }
            for dp, count, ar in top_dp
        ],
        "recent_activity": [
            {
                "id": str(e.id),
                "decision_point": e.decision_point,
                "state_preview": (e.state or {}).get("user_message", "")[:100] if e.state else "",
                "action_preview": _format_action(e.action),
                "reward": e.reward,
                "reward_source": e.reward_source,
                "created_at": e.created_at.isoformat() if e.created_at else None,
            }
            for e in recent
        ],
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
    """List all decision points derived from actual experiences."""
    from sqlalchemy import func

    tid = current_user.tenant_id

    # Derive decision points from experience data (not the empty policy table)
    dp_stats = (
        db.query(
            RLExperience.decision_point,
            func.count(RLExperience.id).label("experience_count"),
            func.avg(RLExperience.reward).label("avg_reward"),
            func.min(RLExperience.created_at).label("first_seen"),
            func.max(RLExperience.created_at).label("last_seen"),
        )
        .filter(RLExperience.tenant_id == tid, RLExperience.archived_at.is_(None))
        .group_by(RLExperience.decision_point)
        .order_by(func.count(RLExperience.id).desc())
        .all()
    )

    # Check if there's a policy state for each
    policies = {
        p.decision_point: p
        for p in db.query(RLPolicyState).filter(RLPolicyState.tenant_id == tid).all()
    }

    features = db.query(TenantFeatures).filter(TenantFeatures.tenant_id == tid).first()
    default_exploration = features.rl_settings.get("exploration_rate", 0.1) if features and features.rl_settings else 0.1

    results = []
    for dp_name, exp_count, avg_rwd, first_seen, last_seen in dp_stats:
        policy = policies.get(dp_name)
        results.append({
            "id": str(policy.id) if policy else dp_name,
            "name": dp_name,
            "decision_point": dp_name,
            "experience_count": exp_count,
            "avg_reward": round(float(avg_rwd), 3) if avg_rwd is not None else None,
            "version": policy.version if policy else "v0",
            "exploration_rate": policy.exploration_rate if policy else default_exploration,
            "first_seen": first_seen.isoformat() if first_seen else None,
            "last_seen": last_seen.isoformat() if last_seen else None,
            "description": _get_dp_description(dp_name),
        })

    return results


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
    return [
        {
            "id": str(e.id),
            "decision_point": e.decision_point,
            "decision_point_name": e.decision_point,
            "action": _format_action(e.action),
            "context": (e.state or {}).get("user_message", "")[:150] if e.state else "",
            "outcome": _format_action(e.action),
            "reward": e.reward,
            "reward_source": e.reward_source,
            "created_at": e.created_at.isoformat() if e.created_at else None,
        }
        for e in experiences
    ]


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


@router.get("/platform-performance")
def get_platform_performance(
    min_experiences: int = Query(5, ge=1),
    db: Session = Depends(deps.get_db),
    current_user=Depends(deps.get_current_active_user),
):
    """Cross-learning: platform/agent/task_type performance breakdown.

    Groups RL experiences by platform, agent_slug, and task_type.
    Returns avg reward, count, and positive percentage for each tuple.
    Only includes tuples with more than min_experiences data points.
    """
    return rl_experience_service.get_platform_performance(
        db, current_user.tenant_id, min_experiences=min_experiences,
    )


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
