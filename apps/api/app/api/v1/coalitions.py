"""Coalition routing API endpoints."""

import uuid
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, get_db
from app.models.user import User
from app.schemas.coalition import (
    CoalitionOutcomeCreate,
    CoalitionOutcomeInDB,
    CoalitionTemplateCreate,
    CoalitionTemplateInDB,
)
from app.services import coalition_service

router = APIRouter()


@router.get("/templates", response_model=List[CoalitionTemplateInDB])
def list_templates(
    task_type: Optional[str] = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """List coalition templates, optionally filtered by task type."""
    return coalition_service.list_templates(db, current_user.tenant_id, task_type=task_type, limit=limit)


@router.post("/templates", response_model=CoalitionTemplateInDB, status_code=status.HTTP_201_CREATED)
def create_template(
    template_in: CoalitionTemplateCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Create a reusable coalition template (team shape)."""
    try:
        return coalition_service.create_template(db, current_user.tenant_id, template_in)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))


@router.get("/templates/{template_id}", response_model=CoalitionTemplateInDB)
def get_template(
    template_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Get a coalition template."""
    template = coalition_service.get_template(db, current_user.tenant_id, template_id)
    if not template:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Template not found")
    return template


@router.get("/recommend", response_model=List[dict])
def recommend_coalition(
    task_type: str = Query(...),
    min_uses: int = Query(default=2, ge=1),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Recommend the best coalition for a task type based on historical outcomes."""
    return coalition_service.recommend_coalition(
        db, current_user.tenant_id, task_type=task_type, min_uses=min_uses
    )


@router.post("/outcomes", response_model=CoalitionOutcomeInDB, status_code=status.HTTP_201_CREATED)
def record_outcome(
    outcome_in: CoalitionOutcomeCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Record a coalition outcome and update template stats."""
    try:
        return coalition_service.record_outcome(db, current_user.tenant_id, outcome_in)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))


@router.get("/outcomes", response_model=List[CoalitionOutcomeInDB])
def list_outcomes(
    task_type: Optional[str] = Query(default=None),
    template_id: Optional[uuid.UUID] = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """List coalition outcomes."""
    return coalition_service.list_outcomes(
        db, current_user.tenant_id, task_type=task_type, template_id=template_id, limit=limit
    )
