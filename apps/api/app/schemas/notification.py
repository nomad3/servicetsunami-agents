"""Pydantic schemas for Notification."""
from datetime import datetime
from typing import Optional, Dict, Any
from uuid import UUID
from pydantic import BaseModel


class NotificationInDB(BaseModel):
    id: UUID
    tenant_id: UUID
    title: str
    body: Optional[str] = None
    source: str
    priority: str
    read: bool
    dismissed: bool
    reference_id: Optional[str] = None
    reference_type: Optional[str] = None
    event_metadata: Optional[Dict[str, Any]] = None
    created_at: datetime

    class Config:
        from_attributes = True


class NotificationCount(BaseModel):
    unread: int
