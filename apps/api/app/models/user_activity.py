"""User activity events for workflow pattern detection."""
import uuid
from datetime import datetime
from sqlalchemy import Column, String, Float, DateTime, ForeignKey
from sqlalchemy.dialects.postgresql import UUID, JSONB
from app.db.base import Base


class UserActivity(Base):
    __tablename__ = "user_activities"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id = Column(UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), index=True)
    event_type = Column(String(50), nullable=False)  # app_switch, clipboard_copy, file_open, url_visit
    source_shell = Column(String(100))  # desktop-abc123, mobile, web
    app_name = Column(String(255))  # "Xcode", "Slack", "Chrome"
    window_title = Column(String(500))
    detail = Column(JSONB, default={})  # extra context (from_app, duration_secs, etc.)
    duration_secs = Column(Float)  # time spent in previous app
    created_at = Column(DateTime, default=datetime.utcnow, index=True)
