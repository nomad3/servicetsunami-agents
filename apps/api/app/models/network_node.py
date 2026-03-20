"""STP network node models for distributed agent execution."""
import uuid
from datetime import datetime

from sqlalchemy import Column, DateTime, Float, ForeignKey, Integer, JSON, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship

from app.db.base import Base


class NetworkNode(Base):
    __tablename__ = "network_nodes"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id = Column(UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True)
    name = Column(String(255), nullable=False)
    tailscale_ip = Column(String(64), nullable=True)
    status = Column(String(20), nullable=False, default="online")  # online, suspect, offline
    last_heartbeat = Column(DateTime, nullable=False, default=datetime.utcnow, index=True)
    capabilities = Column(JSON, nullable=True)
    max_concurrent_tasks = Column(Integer, nullable=False, default=3)
    current_load = Column(Float, nullable=False, default=0.0)
    pricing_tier = Column(String(20), nullable=False, default="standard")
    total_tasks_completed = Column(Integer, nullable=False, default=0)
    avg_execution_time_ms = Column(Float, nullable=True)
    reputation_score = Column(Float, nullable=False, default=0.5)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)

    tenant = relationship("Tenant")

    def __repr__(self):
        return f"<NetworkNode {self.id} {self.status}:{self.name}>"
