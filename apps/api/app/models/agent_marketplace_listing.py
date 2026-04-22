import uuid
from datetime import datetime
from sqlalchemy import Column, String, Boolean, Integer, Numeric, DateTime, ForeignKey, Text
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.orm import relationship

from app.db.base import Base


class AgentMarketplaceListing(Base):
    __tablename__ = "agent_marketplace_listings"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    agent_id = Column(UUID(as_uuid=True), ForeignKey("agents.id", ondelete="CASCADE"), nullable=False)
    publisher_tenant_id = Column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name = Column(String(200), nullable=False)
    description = Column(Text, nullable=True)
    capabilities = Column(JSONB, nullable=False, default=list)
    protocol = Column(String(30), nullable=False)  # openai_chat|mcp_sse|webhook|a2a
    endpoint_url = Column(String(500), nullable=True)
    pricing_model = Column(String(20), nullable=False, default="free")
    price_per_call_usd = Column(Numeric(10, 4), nullable=True)
    install_count = Column(Integer, nullable=False, default=0)
    avg_rating = Column(Numeric(3, 2), nullable=True)
    public = Column(Boolean, nullable=False, default=False)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)

    agent = relationship("Agent", foreign_keys=[agent_id])
    publisher_tenant = relationship("Tenant", foreign_keys=[publisher_tenant_id])


class AgentMarketplaceSubscription(Base):
    __tablename__ = "agent_marketplace_subscriptions"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    listing_id = Column(
        UUID(as_uuid=True),
        ForeignKey("agent_marketplace_listings.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    subscriber_tenant_id = Column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    external_agent_id = Column(
        UUID(as_uuid=True), ForeignKey("external_agents.id", ondelete="SET NULL"), nullable=True
    )
    status = Column(String(20), nullable=False, default="pending")  # pending|approved|revoked|denied
    call_count = Column(Integer, nullable=False, default=0)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)

    listing = relationship("AgentMarketplaceListing", foreign_keys=[listing_id])
    subscriber_tenant = relationship("Tenant", foreign_keys=[subscriber_tenant_id])
    external_agent = relationship("ExternalAgent", foreign_keys=[external_agent_id])
