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
    """Issue a signed access token.

    `iat` is the original issued-at Unix timestamp. On a fresh login it
    defaults to `now`. On `/auth/refresh` the caller passes the original
    iat from the incoming token so the chain has a bounded lifetime — see
    `MAX_TOKEN_CHAIN_AGE_SECONDS` in `auth.py`.
    """
    now = datetime.utcnow()
    if expires_delta:
        expire = now + expires_delta
    else:
        expire = now + timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
    to_encode = {
        "exp": expire,
        "sub": str(subject),
        "iat": iat if iat is not None else int(now.timestamp()),
    }
    if additional_claims:
        to_encode.update(additional_claims)
    encoded_jwt = jwt.encode(to_encode, settings.SECRET_KEY, algorithm=ALGORITHM)
    return encoded_jwt


def verify_password(plain_password: str, hashed_password: str) -> bool:
    return pwd_context.verify(plain_password, hashed_password)


def get_password_hash(password: str) -> str:
    return pwd_context.hash(password[:72]) # Truncate password to 72 bytes
