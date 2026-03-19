"""Pydantic schemas for Webhook Connectors."""
from datetime import datetime
from typing import Optional, List, Dict, Any
from uuid import UUID
from pydantic import BaseModel, field_validator


class WebhookConnectorCreate(BaseModel):
    name: str
    description: Optional[str] = None
    direction: str  # inbound, outbound
    events: List[str] = []  # ["entity.created", "lead.scored", "*"]
    target_url: Optional[str] = None  # required for outbound
    headers: Optional[Dict[str, str]] = None
    auth_type: str = "none"  # none, hmac_sha256, bearer, basic
    secret: Optional[str] = None  # HMAC secret or bearer/basic token
    payload_transform: Optional[Dict[str, str]] = None
    enabled: bool = True

    @field_validator("direction")
    @classmethod
    def validate_direction(cls, v):
        if v not in ("inbound", "outbound"):
            raise ValueError("direction must be 'inbound' or 'outbound'")
        return v


class WebhookConnectorUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    events: Optional[List[str]] = None
    target_url: Optional[str] = None
    headers: Optional[Dict[str, str]] = None
    auth_type: Optional[str] = None
    secret: Optional[str] = None
    payload_transform: Optional[Dict[str, str]] = None
    enabled: Optional[bool] = None


class WebhookConnectorInDB(BaseModel):
    id: UUID
    tenant_id: UUID
    name: str
    description: Optional[str] = None
    direction: str
    slug: Optional[str] = None
    target_url: Optional[str] = None
    events: List[str] = []
    headers: Optional[Dict[str, str]] = None
    auth_type: str = "none"
    payload_transform: Optional[Dict[str, str]] = None
    enabled: bool = True
    status: str = "active"
    last_triggered_at: Optional[datetime] = None
    trigger_count: int = 0
    error_count: int = 0
    created_at: datetime
    updated_at: Optional[datetime] = None

    class Config:
        from_attributes = True


class WebhookDeliveryLogInDB(BaseModel):
    id: UUID
    tenant_id: UUID
    webhook_connector_id: UUID
    direction: str
    event_type: str
    payload: Optional[Dict[str, Any]] = None
    response_status: Optional[int] = None
    response_body: Optional[str] = None
    success: bool = False
    error_message: Optional[str] = None
    duration_ms: Optional[int] = None
    attempt: int = 1
    created_at: datetime

    class Config:
        from_attributes = True


class WebhookTestRequest(BaseModel):
    payload: Optional[Dict[str, Any]] = None
    event_type: str = "webhook.test"


class WebhookFireRequest(BaseModel):
    event_type: str
    payload: Dict[str, Any] = {}
