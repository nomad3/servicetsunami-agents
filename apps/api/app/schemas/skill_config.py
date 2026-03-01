from pydantic import BaseModel
from typing import Optional
from datetime import datetime
import uuid


class SkillConfigBase(BaseModel):
    skill_name: str
    enabled: bool = True
    requires_approval: bool = False
    rate_limit: Optional[dict] = None
    allowed_scopes: Optional[list] = None
    llm_config_id: Optional[uuid.UUID] = None


class SkillConfigCreate(SkillConfigBase):
    instance_id: Optional[uuid.UUID] = None


class SkillConfigUpdate(BaseModel):
    enabled: Optional[bool] = None
    requires_approval: Optional[bool] = None
    rate_limit: Optional[dict] = None
    allowed_scopes: Optional[list] = None
    llm_config_id: Optional[uuid.UUID] = None


class SkillConfig(SkillConfigBase):
    id: uuid.UUID
    tenant_id: uuid.UUID
    instance_id: Optional[uuid.UUID] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    class Config:
        from_attributes = True


class CredentialCreate(BaseModel):
    credential_key: str
    value: str  # plaintext, will be encrypted by vault
    credential_type: str = "api_key"


class CredentialOut(BaseModel):
    id: uuid.UUID
    credential_key: str
    credential_type: str
    status: str
    last_used_at: Optional[datetime] = None
    created_at: Optional[datetime] = None

    class Config:
        from_attributes = True


class SkillRegistryEntry(BaseModel):
    skill_name: str
    display_name: str
    description: str
    icon: str
    credentials: list  # list of {key, label, type, required}
    channel_type: Optional[str] = None  # "baileys" for WhatsApp channel
    auth_type: str = "manual"  # "manual" | "oauth"
    oauth_provider: Optional[str] = None  # "google" | "github" | "linkedin"
