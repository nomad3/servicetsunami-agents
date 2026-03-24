"""Tenant-scoped commitment record API endpoints."""

import uuid
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, get_db
from app.models.user import User
from app.schemas.commitment_record import (
    CommitmentRecordCreate,
    CommitmentRecordInDB,
    CommitmentRecordUpdate,
)
from app.services import commitment_service

router = APIRouter()


@router.get("", response_model=List[CommitmentRecordInDB])
def list_commitments(
    owner_agent_slug: Optional[str] = Query(default=None),
    state: Optional[str] = Query(default=None),
    goal_id: Optional[uuid.UUID] = Query(default=None),
    limit: int = Query(default=100, ge=1, le=500),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """List commitments for the current tenant."""
    return commitment_service.list_commitments(
        db,
        tenant_id=current_user.tenant_id,
        owner_agent_slug=owner_agent_slug,
        state=state,
        goal_id=goal_id,
        limit=limit,
    )


@router.get("/overdue", response_model=List[CommitmentRecordInDB])
def list_overdue_commitments(
    owner_agent_slug: Optional[str] = Query(default=None),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """List commitments that are past their due date and still open."""
    return commitment_service.list_overdue_commitments(
        db,
        tenant_id=current_user.tenant_id,
        owner_agent_slug=owner_agent_slug,
    )


@router.post("", response_model=CommitmentRecordInDB, status_code=status.HTTP_201_CREATED)
def create_commitment(
    commitment_in: CommitmentRecordCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Create a new commitment record."""
    try:
        return commitment_service.create_commitment(
            db,
            tenant_id=current_user.tenant_id,
            commitment_in=commitment_in,
            created_by=current_user.id,
        )
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))


@router.get("/{commitment_id}", response_model=CommitmentRecordInDB)
def get_commitment(
    commitment_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Get a single commitment by ID."""
    commitment = commitment_service.get_commitment(db, current_user.tenant_id, commitment_id)
    if not commitment:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Commitment not found")
    return commitment


@router.patch("/{commitment_id}", response_model=CommitmentRecordInDB)
def update_commitment(
    commitment_id: uuid.UUID,
    commitment_in: CommitmentRecordUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Update a commitment (state transitions, progress, etc.)."""
    try:
        commitment = commitment_service.update_commitment(
            db, current_user.tenant_id, commitment_id, commitment_in
        )
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    if not commitment:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Commitment not found")
    return commitment


@router.delete("/{commitment_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_commitment(
    commitment_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Delete a commitment record."""
    deleted = commitment_service.delete_commitment(db, current_user.tenant_id, commitment_id)
    if not deleted:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Commitment not found")
    return None
