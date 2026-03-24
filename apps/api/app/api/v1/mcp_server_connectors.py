"""MCP server connector API routes — CRUD, tool discovery, tool call proxy, health check.

Route ordering: Static and prefixed paths (/internal/*) are registered BEFORE
parameterised paths (/{connector_id}) so that FastAPI never tries to parse
literal segments like "internal" as a UUID.
"""
import logging
from typing import Optional, List
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.api import deps
from app.models.user import User
from app.schemas.mcp_server_connector import (
    MCPServerConnectorCreate,
    MCPServerConnectorUpdate,
    MCPServerConnectorInDB,
    MCPServerCallLogInDB,
    MCPServerCallRequest,
    MCPServerHealthCheckRequest,
)
from app.services import mcp_server_connectors as svc

logger = logging.getLogger(__name__)
router = APIRouter()


# ---------------------------------------------------------------------------
# List + Create (no path param — safe anywhere)
# ---------------------------------------------------------------------------

@router.get("", response_model=List[MCPServerConnectorInDB])
def list_mcp_server_connectors(
    status: Optional[str] = None,
    skip: int = 0,
    limit: int = 100,
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(deps.get_current_active_user),
):
    """List all MCP server connectors for the current tenant."""
    return svc.list_mcp_servers(db, current_user.tenant_id, status=status, skip=skip, limit=limit)


@router.post("", response_model=MCPServerConnectorInDB, status_code=201)
def create_mcp_server_connector(
    item_in: MCPServerConnectorCreate,
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(deps.get_current_active_user),
):
    """Create a new MCP server connector."""
    connector = svc.create_mcp_server(
        db,
        tenant_id=current_user.tenant_id,
        name=item_in.name,
        server_url=item_in.server_url,
        transport=item_in.transport,
        auth_type=item_in.auth_type,
        auth_token=item_in.auth_token,
        auth_header=item_in.auth_header,
        custom_headers=item_in.custom_headers,
        description=item_in.description,
        enabled=item_in.enabled,
    )
    return connector


# ---------------------------------------------------------------------------
# Internal endpoints (for MCP tools / service-to-service, no JWT)
# ---------------------------------------------------------------------------

@router.post("/internal/create")
def create_mcp_server_internal(
    item_in: MCPServerConnectorCreate,
    tenant_id: str = "",
    db: Session = Depends(deps.get_db),
):
    """Create MCP server connector (internal, for MCP tools)."""
    if not tenant_id:
        raise HTTPException(status_code=400, detail="tenant_id query param required")
    connector = svc.create_mcp_server(
        db, tenant_id=tenant_id, name=item_in.name, server_url=item_in.server_url,
        transport=item_in.transport, auth_type=item_in.auth_type, auth_token=item_in.auth_token,
        auth_header=item_in.auth_header, custom_headers=item_in.custom_headers,
        description=item_in.description, enabled=item_in.enabled,
    )
    return MCPServerConnectorInDB.model_validate(connector).model_dump(mode="json")


@router.get("/internal/list")
def list_mcp_servers_internal(
    tenant_id: str = "",
    status: Optional[str] = None,
    db: Session = Depends(deps.get_db),
):
    """List MCP server connectors (internal, for MCP tools)."""
    if not tenant_id:
        raise HTTPException(status_code=400, detail="tenant_id query param required")
    connectors = svc.list_mcp_servers(db, tenant_id, status=status)
    return [MCPServerConnectorInDB.model_validate(c).model_dump(mode="json") for c in connectors]


@router.get("/internal/logs")
def get_all_logs_internal(
    tenant_id: str = "",
    limit: int = 50,
    db: Session = Depends(deps.get_db),
):
    """Get all MCP server call logs (internal, for MCP tools)."""
    if not tenant_id:
        raise HTTPException(status_code=400, detail="tenant_id query param required")
    logs = svc.get_call_logs(db, tenant_id, limit=limit)
    return [MCPServerCallLogInDB.model_validate(l).model_dump(mode="json") for l in logs]


@router.delete("/internal/{connector_id}")
def delete_mcp_server_internal(
    connector_id: UUID,
    tenant_id: str = "",
    db: Session = Depends(deps.get_db),
):
    """Delete MCP server connector (internal, for MCP tools)."""
    if not tenant_id:
        raise HTTPException(status_code=400, detail="tenant_id query param required")
    if not svc.delete_mcp_server(db, tenant_id, connector_id):
        raise HTTPException(status_code=404, detail="MCP server connector not found")
    return {"status": "deleted", "connector_id": str(connector_id)}


@router.post("/internal/{connector_id}/discover")
def discover_tools_internal(
    connector_id: UUID,
    tenant_id: str = "",
    timeout: int = 15,
    db: Session = Depends(deps.get_db),
):
    """Discover tools from an MCP server (internal, for MCP tools)."""
    if not tenant_id:
        raise HTTPException(status_code=400, detail="tenant_id query param required")
    connector = svc.get_mcp_server(db, tenant_id, connector_id)
    if not connector:
        raise HTTPException(status_code=404, detail="MCP server connector not found")
    return svc.discover_tools(db, connector, timeout=timeout)


