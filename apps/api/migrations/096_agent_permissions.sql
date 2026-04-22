-- Migration: 096_agent_permissions
CREATE TABLE IF NOT EXISTS agent_permissions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    agent_id UUID NOT NULL REFERENCES agents(id) ON DELETE CASCADE,
    tenant_id UUID NOT NULL REFERENCES tenants(id),
    principal_type VARCHAR(20) NOT NULL,
    principal_id UUID NOT NULL,
    permission VARCHAR(20) NOT NULL,
    granted_by UUID REFERENCES users(id) ON DELETE SET NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_agent_permissions_agent_id ON agent_permissions(agent_id);
CREATE INDEX IF NOT EXISTS idx_agent_permissions_tenant_id ON agent_permissions(tenant_id);
CREATE INDEX IF NOT EXISTS idx_agent_permissions_principal ON agent_permissions(principal_type, principal_id);

INSERT INTO _migrations(filename) VALUES ('096_agent_permissions') ON CONFLICT DO NOTHING;
