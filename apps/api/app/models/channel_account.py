import uuid
from sqlalchemy import Column, String, Boolean, Integer, ForeignKey, JSON, DateTime, LargeBinary
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship
from datetime import datetime

from app.db.base import Base


class ChannelAccount(Base):
    __tablename__ = "channel_accounts"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id = Column(UUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False, index=True)
    channel_type = Column(String, nullable=False, default="whatsapp")
    account_id = Column(String, nullable=False, default="default")
    enabled = Column(Boolean, default=False)
    dm_policy = Column(String, default="allowlist")
    allow_from = Column(JSON, default=[])
    config = Column(JSON, default={})
    status = Column(String, default="disconnected")  # disconnected, connecting, connected, pairing, error, logged_out
    connected_at = Column(DateTime, nullable=True)
    disconnected_at = Column(DateTime, nullable=True)
    last_error = Column(String, nullable=True)
    reconnect_attempts = Column(Integer, default=0)
    phone_number = Column(String, nullable=True)
    display_name = Column(String, nullable=True)
    session_blob = Column(LargeBinary, nullable=True)  # gzip-compressed neonize SQLite DB
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    tenant = relationship("Tenant")
