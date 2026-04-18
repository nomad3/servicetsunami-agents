import uuid
from sqlalchemy import Column, String, ForeignKey, JSON, Integer, Text
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.orm import relationship

from app.db.base import Base

class Agent(Base):
    __tablename__ = "agents"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name = Column(String, index=True)
    description = Column(String, nullable=True)
    config = Column(JSON)
    tenant_id = Column(UUID(as_uuid=True), ForeignKey("tenants.id"))
    tenant = relationship("Tenant")

    # Orchestration fields
    role = Column(String, nullable=True)  # "analyst", "manager", "specialist"
    capabilities = Column(JSON, nullable=True)  # list of capability strings
    personality = Column(JSON, nullable=True)  # dict with tone, verbosity settings
    autonomy_level = Column(String, default="supervised")  # "full", "supervised", "approval_required"
    max_delegation_depth = Column(Integer, default=2)

    # LLM and Memory configuration
    llm_config_id = Column(UUID(as_uuid=True), ForeignKey("llm_configs.id"), nullable=True)
    memory_config = Column(JSON, nullable=True)  # {"retention_days": 30, "max_memories": 1000}

    # Agent-driven runtime fields
    tool_groups = Column(JSONB, nullable=True)  # list of tool group names to load
    default_model_tier = Column(String(10), default="full")  # "light" (Haiku) or "full" (Sonnet)
    persona_prompt = Column(Text, nullable=True)  # compact persona instead of full skill file
    memory_domains = Column(JSONB, nullable=True)  # list of memory domain strings for scoped recall
    escalation_agent_id = Column(UUID(as_uuid=True), ForeignKey("agents.id", ondelete="SET NULL"), nullable=True)

    # Ownership
    owner_user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True)
    team_id = Column(UUID(as_uuid=True), ForeignKey("agent_groups.id", ondelete="SET NULL"), nullable=True, index=True)

    # Lifecycle
    status = Column(String(20), nullable=False, default="production")  # draft|staging|production|deprecated
    version = Column(Integer, nullable=False, default=1)
    successor_agent_id = Column(UUID(as_uuid=True), ForeignKey("agents.id", ondelete="SET NULL"), nullable=True)

    # Relationships
    llm_config = relationship("LLMConfig", foreign_keys=[llm_config_id])
    escalation_agent = relationship("Agent", foreign_keys=[escalation_agent_id], remote_side="Agent.id", overlaps="successor")
    owner = relationship("User", foreign_keys=[owner_user_id])
    team = relationship("AgentGroup", foreign_keys=[team_id])
    successor = relationship("Agent", foreign_keys=[successor_agent_id], remote_side="Agent.id", overlaps="escalation_agent")

    # Add relationship to skills
    skills = relationship("AgentSkill", back_populates="agent")

    # Add relationship to memories
    memories = relationship("AgentMemory", back_populates="agent", cascade="all, delete-orphan")

    # Many-to-many pivot to integration configs
    agent_integration_configs = relationship(
        "AgentIntegrationConfig",
        backref="agent",
        cascade="all, delete-orphan",
        foreign_keys="AgentIntegrationConfig.agent_id",
    )
