import uuid
from datetime import datetime
from sqlalchemy import Column, String, DateTime, ForeignKey
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship

from app.db.base import Base


class AgentPermission(Base):
    __tablename__ = "agent_permissions"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    agent_id = Column(UUID(as_uuid=True), ForeignKey("agents.id", ondelete="CASCADE"), nullable=False, index=True)
    tenant_id = Column(UUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False, index=True)
    principal_type = Column(String(20), nullable=False)  # 'user' | 'team' | 'role'
    principal_id = Column(UUID(as_uuid=True), nullable=False)  # user_id, agent_group_id, or role string encoded as UUID
    permission = Column(String(20), nullable=False)  # 'invoke' | 'edit' | 'promote' | 'deprecate' | 'admin'
    granted_by = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)

    # Relationships
    tenant = relationship("Tenant", foreign_keys=[tenant_id])
    agent = relationship("Agent", foreign_keys=[agent_id])
    granter = relationship("User", foreign_keys=[granted_by])
