"""ORM models for the platform-safety admin escape mechanism.

Migration: 146_platform_safety_escape.sql
Design: docs/plans/2026-05-21-platform-safety-floor-design.md §7
"""
from __future__ import annotations

import uuid

from sqlalchemy import (
    Column,
    DateTime,
    ForeignKey,
    String,
    Text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.sql import func

from app.db.base import Base


class PlatformSafetyEscapeGrant(Base):
    __tablename__ = "platform_safety_escape_grants"

    id = Column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4,
    )
    tenant_id = Column(
        UUID(as_uuid=True),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
    )
    issued_by_user_id = Column(
        UUID(as_uuid=True),
        ForeignKey("users.id"),
        nullable=False,
    )
    scoped_user_id = Column(UUID(as_uuid=True), nullable=False)
    scoped_session_id = Column(UUID(as_uuid=True), nullable=False)
    # Category key from PLATFORM_SAFETY_CATEGORIES, or '*' for all
    category = Column(String(64), nullable=False)
    reason = Column(Text, nullable=False)
    created_at = Column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    expires_at = Column(DateTime(timezone=True), nullable=False)
    revoked_at = Column(DateTime(timezone=True), nullable=True)


class PlatformSafetyAdminAudit(Base):
    __tablename__ = "platform_safety_admin_audit"

    id = Column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4,
    )
    # 'grant_created' | 'grant_revoked' | 'block_in_window' | 'block_no_window'
    event_type = Column(String(32), nullable=False)
    tenant_id = Column(
        UUID(as_uuid=True),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
    )
    actor_user_id = Column(UUID(as_uuid=True), nullable=True)
    grant_id = Column(
        UUID(as_uuid=True),
        ForeignKey("platform_safety_escape_grants.id"),
        nullable=True,
    )
    category = Column(String(64), nullable=True)
    # Free-text detail. NEVER contains user message text.
    detail = Column(Text, nullable=False, default="")
    created_at = Column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
