"""Shared blackboard for multi-agent task collaboration."""

import uuid
from datetime import datetime

from sqlalchemy import Column, DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import relationship

from app.db.base import Base


class Blackboard(Base):
    """Task-scoped shared working memory for agent collaboration."""

    __tablename__ = "blackboards"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id = Column(UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False)
    plan_id = Column(UUID(as_uuid=True), ForeignKey("plans.id"), nullable=True)
    goal_id = Column(UUID(as_uuid=True), ForeignKey("goal_records.id"), nullable=True)

    title = Column(String(500), nullable=False)
    status = Column(String(30), nullable=False, default="active")
    version = Column(Integer, nullable=False, default=0)

    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)

    tenant = relationship("Tenant")
    plan = relationship("Plan")
    goal = relationship("GoalRecord")


class BlackboardEntry(Base):
    """Append-only entry on a blackboard. Never overwritten, only superseded."""

    __tablename__ = "blackboard_entries"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    blackboard_id = Column(UUID(as_uuid=True), ForeignKey("blackboards.id", ondelete="CASCADE"), nullable=False)
    board_version = Column(Integer, nullable=False)

    # Content
    entry_type = Column(String(50), nullable=False)
    content = Column(Text, nullable=False)
    evidence = Column(JSONB, nullable=False, default=list)
    confidence = Column(Float, nullable=False, default=0.7)

    # Ownership
    author_agent_slug = Column(String(100), nullable=False)
    author_role = Column(String(50), nullable=False, default="contributor")

    # Hierarchy
    parent_entry_id = Column(UUID(as_uuid=True), ForeignKey("blackboard_entries.id"), nullable=True)
    supersedes_entry_id = Column(UUID(as_uuid=True), ForeignKey("blackboard_entries.id"), nullable=True)

    # Resolution
    status = Column(String(30), nullable=False, default="proposed")
    resolved_by_agent = Column(String(100), nullable=True)
    resolution_reason = Column(Text, nullable=True)

    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)

    blackboard = relationship("Blackboard")
    parent_entry = relationship("BlackboardEntry", remote_side=[id], foreign_keys=[parent_entry_id])
    supersedes = relationship("BlackboardEntry", remote_side=[id], foreign_keys=[supersedes_entry_id])
