"""Pydantic schemas for STP agent packages."""
from datetime import datetime
from typing import Any, Dict, List, Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator


class AgentPackagePublish(BaseModel):
    name: str
    version: str = "0.1.0"
    package_content: str
    metadata: Optional[Dict[str, Any]] = None
    skill_id: Optional[UUID] = None
    required_tools: List[str] = Field(default_factory=list)
    required_cli: str = "any"
    pricing_tier: str = "simple"
    signature: Optional[str] = None
    creator_public_key: Optional[str] = None
    status: str = "published"

    @field_validator("required_cli")
    @classmethod
    def validate_required_cli(cls, v):
        if v not in ("claude_code", "codex", "gemini_cli", "any"):
            raise ValueError("required_cli must be 'claude_code', 'codex', 'gemini_cli', or 'any'")
        return v

    @field_validator("status")
    @classmethod
    def validate_status(cls, v):
        if v not in ("draft", "published", "suspended"):
            raise ValueError("status must be 'draft', 'published', or 'suspended'")
        return v


class AgentPackageInDB(BaseModel):
    model_config = ConfigDict(from_attributes=True, populate_by_name=True)

    id: UUID
    tenant_id: UUID
    creator_tenant_id: UUID
    name: str
    version: str
    content_hash: str
    signature: Optional[str] = None
    creator_public_key: Optional[str] = None
    skill_id: Optional[UUID] = None
    metadata: Optional[Dict[str, Any]] = Field(default=None, alias="package_metadata")
    required_tools: Optional[List[str]] = None
    required_cli: str
    pricing_tier: str
    quality_score: float
    total_executions: int
    downloads: int
    status: str
    created_at: datetime
    updated_at: Optional[datetime] = None


class AgentPackageDownload(BaseModel):
    id: UUID
    name: str
    version: str
    content_hash: str
    package_content: str
    signature: Optional[str] = None
    creator_public_key: Optional[str] = None
    metadata: Optional[Dict[str, Any]] = None


class AgentPackageVerifyResponse(BaseModel):
    package_id: UUID
    content_hash: str
    hash_verified: bool
    signature_verified: bool
