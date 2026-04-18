import uuid
from datetime import datetime
from sqlalchemy import Column, String, Integer, Float, DateTime, ForeignKey
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship

from app.db.base import Base


class AgentPerformanceSnapshot(Base):
    __tablename__ = "agent_performance_snapshots"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    agent_id = Column(UUID(as_uuid=True), ForeignKey("agents.id", ondelete="CASCADE"), nullable=False, index=True)
    tenant_id = Column(UUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False, index=True)
    window_start = Column(DateTime, nullable=False, index=True)
    window_hours = Column(Integer, nullable=False, default=24)

    # Counts
    invocation_count = Column(Integer, nullable=False, default=0)
    success_count = Column(Integer, nullable=False, default=0)
    error_count = Column(Integer, nullable=False, default=0)
    timeout_count = Column(Integer, nullable=False, default=0)

    # Latency percentiles (milliseconds)
    latency_p50_ms = Column(Integer, nullable=True)
    latency_p95_ms = Column(Integer, nullable=True)
    latency_p99_ms = Column(Integer, nullable=True)

    # Quality and cost
    avg_quality_score = Column(Float, nullable=True)
    total_tokens = Column(Integer, nullable=False, default=0)
    total_cost_usd = Column(Float, nullable=False, default=0.0)
    cost_per_quality_point = Column(Float, nullable=True)

    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)

    # Relationships
    agent = relationship("Agent", foreign_keys=[agent_id])
    tenant = relationship("Tenant", foreign_keys=[tenant_id])