@router.post("/internal/{connector_id}/call")
def call_tool_internal(
    connector_id: UUID,
    body: MCPServerCallRequest,
    tenant_id: str = "",
    db: Session = Depends(deps.get_db),
):
    """Proxy a tool call to an MCP server (internal, for MCP tools)."""
    if not tenant_id:
        raise HTTPException(status_code=400, detail="tenant_id query param required")
    connector = svc.get_mcp_server(db, tenant_id, connector_id)
    if not connector:
        raise HTTPException(status_code=404, detail="MCP server connector not found")
    if not connector.enabled:
        raise HTTPException(status_code=400, detail="MCP server connector is disabled")
    return svc.call_tool(db, connector, body.tool_name, body.arguments, channel="api")


@router.post("/internal/{connector_id}/health")
def health_check_internal(
    connector_id: UUID,
    body: MCPServerHealthCheckRequest = MCPServerHealthCheckRequest(),
    tenant_id: str = "",
    db: Session = Depends(deps.get_db),
):
    """Health check an MCP server (internal, for MCP tools)."""
    if not tenant_id:
        raise HTTPException(status_code=400, detail="tenant_id query param required")
    connector = svc.get_mcp_server(db, tenant_id, connector_id)
    if not connector:
        raise HTTPException(status_code=404, detail="MCP server connector not found")
    return svc.health_check(db, connector, timeout=body.timeout)


@router.get("/internal/{connector_id}/logs")
def get_connector_logs_internal(
    connector_id: UUID,
    tenant_id: str = "",
    limit: int = 50,
    db: Session = Depends(deps.get_db),
):
    """Get call logs for a specific MCP server (internal, for MCP tools)."""
    if not tenant_id:
        raise HTTPException(status_code=400, detail="tenant_id query param required")
    logs = svc.get_call_logs(db, tenant_id, connector_id=connector_id, limit=limit)
    return [MCPServerCallLogInDB.model_validate(l).model_dump(mode="json") for l in logs]


# ---------------------------------------------------------------------------
# Authenticated CRUD (parameterised /{connector_id} — MUST come after static paths)
# ---------------------------------------------------------------------------

@router.get("/{connector_id}", response_model=MCPServerConnectorInDB)
def get_mcp_server_connector(
    connector_id: UUID,
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(deps.get_current_active_user),
):
    """Get a single MCP server connector by ID."""
    connector = svc.get_mcp_server(db, current_user.tenant_id, connector_id)
    if not connector:
        raise HTTPException(status_code=404, detail="MCP server connector not found")
    return connector


@router.put("/{connector_id}", response_model=MCPServerConnectorInDB)
def update_mcp_server_connector(
    connector_id: UUID,
    item_in: MCPServerConnectorUpdate,
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(deps.get_current_active_user),
):
    """Update an MCP server connector."""
    updates = item_in.model_dump(exclude_unset=True)
    connector = svc.update_mcp_server(db, current_user.tenant_id, connector_id, updates)
    if not connector:
        raise HTTPException(status_code=404, detail="MCP server connector not found")
    return connector


@router.delete("/{connector_id}")
def delete_mcp_server_connector(
    connector_id: UUID,
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(deps.get_current_active_user),
):
    """Delete an MCP server connector and its call logs."""
    if not svc.delete_mcp_server(db, current_user.tenant_id, connector_id):
        raise HTTPException(status_code=404, detail="MCP server connector not found")
    return {"status": "deleted", "connector_id": str(connector_id)}


@router.get("/{connector_id}/logs", response_model=List[MCPServerCallLogInDB])
def get_connector_logs(
    connector_id: UUID,
    limit: int = 50,
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(deps.get_current_active_user),
):
    """Get call logs for a specific MCP server connector."""
    return svc.get_call_logs(db, current_user.tenant_id, connector_id=connector_id, limit=limit)


@router.post("/{connector_id}/discover")
def discover_tools_endpoint(
    connector_id: UUID,
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(deps.get_current_active_user),
):
    """Discover available tools from an MCP server."""
    connector = svc.get_mcp_server(db, current_user.tenant_id, connector_id)
    if not connector:
        raise HTTPException(status_code=404, detail="MCP server connector not found")
    return svc.discover_tools(db, connector)


@router.post("/{connector_id}/call")
def call_tool_endpoint(
    connector_id: UUID,
    body: MCPServerCallRequest,
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(deps.get_current_active_user),
):
    """Proxy a tool call to an external MCP server."""
    connector = svc.get_mcp_server(db, current_user.tenant_id, connector_id)
    if not connector:
        raise HTTPException(status_code=404, detail="MCP server connector not found")
    if not connector.enabled:
        raise HTTPException(status_code=400, detail="MCP server connector is disabled")
    return svc.call_tool(db, connector, body.tool_name, body.arguments, channel="api")


@router.post("/{connector_id}/health")
def health_check_endpoint(
    connector_id: UUID,
    body: MCPServerHealthCheckRequest = MCPServerHealthCheckRequest(),
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(deps.get_current_active_user),
):
    """Health check an MCP server connection."""
    connector = svc.get_mcp_server(db, current_user.tenant_id, connector_id)
    if not connector:
        raise HTTPException(status_code=404, detail="MCP server connector not found")
    return svc.health_check(db, connector, timeout=body.timeout)
