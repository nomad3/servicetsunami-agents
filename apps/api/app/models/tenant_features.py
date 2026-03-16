"""TenantFeatures model for feature flags and limits."""
import uuid
from datetime import datetime
from sqlalchemy import Column, String, Integer, Float, Boolean, DateTime, ForeignKey
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.orm import relationship

from app.db.base import Base


class TenantFeatures(Base):
    """Tenant feature flags and usage limits."""
    __tablename__ = "tenant_features"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id = Column(UUID(as_uuid=True), ForeignKey("tenants.id"), unique=True, nullable=False)

    # Core Features
    agents_enabled = Column(Boolean, default=True)
    agent_groups_enabled = Column(Boolean, default=True)
    datasets_enabled = Column(Boolean, default=True)
    chat_enabled = Column(Boolean, default=True)
    multi_llm_enabled = Column(Boolean, default=True)
    agent_memory_enabled = Column(Boolean, default=True)

    # AI Intelligence Features
    ai_insights_enabled = Column(Boolean, default=True)
    ai_recommendations_enabled = Column(Boolean, default=True)
    ai_anomaly_detection = Column(Boolean, default=True)

    # Reinforcement Learning Features
    rl_enabled = Column(Boolean, default=True)
    rl_settings = Column(JSONB, nullable=False, default=lambda: {
        "exploration_rate": 0.1,
        "opt_in_global_learning": True,
        "use_global_baseline": True,
        "min_tenant_experiences": 50,
        "blend_alpha_growth": 0.01,
        "reward_weights": {"implicit": 0.3, "explicit": 0.5, "admin": 0.2},
        "review_schedule": "weekly",
        "per_decision_overrides": {}
    })

    # Usage Limits
    max_agents = Column(Integer, default=10)
    max_agent_groups = Column(Integer, default=5)
    monthly_token_limit = Column(Integer, default=1000000)
    storage_limit_gb = Column(Float, default=10.0)

    # UI Customization
    hide_servicetsunami_branding = Column(Boolean, default=False)

    # Plan Type
    plan_type = Column(String, default="starter")  # starter, professional, enterprise

    # LLM Provider Selection
    active_llm_provider = Column(String(50), default="gemini_llm")

    # CLI Orchestrator
    cli_orchestrator_enabled = Column(Boolean, default=False)
    default_cli_platform = Column(String(50), default="claude_code")

    # Timestamps
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relationships
    tenant = relationship("Tenant", back_populates="features")

    def __repr__(self):
        return f"<TenantFeatures {self.tenant_id} plan={self.plan_type}>"
