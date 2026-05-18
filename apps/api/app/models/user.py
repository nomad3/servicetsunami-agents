import uuid
from sqlalchemy import Boolean, Column, DateTime, Integer, String, ForeignKey
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship

from app.db.base import Base

class User(Base):
    __tablename__ = "users"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    full_name = Column(String, index=True)
    email = Column(String, unique=True, index=True)
    hashed_password = Column(String)
    is_active = Column(Boolean(), default=True)
    is_superuser = Column(Boolean(), default=False)
    password_reset_token = Column(String, index=True, nullable=True)
    password_reset_expires = Column(DateTime(), nullable=True)
    # Password-recovery security hardening — migration 130 (2026-05-12).
    # See apps/api/app/api/v1/auth.py + apps/api/app/api/deps.py for usage.
    #   password_changed_at      → JWT-iat floor (B-4 from security review).
    #                              Tokens with iat < this value are 401'd
    #                              by deps.get_current_active_user.
    #   password_reset_attempts  → per-user attempt counter so a partially-
    #                              leaked token can't be brute-forced
    #                              inside the slowapi 5/hr per-IP window
    #                              (I-1 from review).
    #   password_reset_csrf_hash → SHA-256 hash of the requester-confirmer
    #                              cookie value set by /password-recovery;
    #                              /reset-password refuses without a
    #                              matching cookie (B-5).
    password_changed_at = Column(DateTime(), nullable=True)
    password_reset_attempts = Column(Integer, default=0)
    password_reset_csrf_hash = Column(String(64), nullable=True)
    tenant_id = Column(UUID(as_uuid=True), ForeignKey("tenants.id"))
    tenant = relationship("Tenant", back_populates="users")
