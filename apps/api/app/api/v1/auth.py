from datetime import timedelta, datetime
import hashlib
import hmac
import secrets
import logging
from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy.orm import Session

from app.schemas import token as token_schema
from app.schemas import user as user_schema
from app.schemas import tenant as tenant_schema
from app.schemas import auth as auth_schema
from app.api import deps
from app.core import security
from app.core.config import settings
from app.services import base as base_service
from app.services import users as user_service

router = APIRouter()
logger = logging.getLogger(__name__)

@router.post("/login", response_model=token_schema.Token)
def login_for_access_token(
    db: Session = Depends(deps.get_db), form_data: OAuth2PasswordRequestForm = Depends()
):
    user = base_service.authenticate_user(db, email=form_data.username, password=form_data.password)
    if not user:
        raise HTTPException(
            status_code=401,
            detail="Incorrect email or password",
            headers={"WWW-Authenticate": "Bearer"},
        )
    access_token_expires = timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
    claims = {"user_id": str(user.id)}
    if user.tenant_id:
        claims["tenant_id"] = str(user.tenant_id)

    access_token = security.create_access_token(
        user.email,
        expires_delta=access_token_expires,
        additional_claims=claims,
    )
    return {"access_token": access_token, "token_type": "bearer"}

@router.post("/register", response_model=user_schema.User)
def register_user(
    *,
    db: Session = Depends(deps.get_db),
    user_in: user_schema.UserCreate,
    tenant_in: tenant_schema.TenantCreate
):
    user = user_service.get_user_by_email(db, email=user_in.email)
    if user:
        raise HTTPException(
            status_code=400,
            detail="The user with this email already exists in the system.",
        )
    user = user_service.create_user_with_tenant(db, user_in=user_in, tenant_in=tenant_in)
    return user

@router.get("/users/me", response_model=user_schema.User)
def read_users_me(
    current_user: user_schema.User = Depends(deps.get_current_active_user)
):
    """
    Get current user.
    """
    return current_user

@router.post("/password-recovery/{email}")
def recover_password(email: str, db: Session = Depends(deps.get_db)):
    """
    Password recovery
    """
    user = user_service.get_user_by_email(db, email=email)

    if not user:
        # We don't want to reveal if a user exists or not for security reasons
        return {"message": "Password reset email sent if user exists"}

    token = secrets.token_urlsafe(32)
    token_hash = hashlib.sha256(token.encode()).hexdigest()
    user.password_reset_token = token_hash
    user.password_reset_expires = datetime.utcnow() + timedelta(hours=24)
    db.add(user)
    db.commit()

    # In a real app, send an email here. For now, log it.
    logger.info(f"Password reset token generated for {email}")

    return {"message": "Password reset instructions sent if email exists"}

@router.post("/reset-password")
def reset_password(
    body: auth_schema.PasswordResetConfirm,
    db: Session = Depends(deps.get_db)
):
    """
    Reset password
    """
    user = user_service.get_user_by_email(db, email=body.email)

    if not user or not user.password_reset_token or not user.password_reset_expires:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid or expired token",
        )

    if user.password_reset_expires < datetime.utcnow():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid or expired token",
        )

    submitted_hash = hashlib.sha256(body.token.encode()).hexdigest()
    if not hmac.compare_digest(user.password_reset_token, submitted_hash):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid or expired token",
        )

    user.hashed_password = security.get_password_hash(body.new_password)
    user.password_reset_token = None
    user.password_reset_expires = None
    db.add(user)
    db.commit()

    return {"message": "Password updated successfully"}
