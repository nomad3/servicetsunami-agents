"""World state assertion and snapshot API endpoints."""

import uuid
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, get_db
from app.models.user import User
from app.schemas.world_state import (
    WorldStateAssertionCreate,
    WorldStateAssertionInDB,
    WorldStateSnapshotInDB,
)
from app.services import world_state_service

router = APIRouter()


@router.get("/assertions", response_model=List[WorldStateAssertionInDB])
def list_assertions(
    subject_slug: Optional[str] = Query(default=None),
    assertion_status: Optional[str] = Query(default=None, alias="status"),
    limit: int = Query(default=100, ge=1, le=500),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """List world state assertions, defaulting to active only."""
    return world_state_service.list_assertions(
        db,
        tenant_id=current_user.tenant_id,
        subject_slug=subject_slug,
        status=assertion_status,
        limit=limit,
    )


@router.post("/assertions", response_model=WorldStateAssertionInDB, status_code=status.HTTP_201_CREATED)
def create_assertion(
    assertion_in: WorldStateAssertionCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Assert a state claim. Supersedes prior active assertion for same subject+attribute."""
    try:
        return world_state_service.assert_state(
            db,
            tenant_id=current_user.tenant_id,
            assertion_in=assertion_in,
        )
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))


@router.get("/assertions/unstable", response_model=List[WorldStateAssertionInDB])
def list_unstable_assertions(
    confidence_threshold: float = Query(default=0.5, ge=0.0, le=1.0),
    limit: int = Query(default=20, ge=1, le=100),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Find assertions with low confidence or nearing expiry."""
    return world_state_service.get_unstable_assertions(
        db,
        tenant_id=current_user.tenant_id,
        confidence_threshold=confidence_threshold,
        limit=limit,
    )


@router.get("/assertions/{assertion_id}", response_model=WorldStateAssertionInDB)
def get_assertion(
    assertion_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Get a single assertion by ID."""
    assertion = world_state_service.get_assertion(db, current_user.tenant_id, assertion_id)
    if not assertion:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Assertion not found")
    return assertion


@router.get("/snapshots", response_model=List[WorldStateSnapshotInDB])
def list_snapshots(
    limit: int = Query(default=100, ge=1, le=500),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """List all world state snapshots for the current tenant."""
    return world_state_service.list_snapshots(db, current_user.tenant_id, limit=limit)


@router.get("/snapshots/{subject_slug}", response_model=WorldStateSnapshotInDB)
def get_snapshot(
    subject_slug: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Get the current projected state for a subject."""
    snapshot = world_state_service.get_snapshot(db, current_user.tenant_id, subject_slug)
    if not snapshot:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Snapshot not found")
    return snapshot
