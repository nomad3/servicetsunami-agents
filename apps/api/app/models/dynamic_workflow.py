"""Dynamic workflow models — user-defined workflows executed on Temporal."""

import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean, Column, DateTime, Float, ForeignKey, Integer, String, Text,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSON, UUID
from sqlalchemy.orm import relationship

from app.db.base import Base


class DynamicWorkflow(Base):
    __tablename__ = "dynamic_workflows"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id = Column(UUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False)
    name = Column(String(255), nullable=False)
    description = Column(Text)
    definition = Column(JSON, nullable=False, default={"steps": []})
    version = Column(Integer, nullable=False, default=1)
    status = Column(String(20), nullable=False, default="draft")
    trigger_config = Column(JSON)
    created_by = Column(UUID(as_uuid=True))
    tags = Column(ARRAY(String), default=[])
    # Marketplace — counters are NOT NULL (migration 103) so future partial
    # inserts can't poison the /templates/browse response.
    tier = Column(String(20), default="custom")
    source_template_id = Column(UUID(as_uuid=True))
    public = Column(Boolean, default=False)
    installs = Column(Integer, nullable=False, default=0)
    rating = Column(Float, nullable=False, default=0)
    # Stats
    run_count = Column(Integer, nullable=False, default=0)
    last_run_at = Column(DateTime)
    avg_duration_ms = Column(Integer)
    success_rate = Column(Float)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    runs = relationship("WorkflowRun", back_populates="workflow", cascade="all, delete-orphan")


class WorkflowRun(Base):
    __tablename__ = "workflow_runs"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id = Column(UUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False)
    workflow_id = Column(UUID(as_uuid=True), ForeignKey("dynamic_workflows.id"), nullable=False)
    workflow_version = Column(Integer)
    trigger_type = Column(String(20))
    status = Column(String(20), nullable=False, default="running")
    started_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    completed_at = Column(DateTime)
    duration_ms = Column(Integer)
    step_results = Column(JSON, default={})
    current_step = Column(String(100))
    error = Column(Text)
    input_data = Column(JSON)
    output_data = Column(JSON)
    total_tokens = Column(Integer, default=0)
    total_cost_usd = Column(Float, default=0)
    platform = Column(String(50))
    temporal_workflow_id = Column(String(255))

    workflow = relationship("DynamicWorkflow", back_populates="runs")
    step_logs = relationship("WorkflowStepLog", back_populates="run", cascade="all, delete-orphan")


class WorkflowStepLog(Base):
    __tablename__ = "workflow_step_logs"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    run_id = Column(UUID(as_uuid=True), ForeignKey("workflow_runs.id"), nullable=False)
    step_id = Column(String(100), nullable=False)
    step_type = Column(String(50), nullable=False)
    step_name = Column(String(255))
    status = Column(String(20), nullable=False, default="pending")
    started_at = Column(DateTime)
    completed_at = Column(DateTime)
    duration_ms = Column(Integer)
    input_data = Column(JSON)
    output_data = Column(JSON)
    error = Column(Text)
    tokens_used = Column(Integer, default=0)
    cost_usd = Column(Float, default=0)
    platform = Column(String(50))
    retry_count = Column(Integer, default=0)

    run = relationship("WorkflowRun", back_populates="step_logs")
