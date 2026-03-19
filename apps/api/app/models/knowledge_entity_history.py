"""KnowledgeEntityHistory model for tracking entity version changes."""
import uuid
from datetime import datetime
from sqlalchemy import Column, String, Text, Integer, DateTime, ForeignKey
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.orm import relationship

from app.db.base import Base


class KnowledgeEntityHistory(Base):
    """Tracks versioned snapshots of entity changes for audit and rollback."""
    __tablename__ = "knowledge_entity_history"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    entity_id = Column(UUID(as_uuid=True), ForeignKey("knowledge_entities.id", ondelete="CASCADE"), nullable=False, index=True)
    tenant_id = Column(UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True)

    version = Column(Integer, nullable=False, default=1)
    properties_snapshot = Column(JSONB, nullable=True)
    attributes_snapshot = Column(JSONB, nullable=True)
    change_reason = Column(Text, nullable=True)
    changed_by = Column(UUID(as_uuid=True), nullable=True)
    changed_by_platform = Column(String(50), nullable=True)

    changed_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    # Relationships
    entity = relationship("KnowledgeEntity")
    tenant = relationship("Tenant")

    def __repr__(self):
        return f"<KnowledgeEntityHistory entity={self.entity_id} v{self.version}>"
