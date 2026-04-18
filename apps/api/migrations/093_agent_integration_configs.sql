-- Migration: 093_agent_integration_configs
CREATE TABLE IF NOT EXISTS agent_integration_configs (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    agent_id UUID NOT NULL REFERENCES agents(id) ON DELETE CASCADE,
    integration_config_id UUID NOT NULL REFERENCES integration_configs(id) ON DELETE CASCADE,
    tenant_id UUID NOT NULL REFERENCES tenants(id),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE(agent_id, integration_config_id)
);
CREATE INDEX IF NOT EXISTS idx_agent_integration_configs_agent_id ON agent_integration_configs(agent_id);
CREATE INDEX IF NOT EXISTS idx_agent_integration_configs_tenant_id ON agent_integration_configs(tenant_id);

INSERT INTO _migrations(name) VALUES ('093_agent_integration_configs') ON CONFLICT DO NOTHING;
