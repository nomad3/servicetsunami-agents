import uuid
from datetime import datetime
from sqlalchemy import Column, String, Boolean, DateTime, ForeignKey
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.orm import relationship

from app.db.base import Base


class AgentPolicy(Base):
    __tablename__ = "agent_policies"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    agent_id = Column(UUID(as_uuid=True), ForeignKey("agents.id", ondelete="CASCADE"), nullable=True, index=True)
    # NULL agent_id means policy applies to ALL agents in the tenant
    tenant_id = Column(UUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False, index=True)
    policy_type = Column(String(30), nullable=False, index=True)
    # policy_type values:
    #   'output_filter'  — config: {"blocked_patterns": ["competitor_name", ...], "action": "block"|"redact"}
    #   'input_filter'   — config: {"blocked_patterns": [...], "action": "block"|"warn"}
    #   'data_access'    — config: {"denied_table_patterns": [".*pii.*"], "denied_columns": [...]}
    #   'rate_limit'     — config: {"max_calls_per_user_per_hour": 20, "max_calls_per_tenant_per_hour": 200}
    config = Column(JSONB, nullable=False, default=dict)
    enabled = Column(Boolean, nullable=False, default=True)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relationships
    tenant = relationship("Tenant", foreign_keys=[tenant_id])
    agent = relationship("Agent", foreign_keys=[agent_id])
