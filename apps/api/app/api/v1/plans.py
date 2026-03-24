"""Plan runtime API endpoints."""

import uuid
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, get_db
from app.models.user import User
from app.schemas.plan import (
    PlanCreate,
    PlanDetailInDB,
    PlanEventInDB,
    PlanInDB,
    PlanUpdate,
)
from app.services import plan_service

router = APIRouter()


@router.get("", response_model=List[PlanInDB])
def list_plans(
    owner_agent_slug: Optional[str] = Query(default=None),
    plan_status: Optional[str] = Query(default=None, alias="status"),
    goal_id: Optional[uuid.UUID] = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """List plans for the current tenant."""
    return plan_service.list_plans(
        db,
        tenant_id=current_user.tenant_id,
        owner_agent_slug=owner_agent_slug,
        status=plan_status,
        goal_id=goal_id,
        limit=limit,
    )


@router.post("", response_model=PlanInDB, status_code=status.HTTP_201_CREATED)
def create_plan(
    plan_in: PlanCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Create a new plan with steps and assumptions."""
    try:
        return plan_service.create_plan(db, current_user.tenant_id, plan_in)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))


@router.get("/{plan_id}", response_model=PlanDetailInDB)
def get_plan_detail(
    plan_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Get a plan with its steps, assumptions, and recent events."""
    detail = plan_service.get_plan_detail(db, current_user.tenant_id, plan_id)
    if not detail:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Plan not found")
    return PlanDetailInDB(
        **PlanInDB.model_validate(detail["plan"]).model_dump(),
        steps=detail["steps"],
        assumptions=detail["assumptions"],
        recent_events=detail["recent_events"],
    )


@router.patch("/{plan_id}", response_model=PlanInDB)
def update_plan(
    plan_id: uuid.UUID,
    plan_in: PlanUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Update plan status, budget, or metadata."""
    plan = plan_service.update_plan(db, current_user.tenant_id, plan_id, plan_in)
    if not plan:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Plan not found")
    return plan


@router.post("/{plan_id}/advance", response_model=dict)
def advance_plan_step(
    plan_id: uuid.UUID,
    step_output: Optional[dict] = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Mark current step as completed and advance to next."""
    step = plan_service.advance_step(db, current_user.tenant_id, plan_id, step_output)
    if not step:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Cannot advance: plan not executing or no current step")
    return {"step_completed": str(step.id), "step_index": step.step_index}


@router.post("/{plan_id}/fail", response_model=dict)
def fail_plan_step(
    plan_id: uuid.UUID,
    error: str = Query(...),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Mark current step and plan as failed."""
    step = plan_service.fail_step(db, current_user.tenant_id, plan_id, error)
    if not step:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Cannot fail: plan not executing or no current step")
    return {"step_failed": str(step.id), "error": error}


@router.get("/{plan_id}/events", response_model=List[PlanEventInDB])
def list_plan_events(
    plan_id: uuid.UUID,
    limit: int = Query(default=50, ge=1, le=200),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """List audit trail events for a plan."""
    return plan_service.list_plan_events(db, current_user.tenant_id, plan_id, limit=limit)
