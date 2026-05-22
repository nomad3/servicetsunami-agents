"""Platform-admin escape endpoints for the Platform Safety Floor.

Per design §7 + §12 (Luna sign-off):

  - POST /api/v1/admin/platform-safety/escape — create a time-boxed
    grant scoped to (user_id, session_id, category). Requires
    superuser + reason. NEVER operator-invocable.

  - POST /api/v1/admin/platform-safety/escape/{grant_id}/revoke —
    manual revoke before expiry. Audited.

  - GET /api/v1/admin/platform-safety/escape — list recent grants
    + audit events for the platform-admin dashboard.

All routes superuser-only. Grants do NOT appear on any operator
surface (no operator listing, no operator counter row, no chat
visibility).
"""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api import deps
from app.models.platform_safety_escape import (
    PlatformSafetyAdminAudit,
    PlatformSafetyEscapeGrant,
)
from app.models.user import User
from app.services import platform_safety_escape


router = APIRouter()


# ── Request / response models ────────────────────────────────────────


class CreateGrantBody(BaseModel):
    """Body for POST /admin/platform-safety/escape.

    Fields:
      - tenant_id: which tenant the grant applies to. Required.
      - scoped_user_id: which user the grant covers. Required —
        prevents accidental tenant-wide relaxation.
      - scoped_session_id: which chat session the grant covers.
        Required — prevents the grant from leaking across a user's
        other sessions.
      - category: which category to relax. '*' = wildcard (only for
        corpus-curation contexts where the admin needs to see what
        would block at ANY tier).
      - reason: free-text justification. Required + min 8 chars to
        encourage real audit content.
      - duration_seconds: bounded [60, 86400] by the service.
    """

    tenant_id: uuid.UUID
    scoped_user_id: uuid.UUID
    scoped_session_id: uuid.UUID
    category: str = Field(..., min_length=1, max_length=64)
    reason: str = Field(..., min_length=8, max_length=1000)
    duration_seconds: int = Field(
        default=platform_safety_escape.ESCAPE_DEFAULT_SECONDS,
        ge=platform_safety_escape.ESCAPE_MIN_SECONDS,
        le=platform_safety_escape.ESCAPE_MAX_SECONDS,
    )


class GrantOut(BaseModel):
    id: uuid.UUID
    tenant_id: uuid.UUID
    issued_by_user_id: uuid.UUID
    scoped_user_id: uuid.UUID
    scoped_session_id: uuid.UUID
    category: str
    reason: str
    created_at: datetime
    expires_at: datetime
    revoked_at: Optional[datetime] = None

    class Config:
        from_attributes = True


class AdminAuditOut(BaseModel):
    id: uuid.UUID
    event_type: str
    tenant_id: uuid.UUID
    actor_user_id: Optional[uuid.UUID]
    grant_id: Optional[uuid.UUID]
    category: Optional[str]
    detail: str
    created_at: datetime

    class Config:
        from_attributes = True


# ── Routes ───────────────────────────────────────────────────────────


@router.post(
    "/admin/platform-safety/escape",
    response_model=GrantOut,
    status_code=201,
)
def create_escape_grant(
    body: CreateGrantBody,
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(deps.require_superuser),
):
    """Open a time-boxed escape grant. Requires superuser JWT (the
    frontend should additionally gate on 2FA per design §7; the
    backend assumes the JWT carries that proof)."""
    grant = platform_safety_escape.create_grant(
        db,
        tenant_id=body.tenant_id,
        issued_by_user_id=current_user.id,
        scoped_user_id=body.scoped_user_id,
        scoped_session_id=body.scoped_session_id,
        category=body.category,
        reason=body.reason,
        duration_seconds=body.duration_seconds,
    )
    if grant is None:
        raise HTTPException(
            status_code=400,
            detail=(
                "Grant creation rejected — check that reason is set "
                "and category is valid (or '*' for wildcard)."
            ),
        )
    return grant


@router.post(
    "/admin/platform-safety/escape/{grant_id}/revoke",
    status_code=204,
)
def revoke_escape_grant(
    grant_id: uuid.UUID,
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(deps.require_superuser),
):
    """Manually revoke an unexpired grant. Idempotent."""
    ok = platform_safety_escape.revoke_grant(
        db, grant_id=grant_id, actor_user_id=current_user.id,
    )
    if not ok:
        raise HTTPException(status_code=404, detail="grant not found")


@router.get(
    "/admin/platform-safety/escape",
    response_model=list[GrantOut],
)
def list_escape_grants(
    limit: int = Query(default=50, ge=1, le=500),
    db: Session = Depends(deps.get_db),
    _current_user: User = Depends(deps.require_superuser),
):
    """Recent grants for the platform-admin dashboard. Newest first.
    Always returns both active + expired + revoked grants for audit
    review."""
    rows = (
        db.query(PlatformSafetyEscapeGrant)
        .order_by(PlatformSafetyEscapeGrant.created_at.desc())
        .limit(limit)
        .all()
    )
    return rows


@router.get(
    "/admin/platform-safety/escape/audit",
    response_model=list[AdminAuditOut],
)
def list_escape_audit(
    limit: int = Query(default=200, ge=1, le=2000),
    db: Session = Depends(deps.get_db),
    _current_user: User = Depends(deps.require_superuser),
):
    """Recent admin audit events (grant_created / grant_revoked /
    block_in_window). Newest first."""
    rows = (
        db.query(PlatformSafetyAdminAudit)
        .order_by(PlatformSafetyAdminAudit.created_at.desc())
        .limit(limit)
        .all()
    )
    return rows
