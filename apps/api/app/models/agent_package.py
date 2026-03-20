"""STP agent package models for the distributed marketplace."""
import uuid
from datetime import datetime

from sqlalchemy import Column, DateTime, Float, ForeignKey, Integer, JSON, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship

from app.db.base import Base


class AgentPackage(Base):
    __tablename__ = "agent_packages"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id = Column(UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True)
    creator_tenant_id = Column(UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True)
    name = Column(String(255), nullable=False, index=True)
    version = Column(String(50), nullable=False, default="0.1.0")
    content_hash = Column(String(64), nullable=False, index=True)
    signature = Column(Text, nullable=True)
    creator_public_key = Column(Text, nullable=True)
    skill_id = Column(UUID(as_uuid=True), ForeignKey("skills.id", ondelete="SET NULL"), nullable=True, index=True)
    package_metadata = Column("metadata", JSON, nullable=True)
    required_tools = Column(JSON, nullable=True)
    required_cli = Column(String(50), nullable=False, default="any")
    pricing_tier = Column(String(20), nullable=False, default="simple")
    quality_score = Column(Float, nullable=False, default=0.0)
    total_executions = Column(Integer, nullable=False, default=0)
    downloads = Column(Integer, nullable=False, default=0)
    status = Column(String(20), nullable=False, default="draft")  # draft, published, suspended
    package_content = Column(Text, nullable=False)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)

    tenant = relationship("Tenant", foreign_keys=[tenant_id])
    creator_tenant = relationship("Tenant", foreign_keys=[creator_tenant_id])
    skill = relationship("Skill")

    def __repr__(self):
        return f"<AgentPackage {self.id} {self.name}@{self.version}>"
