import uuid
from sqlalchemy import Column, String, Integer, ForeignKey, JSON, DateTime, Text, Numeric
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship
from datetime import datetime

from app.db.base import Base


class ExecutionTrace(Base):
    __tablename__ = "execution_traces"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    task_id = Column(UUID(as_uuid=True), ForeignKey("agent_tasks.id"), nullable=False, index=True)
    tenant_id = Column(UUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False, index=True)
    step_type = Column(String, nullable=False)
    step_order = Column(Integer, nullable=False)
    agent_id = Column(UUID(as_uuid=True), ForeignKey("agents.id"), nullable=True)
    details = Column(JSON, nullable=True)
    duration_ms = Column(Integer, nullable=True)

    # Error tracking
    error_message = Column(Text, nullable=True)

    # LLM cost tracking
    input_tokens = Column(Integer, nullable=True)
    output_tokens = Column(Integer, nullable=True)
    cost_usd = Column(Numeric(10, 6), nullable=True)

    # Skill tracking
    skill_id = Column(UUID(as_uuid=True), ForeignKey("skills.id"), nullable=True)

    # Nested step support
    parent_step_id = Column(UUID(as_uuid=True), ForeignKey("execution_traces.id"), nullable=True)
    retry_count = Column(Integer, default=0)

    created_at = Column(DateTime, default=datetime.utcnow)

    task = relationship("AgentTask")
    tenant = relationship("Tenant")
    agent = relationship("Agent")
    skill = relationship("Skill")
    parent_step = relationship("ExecutionTrace", remote_side="ExecutionTrace.id")
