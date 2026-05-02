"""Tenant user endpoints — self-service profile + member directory."""
import logging
import uuid
from typing import List, Literal, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy.orm import Session

from app.api import deps
from app.db.safe_ops import safe_rollback
from app.models.user import User as UserModel
from app.schemas import user as user_schema

logger = logging.getLogger(__name__)

router = APIRouter()


class ProfileUpdate(BaseModel):
    """Allowed fields for self-service profile update.

    Email and tenant_id are deliberately not editable here — those go
    through admin / re-registration flows. Password change goes through
    the existing /password-recovery + /reset-password flow.
    """

    full_name: Optional[str] = Field(
        default=None,
        max_length=255,
        description="Display name. Whitespace-only values are rejected; "
                    "send null to leave unchanged.",
    )


class UserBrief(BaseModel):
    """Trimmed user payload for the tenant member directory.

    Deliberately excludes:
      - The nested `tenant` relationship (would duplicate the full
        Tenant object on every member row in `GET /users` responses).
      - Raw `is_superuser` (mapped to a `role` string so admin-only
        SQL columns don't leak through the API surface).
      - Any password / hashed_password / token fields.

    Used by `GET /users` (member directory). The richer `User` schema
    with the tenant relationship is still used by `GET /users/me`
    where the caller is fetching their own profile and the tenant info
    is needed once for the Overview card.
    """

    id: uuid.UUID
    email: EmailStr
    full_name: Optional[str] = None
    is_active: bool = True
    role: Literal["admin", "member"]

    class Config:
        from_attributes = True


@router.get("/me", response_model=user_schema.User)
def read_users_me(
    current_user: UserModel = Depends(deps.get_current_active_user),
):
    """Get the currently authenticated user (with tenant relationship)."""
    return current_user


@router.put("/me", response_model=user_schema.User)
def update_users_me(
    payload: ProfileUpdate,
    *,
    db: Session = Depends(deps.get_db),
    current_user: UserModel = Depends(deps.get_current_active_user),
):
    """Update self-editable fields on the current user.

    Today only `full_name`. Email + password go through dedicated flows.

    Empty / whitespace-only `full_name` is rejected with 422 — clearing
    your name is not a supported operation. Send null to leave the
    field unchanged.
    """
    if payload.full_name is not None and not payload.full_name.strip():
        raise HTTPException(
            status_code=422,
            detail="full_name cannot be empty or whitespace-only.",
        )
    try:
        if payload.full_name is not None:
            current_user.full_name = payload.full_name.strip()
        db.commit()
        db.refresh(current_user)
        return current_user
    except Exception:
        # Log the underlying error so we can diagnose; safe_rollback only
        # logs at DEBUG. Without this, real DB / encoder failures vanish.
        logger.exception("Profile update failed for user %s", current_user.id)
        safe_rollback(db)
        raise HTTPException(status_code=500, detail="Could not update profile")


@router.get("", response_model=List[UserBrief])
def list_tenant_users(
    *,
    db: Session = Depends(deps.get_db),
    current_user: UserModel = Depends(deps.get_current_active_user),
):
    """List members of the current tenant (lean payload — no nested tenant).

    Any authenticated user can see who else is in their tenant — that's
    the same level of access already granted by the chat / agent /
    integration UI, where members are visible by virtue of being on the
    same workspace.

    Returns the slim `UserBrief` schema deliberately:
      - No nested `Tenant` object (would duplicate per-row).
      - Raw `is_superuser` projected to a `role` string ("admin" /
        "member") so admin-only SQL columns don't leak.
    """
    rows = (
        db.query(UserModel)
        .filter(UserModel.tenant_id == current_user.tenant_id)
        .order_by(UserModel.email.asc())
        .all()
    )
    return [
        UserBrief(
            id=u.id,
            email=u.email,
            full_name=u.full_name,
            is_active=u.is_active,
            role="admin" if u.is_superuser else "member",
        )
        for u in rows
    ]
