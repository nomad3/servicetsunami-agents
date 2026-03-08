import uuid
from sqlalchemy import Column, String, Boolean, Integer, ForeignKey, JSON, DateTime
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship
from datetime import datetime

from app.db.base import Base


class IntegrationConfig(Base):
    """Per-tenant integration configuration."""
    __tablename__ = "integration_configs"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id = Column(UUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False, index=True)
    skill_name = Column(String, nullable=False)  # e.g., "slack", "gmail", "github"
    account_email = Column(String, nullable=True)  # OAuth account identifier (e.g., "user@gmail.com")
    enabled = Column(Boolean, default=True)
    requires_approval = Column(Boolean, default=False)
    rate_limit = Column(JSON, nullable=True)  # e.g., {"max_calls": 100, "window_seconds": 3600}
    allowed_scopes = Column(JSON, nullable=True)  # e.g., ["read", "write"]
    llm_config_id = Column(UUID(as_uuid=True), ForeignKey("llm_configs.id"), nullable=True)
    # Usage tracking
    last_used_at = Column(DateTime, nullable=True)
    call_count = Column(Integer, default=0)
    success_count = Column(Integer, default=0)
    error_count = Column(Integer, default=0)

    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    tenant = relationship("Tenant", foreign_keys=[tenant_id])
    llm_config = relationship("LLMConfig", foreign_keys=[llm_config_id])

    def __repr__(self):
        return f"<IntegrationConfig {self.skill_name} tenant={self.tenant_id}>"
