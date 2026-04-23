"""LLMConfig model for tenant-specific LLM configuration"""
import uuid
from sqlalchemy import Column, String, ForeignKey, JSON, Integer, Boolean, Numeric
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship

from app.db.base import Base


class LLMConfig(Base):
    """Tenant LLM configuration with routing rules and budget limits."""
    __tablename__ = "llm_configs"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id = Column(
        UUID(as_uuid=True),
        ForeignKey("tenants.id", name="fk_llm_config_tenant_id", use_alter=True),
        nullable=False
    )

    name = Column(String, nullable=False)
    is_tenant_default = Column(Boolean, default=False)

    # Model selection
    primary_model_id = Column(UUID(as_uuid=True), ForeignKey("llm_models.id"), nullable=False)
    fallback_model_id = Column(UUID(as_uuid=True), ForeignKey("llm_models.id"), nullable=True)

    # Credentials (BYOK)
    api_key_encrypted = Column(String, nullable=True)
    use_platform_key = Column(Boolean, default=True)

    # Parameters
    temperature = Column(Numeric(3, 2), default=0.7)
    max_tokens = Column(Integer, default=4096)

    # Advanced
    routing_rules = Column(JSON, nullable=True)  # Rules for task-based routing

    # Provider API Keys (BYOK)
    provider_api_keys = Column(JSON, nullable=True)  # {"openai": "sk-...", "deepseek": "sk-..."}

    # Budget
    budget_limit_daily = Column(Numeric(10, 2), nullable=True)
    budget_limit_monthly = Column(Numeric(10, 2), nullable=True)

    # Relationships
    tenant = relationship("Tenant", foreign_keys=[tenant_id])
    primary_model = relationship("LLMModel", foreign_keys=[primary_model_id])
    fallback_model = relationship("LLMModel", foreign_keys=[fallback_model_id])

    def __repr__(self):
        return f"<LLMConfig {self.name}>"
