"""Tenant-scoped goal record API endpoints."""

import uuid
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, get_db
from app.models.user import User
from app.schemas.goal_record import GoalRecordCreate, GoalRecordInDB, GoalRecordUpdate
from app.services import goal_service

router = APIRouter()


@router.get("", response_model=List[GoalRecordInDB])
def list_goals(
    owner_agent_slug: Optional[str] = Query(default=None),
    state: Optional[str] = Query(default=None),
    limit: int = Query(default=100, ge=1, le=500),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """List goals for the current tenant, optionally filtered by agent or state."""
    return goal_service.list_goals(
        db,
        tenant_id=current_user.tenant_id,
        owner_agent_slug=owner_agent_slug,
        state=state,
        limit=limit,
    )


@router.post("", response_model=GoalRecordInDB, status_code=status.HTTP_201_CREATED)
def create_goal(
    goal_in: GoalRecordCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Create a new goal record."""
    try:
        return goal_service.create_goal(
            db,
            tenant_id=current_user.tenant_id,
            goal_in=goal_in,
            created_by=current_user.id,
        )
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))


@router.get("/{goal_id}", response_model=GoalRecordInDB)
def get_goal(
    goal_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Get a single goal by ID."""
    goal = goal_service.get_goal(db, current_user.tenant_id, goal_id)
    if not goal:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Goal not found")
    return goal


@router.patch("/{goal_id}", response_model=GoalRecordInDB)
def update_goal(
    goal_id: uuid.UUID,
    goal_in: GoalRecordUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Update a goal record (state transitions, progress, etc.)."""
    try:
        goal = goal_service.update_goal(db, current_user.tenant_id, goal_id, goal_in)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    if not goal:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Goal not found")
    return goal


@router.delete("/{goal_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_goal(
    goal_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Delete a goal record."""
    deleted = goal_service.delete_goal(db, current_user.tenant_id, goal_id)
    if not deleted:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Goal not found")
    return None
