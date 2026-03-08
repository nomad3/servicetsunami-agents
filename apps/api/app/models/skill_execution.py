"""SkillExecution model — tracks individual skill invocations."""
import uuid
from datetime import datetime
from sqlalchemy import Column, String, Integer, JSON, DateTime, ForeignKey
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship
from app.db.base import Base


class SkillExecution(Base):
    __tablename__ = "skill_executions"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id = Column(UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True)
    skill_id = Column(UUID(as_uuid=True), ForeignKey("skills.id", ondelete="CASCADE"), nullable=False, index=True)
    entity_id = Column(UUID(as_uuid=True), ForeignKey("knowledge_entities.id", ondelete="SET NULL"), nullable=True, index=True)
    agent_id = Column(UUID(as_uuid=True), ForeignKey("agents.id", ondelete="SET NULL"), nullable=True)
    workflow_run_id = Column(UUID(as_uuid=True), ForeignKey("pipeline_runs.id", ondelete="SET NULL"), nullable=True)
    input = Column(JSON, nullable=True)
    output = Column(JSON, nullable=True)
    status = Column(String, nullable=False)  # success, error
    duration_ms = Column(Integer, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    tenant = relationship("Tenant")
    skill = relationship("Skill")
    entity = relationship("KnowledgeEntity")
    agent = relationship("Agent")
