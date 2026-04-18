import uuid
from datetime import datetime
from sqlalchemy import Column, String, Integer, Float, DateTime, Text, ForeignKey
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.orm import relationship

from app.db.base import Base


class AgentAuditLog(Base):
    __tablename__ = "agent_audit_logs"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id = Column(UUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False, index=True)
    agent_id = Column(UUID(as_uuid=True), ForeignKey("agents.id", ondelete="SET NULL"), nullable=True, index=True)
    external_agent_id = Column(UUID(as_uuid=True), nullable=True)  # for hired external agents
    invoked_by_user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    invoked_by_agent_id = Column(UUID(as_uuid=True), ForeignKey("agents.id", ondelete="SET NULL"), nullable=True)  # A2A call
    session_id = Column(UUID(as_uuid=True), nullable=True)
    invocation_type = Column(String(20), nullable=False)  # chat|workflow|a2a|api|scheduled
    input_summary = Column(Text, nullable=True)   # first 500 chars, PII-stripped
    output_summary = Column(Text, nullable=True)  # first 500 chars
    input_tokens = Column(Integer, nullable=True)
    output_tokens = Column(Integer, nullable=True)
    cost_usd = Column(Float, nullable=True)
    latency_ms = Column(Integer, nullable=True)
    status = Column(String(30), nullable=False)  # success|error|timeout|blocked_by_policy
    error_message = Column(Text, nullable=True)
    policy_violations = Column(JSONB, nullable=True)
    quality_score = Column(Float, nullable=True)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow, index=True)

    # Relationships
    tenant = relationship("Tenant", foreign_keys=[tenant_id])
    agent = relationship("Agent", foreign_keys=[agent_id])
    invoked_by_user = relationship("User", foreign_keys=[invoked_by_user_id])
    invoked_by_agent = relationship("Agent", foreign_keys=[invoked_by_agent_id], overlaps="agent")
