"""Proactive actions model — Luna-initiated messages."""
import uuid
from datetime import datetime
from sqlalchemy import Column, String, Text, TIMESTAMP, Boolean
from sqlalchemy.dialects.postgresql import UUID
from app.db.base_class import Base


class ProactiveAction(Base):
    __tablename__ = "proactive_actions"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id = Column(UUID(as_uuid=True), nullable=False)
    agent_slug = Column(String, nullable=False, default="luna")
    action_type = Column(String, nullable=False)
    trigger_type = Column(String, nullable=False)
    target_ref = Column(Text)
    priority = Column(String, nullable=False, default="medium")
    content = Column(Text, nullable=False)
    channel = Column(String, nullable=False, default="notification")
    status = Column(String, nullable=False, default="pending")
    scheduled_at = Column(TIMESTAMP)
    sent_at = Column(TIMESTAMP)
    created_at = Column(TIMESTAMP, nullable=False, default=datetime.utcnow)
