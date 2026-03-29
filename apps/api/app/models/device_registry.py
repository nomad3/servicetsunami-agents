"""Device Registry — tracks connected IoT devices, cameras, robots."""
import uuid
from datetime import datetime

from sqlalchemy import Column, String, DateTime, ForeignKey, Boolean
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.orm import relationship

from app.db.base import Base


class DeviceRegistry(Base):
    __tablename__ = "device_registry"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id = Column(UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True)
    device_id = Column(String(100), nullable=False, unique=True)
    device_name = Column(String(200), nullable=False)
    device_type = Column(String(50), nullable=False)  # camera, robot, necklace, glasses, sensor
    status = Column(String(20), default="offline")  # online, offline, pairing, error
    device_token_hash = Column(String(200), nullable=True)
    last_heartbeat = Column(DateTime, nullable=True)
    capabilities = Column(JSONB, default=list)  # ["video", "audio", "motion", "temperature"]
    config = Column(JSONB, default=dict)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    tenant = relationship("Tenant")

    def __repr__(self):
        return f"<DeviceRegistry {self.device_id} ({self.device_type}) status={self.status}>"
