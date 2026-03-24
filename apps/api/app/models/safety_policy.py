"""Tenant-scoped safety policy overrides for governed actions."""

import uuid
from datetime import datetime

from sqlalchemy import Boolean, Column, DateTime, ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship

from app.db.base import Base


class TenantActionPolicy(Base):
    """Tenant override for a governed action on a specific channel."""

    __tablename__ = "tenant_action_policies"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id",
            "action_type",
            "action_name",
            "channel",
            name="uq_tenant_action_policy",
        ),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id = Column(UUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False)
    action_type = Column(String(50), nullable=False)
    action_name = Column(String(150), nullable=False)
    channel = Column(String(50), nullable=False, default="*")
    decision = Column(String(30), nullable=False)
    rationale = Column(Text)
    enabled = Column(Boolean, nullable=False, default=True)
    created_by = Column(UUID(as_uuid=True), ForeignKey("users.id"))
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)

    tenant = relationship("Tenant")
    creator = relationship("User")
