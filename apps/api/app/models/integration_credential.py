import uuid
from sqlalchemy import Column, String, ForeignKey, DateTime
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship
from datetime import datetime

from app.db.base import Base


class IntegrationCredential(Base):
    """Encrypted credential storage for integration configurations."""
    __tablename__ = "integration_credentials"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id = Column(UUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False, index=True)
    integration_config_id = Column(UUID(as_uuid=True), ForeignKey("integration_configs.id"), nullable=False, index=True)
    credential_key = Column(String, nullable=False)  # e.g., "api_key", "oauth_token", "webhook_url"
    encrypted_value = Column(String, nullable=False)  # AES-256 encrypted string
    credential_type = Column(String, default="api_key")  # api_key, oauth_token, webhook_url, basic_auth
    status = Column(String, default="active")  # active, expired, revoked
    expires_at = Column(DateTime, nullable=True)
    last_used_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    tenant = relationship("Tenant", foreign_keys=[tenant_id])
    integration_config = relationship("IntegrationConfig", foreign_keys=[integration_config_id])

    def __repr__(self):
        return f"<IntegrationCredential {self.credential_key} type={self.credential_type}>"
