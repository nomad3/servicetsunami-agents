from __future__ import annotations

from datetime import datetime
from typing import Optional
import uuid

from pydantic import BaseModel


class ChatSessionBase(BaseModel):
    title: Optional[str] = None


class ChatSessionCreate(ChatSessionBase):
    dataset_id: Optional[uuid.UUID] = None
    dataset_group_id: Optional[uuid.UUID] = None
    agent_kit_id: Optional[uuid.UUID] = None


class ChatSession(ChatSessionBase):
    id: uuid.UUID
    dataset_id: uuid.UUID | None = None
    dataset_group_id: uuid.UUID | None = None
    agent_kit_id: uuid.UUID | None = None
    source: str = "native"
    external_id: str | None = None
    created_at: datetime

    class Config:
        from_attributes = True


class ChatMessageBase(BaseModel):
    content: str


class ChatMessageCreate(ChatMessageBase):
    pass


class ChatMessage(ChatMessageBase):
    id: uuid.UUID
    session_id: uuid.UUID
    role: str
    context: dict | None = None
    created_at: datetime

    class Config:
        from_attributes = True


class ChatTurn(BaseModel):
    user_message: ChatMessage
    assistant_message: ChatMessage
