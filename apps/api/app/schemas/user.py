from pydantic import BaseModel, EmailStr, Field, field_validator
import uuid
from .tenant import Tenant
from .auth import _validate_password_complexity

class UserBase(BaseModel):
    email: EmailStr
    full_name: str | None = None
    is_active: bool = True
    is_superuser: bool = False

class UserCreate(UserBase):
    # N-N6 (security review 2026-05-12 round 2): the password
    # complexity validator (12+ chars, ≥3 of {upper, lower, digit,
    # symbol}) applies to registration too — previously the reset
    # path was strict and the create path took anything ≥1 char, so a
    # user could register with "password" but couldn't reset to it.
    # Inconsistent posture closed.
    password: str = Field(..., min_length=12)

    @field_validator("password")
    @classmethod
    def password_complexity(cls, v: str) -> str:
        return _validate_password_complexity(v)

class UserUpdate(UserBase):
    password: str | None = Field(default=None, min_length=12)

    @field_validator("password")
    @classmethod
    def password_complexity_optional(cls, v: str | None) -> str | None:
        if v is None:
            return v
        return _validate_password_complexity(v)

class User(UserBase):
    id: uuid.UUID
    tenant_id: uuid.UUID
    tenant: Tenant | None = None

    class Config:
        from_attributes = True
