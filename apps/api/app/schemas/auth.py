"""Auth schemas — password recovery + reset.

Hardened by the 2026-05-12 security review:
    N-1: password policy lifted from 8-char minimum to 12 chars + must
         contain ≥3 of {upper, lower, digit, symbol} — a user can no
         longer reset to "password" or "12345678".
    N-4: dedicated response model so the no-enumeration contract on
         the recovery endpoint can't drift if a future contributor
         adds a field on only one of the hit/miss paths.
"""
from pydantic import BaseModel, EmailStr, Field, field_validator


_MIN_LEN = 12
_MIN_CLASSES = 3


def _classes_used(password: str) -> int:
    """Count how many of {upper, lower, digit, symbol} the password
    uses. Anything not alphanumeric counts as a symbol — that includes
    spaces and unicode punctuation."""
    has_upper = any(c.isupper() for c in password)
    has_lower = any(c.islower() for c in password)
    has_digit = any(c.isdigit() for c in password)
    has_symbol = any(not c.isalnum() for c in password)
    return sum([has_upper, has_lower, has_digit, has_symbol])


def _validate_password_complexity(password: str) -> str:
    """Pydantic validator shared by both new-account and reset paths."""
    if len(password) < _MIN_LEN:
        raise ValueError(
            f"Password must be at least {_MIN_LEN} characters."
        )
    if _classes_used(password) < _MIN_CLASSES:
        raise ValueError(
            f"Password must include at least {_MIN_CLASSES} of: "
            "uppercase, lowercase, digit, symbol."
        )
    return password


class PasswordResetRequest(BaseModel):
    email: EmailStr


class PasswordResetConfirm(BaseModel):
    email: EmailStr
    token: str = Field(..., min_length=8, max_length=128)
    new_password: str = Field(..., min_length=_MIN_LEN)

    @field_validator("new_password")
    @classmethod
    def password_complexity(cls, v: str) -> str:
        return _validate_password_complexity(v)


class PasswordResetMessage(BaseModel):
    """N-4: locked response model for /password-recovery + /reset-password.

    Both endpoints return only `message`. A future contributor adding a
    field on the hit path (e.g. `next_attempt_at`) would now have to
    add it on the miss path too — preserves the no-enumeration
    contract by typing rather than reviewer vigilance.
    """
    message: str
