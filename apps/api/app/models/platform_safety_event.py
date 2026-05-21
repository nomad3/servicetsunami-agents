"""SQLAlchemy model for the platform_safety_events audit table.

Migration: 145_platform_safety_events.sql
Design: docs/plans/2026-05-21-platform-safety-floor-design.md §5

Privacy invariant: this table stores SHA256(message), NEVER the raw
text. The hash lets us detect repeated probing + cross-correlate with
model-level refusals, but does NOT create a queryable catalogue of
user content.
"""
from __future__ import annotations

import uuid

from sqlalchemy import (
    Column,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
)
from sqlalchemy.dialects.postgresql import REAL, UUID
from sqlalchemy.sql import func

from app.db.base import Base


class PlatformSafetyEvent(Base):
    __tablename__ = "platform_safety_events"

    id = Column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4,
    )
    tenant_id = Column(
        UUID(as_uuid=True),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    agent_id = Column(UUID(as_uuid=True), nullable=True)
    session_id = Column(UUID(as_uuid=True), nullable=True)
    user_id = Column(UUID(as_uuid=True), nullable=True)

    # SHA256(message). 64 hex chars. NEVER the raw text.
    message_hash = Column(Text, nullable=False)

    # One of the keys in
    # apps/api/app/core/safety_defaults.py::PLATFORM_SAFETY_CATEGORIES
    category = Column(String(64), nullable=False, index=True)

    # 1 = regex, 2 = embedding, 3 = LLM classifier
    detection_tier = Column(Integer, nullable=False)

    # 0.0-1.0 for tier 2+ (NULL for tier 1 which is binary)
    confidence = Column(REAL, nullable=True)

    # 'enforced' = refusal fired against the user
    # 'shadow'   = tier 3 would-have-blocked during pre-enforcement
    #              window (§12 #7 — Luna call)
    enforcement_mode = Column(
        String(16), nullable=False, default="enforced",
    )

    created_at = Column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
