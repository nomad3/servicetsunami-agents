"""Plan runtime models for long-horizon planning and execution."""

import uuid
from datetime import datetime

from sqlalchemy import Column, DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import relationship

from app.db.base import Base


class Plan(Base):
    """Durable plan linked to a goal, with versioning and budget tracking."""

    __tablename__ = "plans"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id = Column(UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False)
    goal_id = Column(UUID(as_uuid=True), ForeignKey("goal_records.id"), nullable=True)
    owner_agent_slug = Column(String(100), nullable=False)

    title = Column(String(500), nullable=False)
    description = Column(Text)
    plan_version = Column(Integer, nullable=False, default=1)
    status = Column(String(30), nullable=False, default="draft")
    current_step_index = Column(Integer, nullable=False, default=0)
    replan_count = Column(Integer, nullable=False, default=0)

    # Budget constraints
    budget_max_actions = Column(Integer, nullable=True)
    budget_max_cost_usd = Column(Float, nullable=True)
    budget_max_runtime_hours = Column(Float, nullable=True)
    budget_actions_used = Column(Integer, nullable=False, default=0)
    budget_cost_used = Column(Float, nullable=False, default=0.0)

    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)

    tenant = relationship("Tenant")
    goal = relationship("GoalRecord")


class PlanStep(Base):
    """First-class step record within a plan."""

    __tablename__ = "plan_steps"
    __table_args__ = (
        UniqueConstraint("plan_id", "step_index", name="uq_plan_step_index"),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    plan_id = Column(UUID(as_uuid=True), ForeignKey("plans.id", ondelete="CASCADE"), nullable=False)
    step_index = Column(Integer, nullable=False)

    title = Column(String(500), nullable=False)
    description = Column(Text)
    owner_agent_slug = Column(String(100))
    step_type = Column(String(50), nullable=False, default="action")
    status = Column(String(30), nullable=False, default="pending")

    # Execution semantics
    expected_inputs = Column(JSONB, nullable=False, default=list)
    expected_outputs = Column(JSONB, nullable=False, default=list)
    required_tools = Column(JSONB, nullable=False, default=list)
    side_effect_level = Column(String(30), nullable=False, default="none")
    retry_policy = Column(JSONB, nullable=False, default=dict)
    fallback_step_index = Column(Integer, nullable=True)

    # Execution results
    output = Column(JSONB, nullable=True)
    error = Column(Text, nullable=True)
    started_at = Column(DateTime, nullable=True)
    completed_at = Column(DateTime, nullable=True)

    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)

    plan = relationship("Plan")


class PlanAssumption(Base):
    """Tracked assumption that a plan depends on."""

    __tablename__ = "plan_assumptions"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    plan_id = Column(UUID(as_uuid=True), ForeignKey("plans.id", ondelete="CASCADE"), nullable=False)

    description = Column(Text, nullable=False)
    status = Column(String(30), nullable=False, default="unverified")
    assertion_id = Column(UUID(as_uuid=True), ForeignKey("world_state_assertions.id"), nullable=True)
    invalidated_reason = Column(Text, nullable=True)

    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)

    plan = relationship("Plan")
    assertion = relationship("WorldStateAssertion")


class PlanEvent(Base):
    """Audit trail entry for plan state transitions."""

    __tablename__ = "plan_events"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    plan_id = Column(UUID(as_uuid=True), ForeignKey("plans.id", ondelete="CASCADE"), nullable=False)
    step_id = Column(UUID(as_uuid=True), ForeignKey("plan_steps.id"), nullable=True)

    event_type = Column(String(50), nullable=False)
    previous_status = Column(String(30), nullable=True)
    new_status = Column(String(30), nullable=True)
    reason = Column(Text)
    metadata_json = Column(JSONB, nullable=False, default=dict)
    agent_slug = Column(String(100), nullable=True)

    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)

    plan = relationship("Plan")
    step = relationship("PlanStep")
