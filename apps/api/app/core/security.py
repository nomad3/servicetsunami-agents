import time
from datetime import datetime, timedelta
from typing import Any, Dict, Union

from jose import jwt
from passlib.context import CryptContext

from app.core.config import settings

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

ALGORITHM = "HS256"


def create_access_token(
    subject: Union[str, Any],
    expires_delta: timedelta | None = None,
    additional_claims: Dict[str, Any] | None = None,
    iat: int | None = None,
) -> str:
    """Issue a signed user access token.

    `iat` is the original issued-at Unix timestamp. On a fresh login it
    defaults to `now`. On `/auth/refresh` the caller passes the original
    iat from the incoming token so the chain has a bounded lifetime — see
    `MAX_TOKEN_CHAIN_AGE_SECONDS` in `auth.py`.

    Sub-project A PR2 (F7a): delegates to ``app.core.jwt_signing.mint_token``
    so the new ``kid="user-v1"`` claim lands in the header and the user
    domain's secret (``JWT_USER_SECRET``, defaulting to ``SECRET_KEY``
    until PR4) signs the token. Behavior unchanged at this PR; PR4
    introduces real distinct key material.
    """
    from app.core.jwt_signing import mint_token

    now = datetime.utcnow()
    if expires_delta:
        expire = now + expires_delta
    else:
        expire = now + timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
    # Use time.time() for iat (always Unix-epoch UTC) instead of
    # datetime.utcnow().timestamp() — the latter treats a naive datetime as
    # local time on .timestamp(), which would skew iat by the local UTC
    # offset on non-UTC hosts. Prod K8s is UTC; this just removes the latent
    # footgun for dev laptops.
    to_encode = {
        "exp": expire,
        "sub": str(subject),
        "iat": iat if iat is not None else int(time.time()),
    }
    if additional_claims:
        to_encode.update(additional_claims)
    return mint_token(to_encode, domain="user")


def verify_password(plain_password: str, hashed_password: str) -> bool:
    return pwd_context.verify(plain_password, hashed_password)


def get_password_hash(password: str) -> str:
    return pwd_context.hash(password[:72]) # Truncate password to 72 bytes
