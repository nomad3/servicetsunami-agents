"""Unsupervised learning API — skill gaps, simulation results, proactive actions, feedback."""

import uuid
from datetime import date, datetime
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy.orm import Session
from sqlalchemy import text

from app.api.deps import get_current_user, get_db
from app.models.user import User

router = APIRouter()


# --- Pydantic schemas ---

class SkillGapOut(BaseModel):
    id: uuid.UUID
    gap_type: str
    description: str
    industry: Optional[str]
    frequency: int
    severity: str
    proposed_fix: Optional[str]
    status: str
    detected_at: datetime
    resolved_at: Optional[datetime]

    class Config:
        from_attributes = True


class SkillGapStatusUpdate(BaseModel):
    status: str  # detected, acknowledged, in_progress, resolved


class SimulationResultOut(BaseModel):
    id: uuid.UUID
    scenario_id: uuid.UUID
    scenario_type: Optional[str]
    message: Optional[str]
    response_text: Optional[str]
    quality_score: Optional[float]
    dimension_scores: Optional[dict]
    failure_type: Optional[str]
    failure_detail: Optional[str]
    executed_at: datetime


class ProactiveActionOut(BaseModel):
    id: uuid.UUID
    agent_slug: str
    action_type: str
    trigger_type: str
    target_ref: Optional[str]
    priority: str
    content: str
    channel: str
    status: str
    scheduled_at: Optional[datetime]
    sent_at: Optional[datetime]
    created_at: datetime

    class Config:
        from_attributes = True


class FeedbackIn(BaseModel):
    content: str
    feedback_type: str = "direction"
    report_id: Optional[str] = None
    candidate_id: Optional[uuid.UUID] = None


class FeedbackOut(BaseModel):
    id: uuid.UUID
    feedback_type: str
    content: str
    parsed_intent: Optional[str]
    applied: bool
    created_at: datetime

    class Config:
        from_attributes = True


# --- Skill Gaps ---

