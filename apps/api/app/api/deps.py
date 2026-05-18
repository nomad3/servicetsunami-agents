from typing import Callable, Generator, Optional
import uuid

from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from jose import jwt, JWTError
from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.session import SessionLocal
from app.models.user import User
from app.models.agent import Agent
from app.models.agent_permission import AgentPermission

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/v1/auth/login")
oauth2_scheme_optional = OAuth2PasswordBearer(tokenUrl="/api/v1/auth/login", auto_error=False)

def get_db() -> Generator:
    try:
        db = SessionLocal()
        yield db
    finally:
        db.close()

def _jwt_iat_before_password_change(payload: dict, user: User) -> bool:
    """B-4: a JWT whose `iat` predates the user's last password change
    must be rejected. Without this, an attacker already inside the
    victim's account stays authenticated even after the password is
    reset (the very thing a user resets their password to prevent).
    Migration 130 stamps `password_changed_at` on every existing user
    so legacy tokens issued before the migration are also covered
    (a fresh deploy invalidates all tokens — that's the right
    conservative default for a security hardening rollout)."""
    iat = payload.get("iat")
    pwc = user.password_changed_at
    # IMP-2: when the user has NO password_changed_at (only possible on
    # rows that pre-date migration 130 in environments where the
    # migration hasn't run yet), fail open — there's nothing to compare
    # against. Migration 130 backfills every existing row with NOW(),
    # so in any deployed environment this branch should be unreachable.
    if pwc is None:
        return False
    # IMP-2: a token that's missing `iat` against a user with a real
    # password_changed_at is suspicious — `create_access_token` always
    # stamps iat, so an absent iat means either a hand-crafted token
    # or a legacy token from before iat existed. Either way the safer
    # choice is to invalidate (reject), not fail open.
    if iat is None:
        return True
    # iat is unix seconds (jose stamps it as int); pwc is a naive
    # UTC datetime per the migration. Convert to comparable units.
    try:
        from datetime import timezone
        pwc_unix = pwc.replace(tzinfo=timezone.utc).timestamp()
    except Exception:
        return False
    return float(iat) < pwc_unix


def get_current_user(
    db: Session = Depends(get_db),
    token: str = Depends(oauth2_scheme)
) -> User:
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        payload = jwt.decode(
            token,
            settings.SECRET_KEY,
            algorithms=[settings.ALGORITHM]
        )
        email: str = payload.get("sub")
        if email is None:
            raise credentials_exception
    except JWTError:
        raise credentials_exception
    user = db.query(User).filter(User.email == payload.get("sub")).first()
    if user is None:
        raise credentials_exception
    # B-4: invalidate JWTs issued before the user's last password change.
    if _jwt_iat_before_password_change(payload, user):
        raise credentials_exception
    return user

def get_current_active_user(
    current_user: User = Depends(get_current_user)
) -> User:
    if not current_user.is_active:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Inactive user")
    return current_user


def get_current_user_optional(
    db: Session = Depends(get_db),
    token: Optional[str] = Depends(oauth2_scheme_optional),
) -> Optional[User]:
    """Return the authenticated user if a valid JWT is present, otherwise None."""
    if not token:
        return None
    try:
        payload = jwt.decode(
            token,
            settings.SECRET_KEY,
            algorithms=[settings.ALGORITHM],
        )
        email: str = payload.get("sub")
        if email is None:
            return None
    except JWTError:
        return None
    user = db.query(User).filter(User.email == email).first()
    if user and not user.is_active:
        return None
    # B-4: same JWT-iat floor as the required path.
    if user and _jwt_iat_before_password_change(payload, user):
        return None
    return user


def require_superuser(
    current_user: User = Depends(get_current_active_user),
) -> User:
    if not current_user.is_superuser:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Superuser required")
    return current_user


def require_agent_permission(permission: str) -> Callable:
    """Return a FastAPI dependency that checks the caller has `permission` on the agent."""

    def dependency(
        agent_id: uuid.UUID,
        db: Session = Depends(get_db),
        current_user: User = Depends(get_current_active_user),
    ) -> Agent:
        agent = db.query(Agent).filter(Agent.id == agent_id).first()
        if not agent or str(agent.tenant_id) != str(current_user.tenant_id):
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Agent not found")

        # Superusers and the agent's owner always have implicit admin access.
        if current_user.is_superuser or (agent.owner_user_id and str(agent.owner_user_id) == str(current_user.id)):
            return agent

        # Check explicit grant: the user must have the requested permission or 'admin'.
        has_grant = (
            db.query(AgentPermission)
            .filter(
                AgentPermission.agent_id == agent.id,
                AgentPermission.tenant_id == current_user.tenant_id,
                AgentPermission.principal_type == "user",
                AgentPermission.principal_id == current_user.id,
                AgentPermission.permission.in_([permission, "admin"]),
            )
            .first()
        )
        if not has_grant:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Insufficient permissions")
        return agent

    return dependency