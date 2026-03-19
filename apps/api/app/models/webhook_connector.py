"""Webhook connector models for universal inbound/outbound webhook support."""
import uuid
import secrets
from datetime import datetime
from sqlalchemy import Boolean, Column, DateTime, ForeignKey, Integer, String, JSON
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship

from app.db.base import Base


class WebhookConnector(Base):
    __tablename__ = "webhook_connectors"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id = Column(UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True)
    name = Column(String(255), nullable=False)
    description = Column(String, nullable=True)
    direction = Column(String(10), nullable=False)  # inbound, outbound
    slug = Column(String(64), unique=True, nullable=True, index=True)  # inbound URL path
    target_url = Column(String, nullable=True)  # outbound destination
    events = Column(JSON, nullable=False, default=list)  # ["entity.created", "lead.scored", "*"]
    headers = Column(JSON, nullable=True)  # outbound custom headers
    auth_type = Column(String(20), default="none")  # none, hmac_sha256, bearer, basic
    secret = Column(String, nullable=True)  # encrypted HMAC secret or bearer token
    payload_transform = Column(JSON, nullable=True)  # field mapping {"out_key": "$.data.field"}
    enabled = Column(Boolean, default=True)
    status = Column(String(20), default="active")  # active, paused, error
    last_triggered_at = Column(DateTime, nullable=True)
    trigger_count = Column(Integer, default=0)
    error_count = Column(Integer, default=0)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    tenant = relationship("Tenant")

    @staticmethod
    def generate_slug() -> str:
        return secrets.token_urlsafe(24)

    def __repr__(self):
        return f"<WebhookConnector {self.id} {self.direction}:{self.name}>"


class WebhookDeliveryLog(Base):
    __tablename__ = "webhook_delivery_logs"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id = Column(UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True)
    webhook_connector_id = Column(UUID(as_uuid=True), ForeignKey("webhook_connectors.id", ondelete="CASCADE"), nullable=False, index=True)
    direction = Column(String(10), nullable=False)  # inbound, outbound
    event_type = Column(String(100), nullable=False)
    payload = Column(JSON, nullable=True)
    response_status = Column(Integer, nullable=True)
    response_body = Column(String, nullable=True)
    success = Column(Boolean, default=False)
    error_message = Column(String, nullable=True)
    duration_ms = Column(Integer, nullable=True)
    attempt = Column(Integer, default=1)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False, index=True)

    webhook_connector = relationship("WebhookConnector")
    tenant = relationship("Tenant")

    def __repr__(self):
        return f"<WebhookDeliveryLog {self.id} {self.direction}:{self.event_type}>"
