from datetime import timedelta, datetime
import hashlib
import hmac
import secrets
import logging
import time
from fastapi import APIRouter, Depends, HTTPException, status, Request
from fastapi.security import OAuth2PasswordRequestForm
from jose import JWTError, jwt
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
from app.core.rate_limit import limiter

router = APIRouter()
logger = logging.getLogger(__name__)

_PASSWORD_RESET_MESSAGE = "Password reset instructions sent if email is registered"

# Cap the refresh chain. Even with /auth/refresh, the *original* iat travels
# with every refreshed token, so a stolen token at hour 0 can be refreshed
# at most until hour `MAX_TOKEN_CHAIN_AGE_SECONDS / 3600`. After that the
# user must re-authenticate. 7 days = weekly forced re-auth.
MAX_TOKEN_CHAIN_AGE_SECONDS = 7 * 24 * 60 * 60


@router.post("/login", response_model=token_schema.Token)
@limiter.limit("10/minute")
def login_for_access_token(
    request: Request,
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


def _bearer_token(request: Request) -> str | None:
    auth = request.headers.get("authorization") or request.headers.get("Authorization")
    if not auth:
        return None
    parts = auth.split(None, 1)
    if len(parts) != 2 or parts[0].lower() != "bearer":
        return None
    return parts[1].strip()


@router.post("/refresh", response_model=token_schema.Token)
@limiter.limit("60/minute")
def refresh_access_token(
    request: Request,
    current_user=Depends(deps.get_current_active_user),
):
    """Re-issue a fresh access token for the currently authenticated caller.

    The caller must present a still-valid bearer token. We re-mint with the
    same identity claims, a fresh `exp`, and the *original* `iat` preserved
    so the refresh chain has a bounded lifetime. After
    `MAX_TOKEN_CHAIN_AGE_SECONDS` since original login, the user must
    re-authenticate.
    """
    raw_token = _bearer_token(request)
    original_iat: int | None = None
    if raw_token:
        try:
            decoded = jwt.decode(
                raw_token,
                settings.SECRET_KEY,
                algorithms=[security.ALGORITHM],
            )
            iat_claim = decoded.get("iat")
            if isinstance(iat_claim, (int, float)):
                original_iat = int(iat_claim)
        except JWTError:
            # Token failed to decode — get_current_active_user already
            # rejected this case, so we shouldn't get here. Fall through
            # to a fresh iat to avoid hard-failing the refresh on an
            # encoding edge case.
            original_iat = None

    if original_iat is not None:
        age = int(time.time()) - original_iat
        if age > MAX_TOKEN_CHAIN_AGE_SECONDS:
            raise HTTPException(
                status_code=401,
                detail="session too old; please re-authenticate",
                headers={"WWW-Authenticate": "Bearer"},
            )

    access_token_expires = timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
    claims = {"user_id": str(current_user.id)}
    if current_user.tenant_id:
        claims["tenant_id"] = str(current_user.tenant_id)
    access_token = security.create_access_token(
        current_user.email,
        expires_delta=access_token_expires,
        additional_claims=claims,
        iat=original_iat,
    )
    logger.info("Token refreshed for %s", current_user.email)
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
@limiter.limit("3/hour")
def recover_password(request: Request, email: str, db: Session = Depends(deps.get_db)):
    """
    Password recovery
    """
    user = user_service.get_user_by_email(db, email=email)

    if not user:
        # Identical message for missing user — prevents email enumeration
        return {"message": _PASSWORD_RESET_MESSAGE}

    token = secrets.token_urlsafe(32)
    token_hash = hashlib.sha256(token.encode()).hexdigest()
    user.password_reset_token = token_hash
    user.password_reset_expires = datetime.utcnow() + timedelta(hours=24)
    db.add(user)
    db.commit()

    # In a real app, send an email here. For now, log it.
    logger.info(f"Password reset token generated for {email}")

    return {"message": _PASSWORD_RESET_MESSAGE}

@router.post("/reset-password")
@limiter.limit("5/hour")
def reset_password(
    request: Request,
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
