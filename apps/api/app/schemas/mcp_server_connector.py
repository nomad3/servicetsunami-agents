"""Pydantic schemas for MCP Server Connectors."""
from datetime import datetime
from typing import Optional, List, Dict, Any
from uuid import UUID
from pydantic import BaseModel, field_validator


class MCPServerConnectorCreate(BaseModel):
    name: str
    description: Optional[str] = None
    server_url: str
    transport: str = "sse"  # sse, streamable-http, stdio
    auth_type: str = "none"  # none, bearer, api_key, basic
    auth_token: Optional[str] = None
    auth_header: Optional[str] = None  # custom auth header name
    custom_headers: Optional[Dict[str, str]] = None
    enabled: bool = True

    @field_validator("transport")
    @classmethod
    def validate_transport(cls, v):
        if v not in ("sse", "streamable-http", "stdio"):
            raise ValueError("transport must be 'sse', 'streamable-http', or 'stdio'")
        return v

    @field_validator("auth_type")
    @classmethod
    def validate_auth_type(cls, v):
        if v not in ("none", "bearer", "api_key", "basic"):
            raise ValueError("auth_type must be 'none', 'bearer', 'api_key', or 'basic'")
        return v


class MCPServerConnectorUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    server_url: Optional[str] = None
    transport: Optional[str] = None
    auth_type: Optional[str] = None
    auth_token: Optional[str] = None
    auth_header: Optional[str] = None
    custom_headers: Optional[Dict[str, str]] = None
    enabled: Optional[bool] = None


class MCPServerConnectorInDB(BaseModel):
    id: UUID
    tenant_id: UUID
    name: str
    description: Optional[str] = None
    server_url: str
    transport: str = "sse"
    auth_type: str = "none"
    auth_header: Optional[str] = None
    custom_headers: Optional[Dict[str, str]] = None
    tools_discovered: Optional[List[Dict[str, Any]]] = None
    tool_count: int = 0
    enabled: bool = True
    status: str = "pending"
    last_health_check: Optional[datetime] = None
    last_error: Optional[str] = None
    call_count: int = 0
    error_count: int = 0
    created_at: datetime
    updated_at: Optional[datetime] = None

    class Config:
        from_attributes = True


class MCPServerCallLogInDB(BaseModel):
    id: UUID
    tenant_id: UUID
    mcp_server_connector_id: UUID
    tool_name: str
    arguments: Optional[Dict[str, Any]] = None
    result: Optional[Dict[str, Any]] = None
    success: bool = False
    error_message: Optional[str] = None
    duration_ms: Optional[int] = None
    created_at: datetime

    class Config:
        from_attributes = True


class MCPServerCallRequest(BaseModel):
    tool_name: str
    arguments: Dict[str, Any] = {}


class MCPServerHealthCheckRequest(BaseModel):
    timeout: int = 10  # seconds
