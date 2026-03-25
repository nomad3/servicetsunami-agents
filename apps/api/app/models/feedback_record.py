"""Feedback records model — human responses to morning reports."""
import uuid
from datetime import datetime
from sqlalchemy import Column, String, Text, Boolean, TIMESTAMP
from sqlalchemy.dialects.postgresql import UUID
from app.db.base_class import Base


class FeedbackRecord(Base):
    __tablename__ = "feedback_records"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id = Column(UUID(as_uuid=True), nullable=False)
    report_id = Column(Text)
    candidate_id = Column(UUID(as_uuid=True))
    feedback_type = Column(String, nullable=False)
    content = Column(Text, nullable=False)
    parsed_intent = Column(Text)
    applied = Column(Boolean, nullable=False, default=False)
    created_at = Column(TIMESTAMP, nullable=False, default=datetime.utcnow)
