"""Coalition models for tracking team shape performance."""

import uuid
from datetime import datetime

from sqlalchemy import Column, DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import relationship

from app.db.base import Base


class CoalitionTemplate(Base):
    """Reusable team shape definition."""

    __tablename__ = "coalition_templates"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id = Column(UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False)

    name = Column(String(200), nullable=False)
    description = Column(Text)
    pattern = Column(String(50), nullable=False)
    role_agent_map = Column(JSONB, nullable=False, default=dict)
    task_types = Column(JSONB, nullable=False, default=list)

    # Performance stats (updated from collaboration outcomes)
    total_uses = Column(Integer, nullable=False, default=0)
    success_count = Column(Integer, nullable=False, default=0)
    avg_quality_score = Column(Float, nullable=False, default=0.0)
    avg_rounds_to_consensus = Column(Float, nullable=False, default=0.0)
    avg_cost_usd = Column(Float, nullable=False, default=0.0)

    status = Column(String(30), nullable=False, default="active")
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)

    tenant = relationship("Tenant")


class CoalitionOutcome(Base):
    """Record of a coalition's performance on a specific task."""

    __tablename__ = "coalition_outcomes"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id = Column(UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False)
    template_id = Column(UUID(as_uuid=True), ForeignKey("coalition_templates.id"), nullable=True)
    collaboration_id = Column(UUID(as_uuid=True), ForeignKey("collaboration_sessions.id"), nullable=True)

    task_type = Column(String(50), nullable=False)
    pattern = Column(String(50), nullable=False)
    role_agent_map = Column(JSONB, nullable=False, default=dict)

    # Outcome
    success = Column(String(10), nullable=False)
    quality_score = Column(Float, nullable=True)
    rounds_completed = Column(Integer, nullable=False, default=1)
    consensus_reached = Column(String(10), nullable=True)
    cost_usd = Column(Float, nullable=False, default=0.0)
    duration_seconds = Column(Integer, nullable=True)

    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)

    tenant = relationship("Tenant")
    template = relationship("CoalitionTemplate")
    collaboration = relationship("CollaborationSession")
