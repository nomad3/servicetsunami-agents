from pydantic import BaseModel
from typing import Optional
from datetime import datetime
import uuid


class IntegrationConfigBase(BaseModel):
    integration_name: str
    account_email: Optional[str] = None
    enabled: bool = True
    requires_approval: bool = False
    rate_limit: Optional[dict] = None
    allowed_scopes: Optional[list] = None
    llm_config_id: Optional[uuid.UUID] = None


class IntegrationConfigCreate(IntegrationConfigBase):
    instance_id: Optional[uuid.UUID] = None


class IntegrationConfigUpdate(BaseModel):
    enabled: Optional[bool] = None
    requires_approval: Optional[bool] = None
    rate_limit: Optional[dict] = None
    allowed_scopes: Optional[list] = None
    llm_config_id: Optional[uuid.UUID] = None


class IntegrationConfig(IntegrationConfigBase):
    id: uuid.UUID
    tenant_id: uuid.UUID
    account_email: Optional[str] = None
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


class IntegrationRegistryEntry(BaseModel):
    integration_name: str
    display_name: str
    description: str
    icon: str
    credentials: list  # list of {key, label, type, required}
    auth_type: str = "manual"  # "manual" | "oauth"
    oauth_provider: Optional[str] = None  # "google" | "github" | "linkedin" | "microsoft"
