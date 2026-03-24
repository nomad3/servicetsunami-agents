"""Durable goal records for agent self-model persistence."""

import uuid
from datetime import datetime

from sqlalchemy import Column, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import relationship

from app.db.base import Base


class GoalRecord(Base):
    """Persistent goal tracked across sessions for an agent."""

    __tablename__ = "goal_records"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id = Column(UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False)
    owner_agent_slug = Column(String(100), nullable=False)
    created_by = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)

    title = Column(String(500), nullable=False)
    description = Column(Text)
    objective_type = Column(String(50), nullable=False, default="operational")
    priority = Column(String(20), nullable=False, default="normal")
    state = Column(String(30), nullable=False, default="proposed")

    success_criteria = Column(JSONB, nullable=False, default=list)
    deadline = Column(DateTime, nullable=True)
    related_entity_ids = Column(JSONB, nullable=False, default=list)
    parent_goal_id = Column(UUID(as_uuid=True), ForeignKey("goal_records.id"), nullable=True)
    progress_summary = Column(Text)
    progress_pct = Column(Integer, nullable=False, default=0)

    last_reviewed_at = Column(DateTime, nullable=True)
    completed_at = Column(DateTime, nullable=True)
    abandoned_at = Column(DateTime, nullable=True)
    abandoned_reason = Column(Text)

    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)

    tenant = relationship("Tenant")
    creator = relationship("User")
    parent_goal = relationship("GoalRecord", remote_side=[id])
