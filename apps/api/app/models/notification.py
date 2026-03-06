"""Notification model for proactive alerts from Luna."""
import uuid
from datetime import datetime
from sqlalchemy import Boolean, Column, DateTime, ForeignKey, String, Text, JSON
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship

from app.db.base import Base


class Notification(Base):
    __tablename__ = "notifications"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id = Column(UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True)

    title = Column(String(255), nullable=False)
    body = Column(Text, nullable=True)
    source = Column(String(50), nullable=False)  # gmail, calendar, whatsapp, system
    priority = Column(String(20), nullable=False, default="medium")  # high, medium, low

    read = Column(Boolean, default=False, nullable=False)
    dismissed = Column(Boolean, default=False, nullable=False)

    reference_id = Column(String(255), nullable=True)  # email message_id, event_id, etc.
    reference_type = Column(String(50), nullable=True)  # email, event, reminder
    event_metadata = Column("metadata", JSON, nullable=True)

    created_at = Column(DateTime, default=datetime.utcnow, nullable=False, index=True)

    tenant = relationship("Tenant")

    def __repr__(self):
        return f"<Notification {self.id} {self.priority}:{self.source}>"
