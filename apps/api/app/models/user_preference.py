"""User Preference — learned or explicit communication preferences."""
import uuid
from datetime import datetime
from sqlalchemy import Column, String, Float, Integer, DateTime, ForeignKey
from sqlalchemy.dialects.postgresql import UUID
from app.db.base import Base


class UserPreference(Base):
    __tablename__ = "user_preferences"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id = Column(UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=True)
    preference_type = Column(String(50), nullable=False)  # response_length, tone, emoji_usage, format, etc.
    value = Column(String(200), nullable=False)  # short, professional, none, bullet_points, etc.
    confidence = Column(Float, default=0.5)
    evidence_count = Column(Integer, default=1)
    updated_at = Column(DateTime, default=datetime.utcnow, nullable=False)
