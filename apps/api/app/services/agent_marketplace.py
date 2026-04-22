import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy.orm import Session

from app.models.agent import Agent
from app.models.agent_marketplace_listing import (
    AgentMarketplaceListing,
    AgentMarketplaceSubscription,
)
from app.models.external_agent import ExternalAgent


def publish(
    db: Session,
    *,
    agent_id: uuid.UUID,
    publisher_tenant_id: uuid.UUID,
    protocol: str,
    endpoint_url: Optional[str],
    pricing_model: str = "free",
    price_per_call_usd: Optional[float] = None,
    public: bool = True,
    override_name: Optional[str] = None,
    override_description: Optional[str] = None,
) -> AgentMarketplaceListing:
    agent = (
        db.query(Agent)
        .filter(Agent.id == agent_id, Agent.tenant_id == publisher_tenant_id)
        .first()
    )
    if not agent:
        raise ValueError("Agent not found for publisher tenant")
    if agent.status != "production":
        raise ValueError("Only production agents can be published")
    if not endpoint_url:
        # Every supported protocol (openai_chat, mcp_sse, webhook, a2a) is endpoint-driven;
        # a listing without one is an unusable stub for subscribers.
        raise ValueError("endpoint_url is required to publish")

    existing = (
        db.query(AgentMarketplaceListing)
        .filter(
            AgentMarketplaceListing.agent_id == agent.id,
            AgentMarketplaceListing.publisher_tenant_id == publisher_tenant_id,
        )
        .first()
    )
    if existing:
        raise ValueError("This agent already has a marketplace listing")

    listing = AgentMarketplaceListing(
        agent_id=agent.id,
        publisher_tenant_id=publisher_tenant_id,
        name=override_name or agent.name,
        description=override_description or agent.description,
        capabilities=agent.capabilities or [],
        protocol=protocol,
        endpoint_url=endpoint_url,
        pricing_model=pricing_model,
        price_per_call_usd=price_per_call_usd,
        public=public,
    )
    db.add(listing)
    db.commit()
    db.refresh(listing)
    return listing


def subscribe(
    db: Session,
    *,
    listing_id: uuid.UUID,
    subscriber_tenant_id: uuid.UUID,
) -> AgentMarketplaceSubscription:
    listing = db.query(AgentMarketplaceListing).filter(AgentMarketplaceListing.id == listing_id).first()
    if not listing or not listing.public:
        raise ValueError("Listing not found or not public")
    if listing.publisher_tenant_id == subscriber_tenant_id:
        raise ValueError("Cannot subscribe to your own listing")

    existing = (
        db.query(AgentMarketplaceSubscription)
        .filter(
            AgentMarketplaceSubscription.listing_id == listing_id,
            AgentMarketplaceSubscription.subscriber_tenant_id == subscriber_tenant_id,
        )
        .first()
    )
    if existing:
        return existing

    # Free listings auto-approve; priced listings stay pending until publisher approves.
    auto_approve = listing.pricing_model == "free"

    external_agent = None
    if auto_approve and listing.endpoint_url:
        external_agent = ExternalAgent(
            tenant_id=subscriber_tenant_id,
            name=listing.name,
            description=listing.description,
            protocol=listing.protocol,
            endpoint_url=listing.endpoint_url,
            capabilities=listing.capabilities or [],
            metadata_={"source": "marketplace", "listing_id": str(listing.id)},
        )
        db.add(external_agent)
        db.flush()

    subscription = AgentMarketplaceSubscription(
        listing_id=listing.id,
        subscriber_tenant_id=subscriber_tenant_id,
        external_agent_id=external_agent.id if external_agent else None,
        status="approved" if auto_approve else "pending",
    )
    db.add(subscription)

    if auto_approve:
        listing.install_count = (listing.install_count or 0) + 1

    db.commit()
    db.refresh(subscription)
    return subscription


def approve_subscription(
    db: Session,
    *,
    subscription_id: uuid.UUID,
    publisher_tenant_id: uuid.UUID,
) -> AgentMarketplaceSubscription:
    sub = (
        db.query(AgentMarketplaceSubscription)
        .join(AgentMarketplaceListing, AgentMarketplaceListing.id == AgentMarketplaceSubscription.listing_id)
        .filter(
            AgentMarketplaceSubscription.id == subscription_id,
            AgentMarketplaceListing.publisher_tenant_id == publisher_tenant_id,
        )
        .first()
    )
    if not sub:
        raise ValueError("Subscription not found")
    if sub.status != "pending":
        return sub

    listing = db.query(AgentMarketplaceListing).filter(AgentMarketplaceListing.id == sub.listing_id).first()
    if listing and listing.endpoint_url and not sub.external_agent_id:
        external_agent = ExternalAgent(
            tenant_id=sub.subscriber_tenant_id,
            name=listing.name,
            description=listing.description,
            protocol=listing.protocol,
            endpoint_url=listing.endpoint_url,
            capabilities=listing.capabilities or [],
            metadata_={"source": "marketplace", "listing_id": str(listing.id)},
        )
        db.add(external_agent)
        db.flush()
        sub.external_agent_id = external_agent.id

    sub.status = "approved"
    sub.updated_at = datetime.utcnow()
    if listing:
        listing.install_count = (listing.install_count or 0) + 1
    db.commit()
    db.refresh(sub)
    return sub


def revoke_subscription(
    db: Session,
    *,
    subscription_id: uuid.UUID,
    requester_tenant_id: uuid.UUID,
) -> AgentMarketplaceSubscription:
    sub = db.query(AgentMarketplaceSubscription).filter(AgentMarketplaceSubscription.id == subscription_id).first()
    if not sub:
        raise ValueError("Subscription not found")

    listing = db.query(AgentMarketplaceListing).filter(AgentMarketplaceListing.id == sub.listing_id).first()
    is_publisher = listing and listing.publisher_tenant_id == requester_tenant_id
    is_subscriber = sub.subscriber_tenant_id == requester_tenant_id
    if not (is_publisher or is_subscriber):
        raise ValueError("Not authorized to revoke this subscription")

    sub.status = "revoked"
    sub.updated_at = datetime.utcnow()
    db.commit()
    db.refresh(sub)
    return sub
