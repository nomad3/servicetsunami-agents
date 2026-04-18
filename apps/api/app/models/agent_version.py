import uuid
from datetime import datetime
from sqlalchemy import Column, String, Integer, DateTime, Text, ForeignKey
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.orm import relationship

from app.db.base import Base


class AgentVersion(Base):
    __tablename__ = "agent_versions"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    agent_id = Column(UUID(as_uuid=True), ForeignKey("agents.id", ondelete="CASCADE"), nullable=False, index=True)
    tenant_id = Column(UUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False, index=True)
    version = Column(Integer, nullable=False)  # monotonic counter, increments on each save
    config_snapshot = Column(JSONB, nullable=False)  # full agent config at this version
    status = Column(String(20), nullable=False, default="draft")  # draft|staging|production|rolled_back
    notes = Column(Text, nullable=True)  # changelog / reason for change
    promoted_by = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    promoted_at = Column(DateTime, nullable=True)
    performance_snapshot = Column(JSONB, nullable=True)  # p95 latency + quality score at promotion time
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)

    # Relationships
    agent = relationship("Agent", foreign_keys=[agent_id])
    tenant = relationship("Tenant", foreign_keys=[tenant_id])
    promoter = relationship("User", foreign_keys=[promoted_by])
