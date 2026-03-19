"""MCP server connector models for connecting external MCP servers."""
import uuid
from datetime import datetime
from sqlalchemy import Boolean, Column, DateTime, ForeignKey, Integer, String, JSON
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship

from app.db.base import Base


class MCPServerConnector(Base):
    __tablename__ = "mcp_server_connectors"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id = Column(UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True)
    name = Column(String(255), nullable=False)
    description = Column(String, nullable=True)
    server_url = Column(String, nullable=False)  # http://host:port/sse or stdio command
    transport = Column(String(20), nullable=False, default="sse")  # sse, streamable-http, stdio
    auth_type = Column(String(20), default="none")  # none, bearer, api_key, basic
    auth_token = Column(String, nullable=True)  # bearer token, API key, or basic creds
    auth_header = Column(String(100), nullable=True)  # custom header name (default: Authorization)
    custom_headers = Column(JSON, nullable=True)  # extra headers for HTTP transports
    tools_discovered = Column(JSON, nullable=True)  # cached list of tools from last discovery
    tool_count = Column(Integer, default=0)
    enabled = Column(Boolean, default=True)
    status = Column(String(20), default="pending")  # pending, connected, error, disconnected
    last_health_check = Column(DateTime, nullable=True)
    last_error = Column(String, nullable=True)
    call_count = Column(Integer, default=0)
    error_count = Column(Integer, default=0)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    tenant = relationship("Tenant")

    def __repr__(self):
        return f"<MCPServerConnector {self.id} {self.transport}:{self.name}>"


class MCPServerCallLog(Base):
    __tablename__ = "mcp_server_call_logs"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id = Column(UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True)
    mcp_server_connector_id = Column(UUID(as_uuid=True), ForeignKey("mcp_server_connectors.id", ondelete="CASCADE"), nullable=False, index=True)
    tool_name = Column(String(255), nullable=False)
    arguments = Column(JSON, nullable=True)
    result = Column(JSON, nullable=True)
    success = Column(Boolean, default=False)
    error_message = Column(String, nullable=True)
    duration_ms = Column(Integer, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False, index=True)

    mcp_server_connector = relationship("MCPServerConnector")
    tenant = relationship("Tenant")

    def __repr__(self):
        return f"<MCPServerCallLog {self.id} {self.tool_name}>"
