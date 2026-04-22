import uuid
from datetime import datetime
from sqlalchemy import Column, String, Boolean, Integer, Numeric, DateTime, ForeignKey, Text
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.orm import relationship

from app.db.base import Base


class AgentTestCase(Base):
    __tablename__ = "agent_test_cases"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    agent_id = Column(UUID(as_uuid=True), ForeignKey("agents.id", ondelete="CASCADE"), nullable=False, index=True)
    tenant_id = Column(UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True)
    name = Column(String(200), nullable=False)
    input = Column(Text, nullable=False)
    expected_output_contains = Column(JSONB, nullable=False, default=list)
    expected_output_excludes = Column(JSONB, nullable=False, default=list)
    min_quality_score = Column(Numeric(3, 2), nullable=False, default=0.6)
    max_latency_ms = Column(Integer, nullable=False, default=10000)
    tags = Column(JSONB, nullable=False, default=list)
    enabled = Column(Boolean, nullable=False, default=True)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)

    agent = relationship("Agent", foreign_keys=[agent_id])


class AgentTestRun(Base):
    __tablename__ = "agent_test_runs"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    agent_id = Column(UUID(as_uuid=True), ForeignKey("agents.id", ondelete="CASCADE"), nullable=False, index=True)
    tenant_id = Column(UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True)
    agent_version = Column(Integer, nullable=True)
    triggered_by_user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    run_type = Column(String(20), nullable=False, default="manual")  # manual|promotion_gate|shadow
    status = Column(String(20), nullable=False, default="running")   # running|passed|failed|error
    total_cases = Column(Integer, nullable=False, default=0)
    passed_count = Column(Integer, nullable=False, default=0)
    failed_count = Column(Integer, nullable=False, default=0)
    results = Column(JSONB, nullable=False, default=list)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow, index=True)
    completed_at = Column(DateTime, nullable=True)

    agent = relationship("Agent", foreign_keys=[agent_id])
    triggered_by = relationship("User", foreign_keys=[triggered_by_user_id])
