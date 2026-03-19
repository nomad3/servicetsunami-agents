-- 048: MCP Server Connectors
-- Adds mcp_server_connectors and mcp_server_call_logs tables for
-- connecting external MCP servers and proxying tool calls.

CREATE TABLE IF NOT EXISTS mcp_server_connectors (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    name VARCHAR(255) NOT NULL,
    description TEXT,
    server_url TEXT NOT NULL,
    transport VARCHAR(20) NOT NULL DEFAULT 'sse' CHECK (transport IN ('sse', 'streamable-http', 'stdio')),
    auth_type VARCHAR(20) NOT NULL DEFAULT 'none' CHECK (auth_type IN ('none', 'bearer', 'api_key', 'basic')),
    auth_token TEXT,
    auth_header VARCHAR(100),
    custom_headers JSONB,
    tools_discovered JSONB,
    tool_count INTEGER NOT NULL DEFAULT 0,
    enabled BOOLEAN NOT NULL DEFAULT true,
    status VARCHAR(20) NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'connected', 'error', 'disconnected')),
    last_health_check TIMESTAMP,
    last_error TEXT,
    call_count INTEGER NOT NULL DEFAULT 0,
    error_count INTEGER NOT NULL DEFAULT 0,
    created_at TIMESTAMP NOT NULL DEFAULT now(),
    updated_at TIMESTAMP DEFAULT now()
);

CREATE INDEX IF NOT EXISTS ix_mcp_server_connectors_tenant_id ON mcp_server_connectors(tenant_id);
CREATE INDEX IF NOT EXISTS ix_mcp_server_connectors_tenant_enabled ON mcp_server_connectors(tenant_id) WHERE enabled = true;

CREATE TABLE IF NOT EXISTS mcp_server_call_logs (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    mcp_server_connector_id UUID NOT NULL REFERENCES mcp_server_connectors(id) ON DELETE CASCADE,
    tool_name VARCHAR(255) NOT NULL,
    arguments JSONB,
    result JSONB,
    success BOOLEAN NOT NULL DEFAULT false,
    error_message TEXT,
    duration_ms INTEGER,
    created_at TIMESTAMP NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS ix_mcp_server_call_logs_connector_id ON mcp_server_call_logs(mcp_server_connector_id);
CREATE INDEX IF NOT EXISTS ix_mcp_server_call_logs_tenant_id ON mcp_server_call_logs(tenant_id);
CREATE INDEX IF NOT EXISTS ix_mcp_server_call_logs_created_at ON mcp_server_call_logs(created_at);
