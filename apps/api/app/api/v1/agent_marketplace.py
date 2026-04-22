import uuid
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api import deps
from app.models.agent_marketplace_listing import (
    AgentMarketplaceListing,
    AgentMarketplaceSubscription,
)
from app.models.user import User
from app.services import agent_marketplace

router = APIRouter()


class ListingOut(BaseModel):
    id: uuid.UUID
    agent_id: uuid.UUID
    publisher_tenant_id: uuid.UUID
    name: str
    description: Optional[str] = None
    capabilities: list = Field(default_factory=list)
    protocol: str
    endpoint_url: Optional[str] = None
    pricing_model: str
    price_per_call_usd: Optional[float] = None
    install_count: int
    avg_rating: Optional[float] = None
    public: bool

    class Config:
        from_attributes = True


class PublishRequest(BaseModel):
    agent_id: uuid.UUID
    protocol: str  # openai_chat|mcp_sse|webhook|a2a
    endpoint_url: Optional[str] = None
    pricing_model: str = "free"
    price_per_call_usd: Optional[float] = None
    public: bool = True
    name: Optional[str] = None
    description: Optional[str] = None


class SubscribeRequest(BaseModel):
    listing_id: uuid.UUID


class SubscriptionOut(BaseModel):
    id: uuid.UUID
    listing_id: uuid.UUID
    subscriber_tenant_id: uuid.UUID
    external_agent_id: Optional[uuid.UUID] = None
    status: str
    call_count: int

    class Config:
        from_attributes = True


@router.get("/listings", response_model=List[ListingOut])
def list_public_listings(
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(deps.get_current_active_user),
    skip: int = 0,
    limit: int = 50,
):
    """Browse the cross-tenant marketplace. Returns public listings from other tenants."""
    return (
        db.query(AgentMarketplaceListing)
        .filter(
            AgentMarketplaceListing.public.is_(True),
            AgentMarketplaceListing.publisher_tenant_id != current_user.tenant_id,
        )
        .order_by(AgentMarketplaceListing.install_count.desc())
        .offset(max(0, skip))
        .limit(max(1, min(limit, 100)))
        .all()
    )


@router.get("/my-listings", response_model=List[ListingOut])
def my_listings(
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(deps.get_current_active_user),
):
    """Listings published by the current tenant."""
    return (
        db.query(AgentMarketplaceListing)
        .filter(AgentMarketplaceListing.publisher_tenant_id == current_user.tenant_id)
        .all()
    )


@router.post("/listings", response_model=ListingOut, status_code=status.HTTP_201_CREATED)
def publish_listing(
    body: PublishRequest,
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(deps.get_current_active_user),
):
    try:
        listing = agent_marketplace.publish(
            db,
            agent_id=body.agent_id,
            publisher_tenant_id=current_user.tenant_id,
            protocol=body.protocol,
            endpoint_url=body.endpoint_url,
            pricing_model=body.pricing_model,
            price_per_call_usd=body.price_per_call_usd,
            public=body.public,
            override_name=body.name,
            override_description=body.description,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return listing


@router.delete("/listings/{listing_id}")
def unpublish_listing(
    listing_id: uuid.UUID,
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(deps.get_current_active_user),
):
    listing = (
        db.query(AgentMarketplaceListing)
        .filter(
            AgentMarketplaceListing.id == listing_id,
            AgentMarketplaceListing.publisher_tenant_id == current_user.tenant_id,
        )
        .first()
    )
    if not listing:
        raise HTTPException(status_code=404, detail="Listing not found")

    # Revoke every downstream ExternalAgent row so subscribers can't keep calling the
    # publisher's endpoint after the listing is removed. The subscriptions themselves
    # cascade via the FK; the external agents don't, since they're owned by the
    # subscriber tenant and only soft-linked via metadata_.
    from app.models.external_agent import ExternalAgent

    subs = (
        db.query(AgentMarketplaceSubscription)
        .filter(AgentMarketplaceSubscription.listing_id == listing_id)
        .all()
    )
    for sub in subs:
        if sub.external_agent_id:
            ea = db.query(ExternalAgent).filter(ExternalAgent.id == sub.external_agent_id).first()
            if ea:
                db.delete(ea)

    db.delete(listing)
    db.commit()
    return {"deleted": True, "revoked_subscribers": len(subs)}


@router.post("/subscribe", response_model=SubscriptionOut, status_code=status.HTTP_201_CREATED)
def subscribe(
    body: SubscribeRequest,
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(deps.get_current_active_user),
):
    try:
        sub = agent_marketplace.subscribe(
            db,
            listing_id=body.listing_id,
            subscriber_tenant_id=current_user.tenant_id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return sub


@router.get("/my-subscriptions", response_model=List[SubscriptionOut])
def my_subscriptions(
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(deps.get_current_active_user),
):
    return (
        db.query(AgentMarketplaceSubscription)
        .filter(AgentMarketplaceSubscription.subscriber_tenant_id == current_user.tenant_id)
        .order_by(AgentMarketplaceSubscription.created_at.desc())
        .all()
    )


@router.get("/inbound-subscriptions", response_model=List[SubscriptionOut])
def inbound_subscriptions(
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(deps.get_current_active_user),
):
    """Subscribers to the current tenant's listings (for publisher approval queue)."""
    return (
        db.query(AgentMarketplaceSubscription)
        .join(
            AgentMarketplaceListing,
            AgentMarketplaceListing.id == AgentMarketplaceSubscription.listing_id,
        )
        .filter(AgentMarketplaceListing.publisher_tenant_id == current_user.tenant_id)
        .order_by(AgentMarketplaceSubscription.created_at.desc())
        .all()
    )


@router.post("/subscriptions/{subscription_id}/approve", response_model=SubscriptionOut)
def approve_subscription(
    subscription_id: uuid.UUID,
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(deps.get_current_active_user),
):
    try:
        return agent_marketplace.approve_subscription(
            db,
            subscription_id=subscription_id,
            publisher_tenant_id=current_user.tenant_id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.post("/subscriptions/{subscription_id}/revoke", response_model=SubscriptionOut)
def revoke_subscription(
    subscription_id: uuid.UUID,
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(deps.get_current_active_user),
):
    try:
        return agent_marketplace.revoke_subscription(
            db,
            subscription_id=subscription_id,
            requester_tenant_id=current_user.tenant_id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
