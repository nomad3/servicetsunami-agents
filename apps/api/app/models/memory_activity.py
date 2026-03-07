"""MemoryActivity model for tracking Luna's memory events and actions."""
import uuid
from datetime import datetime
from sqlalchemy import Column, String, Text, DateTime, ForeignKey, JSON
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship

from app.db.base import Base


class MemoryActivity(Base):
    """Tracks all memory-related events: entity extraction, memory creation,
    action triggers, recalls, etc. Luna's audit log."""
    __tablename__ = "memory_activities"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id = Column(UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True)

    # Event details
    event_type = Column(String(50), nullable=False, index=True)
    # Types: entity_created, entity_updated, entity_deleted, relation_created,
    #        memory_created, memory_updated, action_triggered, action_completed,
    #        action_failed, recall_used
    description = Column(Text, nullable=False)
    source = Column(String(50), nullable=True)  # chat, gmail, whatsapp, calendar, manual
    event_metadata = Column("metadata", JSON, nullable=True)  # Extra context

    # Attribution
    agent_id = Column(UUID(as_uuid=True), ForeignKey("agents.id", ondelete="SET NULL"), nullable=True)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    task_id = Column(UUID(as_uuid=True), ForeignKey("agent_tasks.id", ondelete="SET NULL"), nullable=True)

    # Optional references
    entity_id = Column(UUID(as_uuid=True), ForeignKey("knowledge_entities.id", ondelete="SET NULL"), nullable=True)
    memory_id = Column(UUID(as_uuid=True), ForeignKey("agent_memories.id", ondelete="SET NULL"), nullable=True)
    workflow_run_id = Column(String(100), nullable=True)  # Temporal workflow run ID
    change_delta = Column(JSON, nullable=True)  # Before/after diff for updates

    created_at = Column(DateTime, default=datetime.utcnow, nullable=False, index=True)

    # Relationships
    tenant = relationship("Tenant")
    agent = relationship("Agent")
    user = relationship("User")
    task = relationship("AgentTask")
    entity = relationship("KnowledgeEntity")
    memory = relationship("AgentMemory")

    def __repr__(self):
        return f"<MemoryActivity {self.id} type={self.event_type}>"