@router.get("/skill-gaps", response_model=List[SkillGapOut])
def list_skill_gaps(
    gap_status: Optional[str] = Query(default=None, alias="status"),
    severity: Optional[str] = Query(default=None),
    industry: Optional[str] = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """List skill gaps detected from simulation failures."""
    from app.models.simulation import SkillGap

    q = db.query(SkillGap).filter(SkillGap.tenant_id == current_user.tenant_id)

    if gap_status:
        q = q.filter(SkillGap.status == gap_status)
    if severity:
        q = q.filter(SkillGap.severity == severity)
    if industry:
        q = q.filter(SkillGap.industry == industry)

    return q.order_by(
        SkillGap.frequency.desc(),
        SkillGap.detected_at.desc(),
    ).limit(limit).all()


@router.patch("/skill-gaps/{gap_id}", response_model=SkillGapOut)
def update_skill_gap_status(
    gap_id: uuid.UUID,
    update: SkillGapStatusUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Update a skill gap's status (e.g., mark as acknowledged or resolved)."""
    from app.models.simulation import SkillGap

    gap = (
        db.query(SkillGap)
        .filter(SkillGap.id == gap_id, SkillGap.tenant_id == current_user.tenant_id)
        .first()
    )
    if not gap:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Skill gap not found")

    valid_statuses = {"detected", "acknowledged", "in_progress", "resolved"}
    if update.status not in valid_statuses:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid status. Must be one of: {valid_statuses}",
        )

    gap.status = update.status
    if update.status == "resolved":
        gap.resolved_at = datetime.utcnow()

    db.commit()
    db.refresh(gap)
    return gap


# --- Simulation Results ---

@router.get("/simulation/results", response_model=List[SimulationResultOut])
def list_simulation_results(
    cycle_date: Optional[date] = Query(default=None),
    failure_type: Optional[str] = Query(default=None),
    min_score: Optional[float] = Query(default=None),
    max_score: Optional[float] = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """List simulation results for today or a specific cycle date."""
    target_date = cycle_date or date.today()

    rows = db.execute(text("""
        SELECT
            sr.id,
            sr.scenario_id,
            ss.scenario_type,
            ss.message,
            sr.response_text,
            CAST(sr.quality_score AS FLOAT) AS quality_score,
            sr.dimension_scores,
            sr.failure_type,
            sr.failure_detail,
            sr.executed_at
        FROM simulation_results sr
        JOIN simulation_scenarios ss ON ss.id = sr.scenario_id
        WHERE sr.tenant_id = CAST(:tid AS uuid)
          AND ss.cycle_date = :cdate
          AND sr.is_simulation = TRUE
          AND (:failure_type IS NULL OR sr.failure_type = :failure_type)
          AND (:min_score IS NULL OR sr.quality_score >= :min_score)
          AND (:max_score IS NULL OR sr.quality_score <= :max_score)
        ORDER BY sr.executed_at DESC
        LIMIT :lim
    """), {
        "tid": str(current_user.tenant_id),
        "cdate": target_date,
        "failure_type": failure_type,
        "min_score": min_score,
        "max_score": max_score,
        "lim": limit,
    }).fetchall()

    return [
        SimulationResultOut(
            id=r.id,
            scenario_id=r.scenario_id,
            scenario_type=r.scenario_type,
            message=r.message,
            response_text=r.response_text,
            quality_score=r.quality_score,
            dimension_scores=r.dimension_scores or {},
            failure_type=r.failure_type,
            failure_detail=r.failure_detail,
            executed_at=r.executed_at,
        )
        for r in rows
    ]


# --- Proactive Actions ---

@router.get("/proactive-actions", response_model=List[ProactiveActionOut])
def list_proactive_actions(
    action_status: Optional[str] = Query(default=None, alias="status"),
    action_type: Optional[str] = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """List proactive actions for the tenant."""
    from app.models.proactive_action import ProactiveAction

    q = db.query(ProactiveAction).filter(
        ProactiveAction.tenant_id == current_user.tenant_id
    )

    if action_status:
        q = q.filter(ProactiveAction.status == action_status)
    if action_type:
        q = q.filter(ProactiveAction.action_type == action_type)

    return q.order_by(ProactiveAction.created_at.desc()).limit(limit).all()


@router.patch("/proactive-actions/{action_id}/dismiss", response_model=ProactiveActionOut)
def dismiss_proactive_action(
    action_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Dismiss a proactive action."""
    from app.models.proactive_action import ProactiveAction

    action = (
        db.query(ProactiveAction)
        .filter(
            ProactiveAction.id == action_id,
            ProactiveAction.tenant_id == current_user.tenant_id,
        )
        .first()
    )
    if not action:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Proactive action not found")

    action.status = "dismissed"
    db.commit()
    db.refresh(action)
    return action


# --- Feedback ---

@router.post("/feedback", response_model=FeedbackOut, status_code=status.HTTP_201_CREATED)
def submit_feedback(
    feedback_in: FeedbackIn,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Submit human feedback on a learning report or candidate."""
    from app.models.feedback_record import FeedbackRecord

    valid_types = {"approval", "rejection", "direction", "correction"}
    if feedback_in.feedback_type not in valid_types:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid feedback_type. Must be one of: {valid_types}",
        )

    # Auto-detect parsed_intent
    parsed_intent = _infer_intent(feedback_in.content, feedback_in.feedback_type)

    record = FeedbackRecord(
        tenant_id=current_user.tenant_id,
        report_id=feedback_in.report_id,
        candidate_id=feedback_in.candidate_id,
        feedback_type=feedback_in.feedback_type,
        content=feedback_in.content,
        parsed_intent=parsed_intent,
        applied=False,
    )
    db.add(record)
    db.commit()
    db.refresh(record)
    return record


# --- Decision Point Config ---

class DecisionPointConfigOut(BaseModel):
    id: uuid.UUID
    decision_point: str
    exploration_rate: float
    exploration_mode: str
    target_platforms: Optional[list]
    updated_at: datetime

    class Config:
        from_attributes = True


class DecisionPointConfigUpdate(BaseModel):
    exploration_rate: Optional[float] = None
    exploration_mode: Optional[str] = None
    target_platforms: Optional[list] = None


@router.get("/decision-point-config", response_model=List[DecisionPointConfigOut])
def list_decision_point_configs(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """List per-decision-point exploration configuration for the tenant."""
    from app.models.decision_point_config import DecisionPointConfig

    return (
        db.query(DecisionPointConfig)
        .filter(DecisionPointConfig.tenant_id == current_user.tenant_id)
        .order_by(DecisionPointConfig.decision_point)
        .all()
    )


@router.patch("/decision-point-config/{decision_point}", response_model=DecisionPointConfigOut)
def update_decision_point_config(
    decision_point: str,
    update: DecisionPointConfigUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Update exploration rate/mode for a specific decision point (upsert)."""
    from app.models.decision_point_config import DecisionPointConfig

    valid_points = {"chat_response", "agent_routing", "code_task"}
    if decision_point not in valid_points:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid decision_point. Must be one of: {valid_points}",
        )

    config = (
        db.query(DecisionPointConfig)
        .filter(
            DecisionPointConfig.tenant_id == current_user.tenant_id,
            DecisionPointConfig.decision_point == decision_point,
        )
        .first()
    )

    if not config:
        config = DecisionPointConfig(
            tenant_id=current_user.tenant_id,
            decision_point=decision_point,
            exploration_rate=update.exploration_rate or 0.1,
            exploration_mode=update.exploration_mode or "balanced",
            target_platforms=update.target_platforms or [],
        )
        db.add(config)
    else:
        if update.exploration_rate is not None:
            if not (0.0 <= update.exploration_rate <= 1.0):
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="exploration_rate must be between 0.0 and 1.0",
                )
            config.exploration_rate = update.exploration_rate
        if update.exploration_mode is not None:
            valid_modes = {"off", "balanced", "targeted"}
            if update.exploration_mode not in valid_modes:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"exploration_mode must be one of: {valid_modes}",
                )
            config.exploration_mode = update.exploration_mode
        if update.target_platforms is not None:
            config.target_platforms = update.target_platforms
        config.updated_at = datetime.utcnow()

    db.commit()
    db.refresh(config)
    return config


# --- Private helpers ---

def _infer_intent(content: str, feedback_type: str) -> str:
    """Infer a machine-readable intent from feedback content."""
    content_lower = content.lower()
    if feedback_type == "approval":
        if "routing" in content_lower or "platform" in content_lower:
            return "approve_routing_change"
        return "general_approval"
    if feedback_type == "rejection":
        if "platform" in content_lower:
            return "reject_platform"
        if "rollback" in content_lower or "revert" in content_lower:
            return "request_rollback"
        return "general_rejection"
    if feedback_type == "correction":
        return "factual_correction"
    if feedback_type == "direction":
        return "exploration_direction"
    return "unclassified"
