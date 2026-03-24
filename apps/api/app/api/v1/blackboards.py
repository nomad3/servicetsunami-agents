"""Blackboard collaboration API endpoints."""

import uuid
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, get_db
from app.models.user import User
from app.schemas.blackboard import (
    BlackboardCreate,
    BlackboardDetailInDB,
    BlackboardEntryCreate,
    BlackboardEntryInDB,
    BlackboardInDB,
    ResolveEntryRequest,
)
from app.services import blackboard_service

router = APIRouter()


@router.get("", response_model=List[BlackboardInDB])
def list_blackboards(
    board_status: Optional[str] = Query(default=None, alias="status"),
    limit: int = Query(default=50, ge=1, le=200),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """List blackboards for the current tenant."""
    return blackboard_service.list_blackboards(
        db, current_user.tenant_id, status=board_status, limit=limit
    )


@router.post("", response_model=BlackboardInDB, status_code=status.HTTP_201_CREATED)
def create_blackboard(
    board_in: BlackboardCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Create a new blackboard for task collaboration."""
    try:
        return blackboard_service.create_blackboard(db, current_user.tenant_id, board_in)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))


@router.get("/{board_id}", response_model=BlackboardDetailInDB)
def get_blackboard_detail(
    board_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Get blackboard with all entries."""
    detail = blackboard_service.get_blackboard_detail(db, current_user.tenant_id, board_id)
    if not detail:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Blackboard not found")
    return BlackboardDetailInDB(
        **BlackboardInDB.model_validate(detail["board"]).model_dump(),
        entries=detail["entries"],
    )


@router.post("/{board_id}/entries", response_model=BlackboardEntryInDB, status_code=status.HTTP_201_CREATED)
def add_entry(
    board_id: uuid.UUID,
    entry_in: BlackboardEntryCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Append an entry to the blackboard (append-only)."""
    try:
        entry = blackboard_service.add_entry(db, current_user.tenant_id, board_id, entry_in)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    if not entry:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Blackboard not found or not active")
    return entry


@router.get("/{board_id}/entries/active", response_model=List[BlackboardEntryInDB])
def get_active_entries(
    board_id: uuid.UUID,
    entry_type: Optional[str] = Query(default=None),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Get current working state (non-superseded, non-resolved entries)."""
    return blackboard_service.get_active_entries(
        db, current_user.tenant_id, board_id, entry_type=entry_type
    )


@router.get("/{board_id}/disagreements", response_model=List[BlackboardEntryInDB])
def get_disagreements(
    board_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Get unresolved disagreements on the blackboard."""
    return blackboard_service.get_disagreements(db, current_user.tenant_id, board_id)


@router.post("/{board_id}/entries/{entry_id}/resolve", response_model=BlackboardEntryInDB)
def resolve_entry(
    board_id: uuid.UUID,
    entry_id: uuid.UUID,
    request: ResolveEntryRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Resolve an entry (authority-checked). Returns the new resolution entry.

    The agent's role is derived from agent_identity_profiles (server-side),
    not from the request. If no profile exists, the agent defaults to
    'contributor' (lowest authority).
    """
    from app.services import agent_identity_service

    # Server-side role lookup — don't trust client-supplied role
    profile = agent_identity_service.get_profile(
        db, current_user.tenant_id, request.resolved_by_agent
    )
    server_role = "contributor"
    if profile and profile.role:
        # Map identity profile roles to blackboard authority roles
        role_lower = profile.role.lower()
        for authority_role in ("auditor", "synthesizer", "verifier", "critic", "executor", "planner", "researcher"):
            if authority_role in role_lower:
                server_role = authority_role
                break

    try:
        entry = blackboard_service.resolve_entry(
            db, current_user.tenant_id, board_id, entry_id,
            resolution_status=request.resolution_status.value,
            resolved_by_agent=request.resolved_by_agent,
            resolved_by_role=server_role,
            resolution_reason=request.resolution_reason,
            authenticated_user_id=current_user.id,
        )
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(e))
    if not entry:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Entry not found")
    return entry


@router.get("/{board_id}/diff", response_model=List[BlackboardEntryInDB])
def get_version_diff(
    board_id: uuid.UUID,
    from_version: int = Query(..., ge=0),
    to_version: Optional[int] = Query(default=None),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Get entries added between two versions (for replay/diff)."""
    return blackboard_service.get_version_diff(
        db, current_user.tenant_id, board_id,
        from_version=from_version, to_version=to_version,
    )
