-- Migration: 095_agent_policies
CREATE TABLE IF NOT EXISTS agent_policies (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    agent_id UUID REFERENCES agents(id) ON DELETE CASCADE,
    tenant_id UUID NOT NULL REFERENCES tenants(id),
    policy_type VARCHAR(30) NOT NULL,
    config JSONB NOT NULL DEFAULT '{}',
    enabled BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_agent_policies_agent_id ON agent_policies(agent_id);
CREATE INDEX IF NOT EXISTS idx_agent_policies_tenant_id ON agent_policies(tenant_id);
CREATE INDEX IF NOT EXISTS idx_agent_policies_type ON agent_policies(policy_type);

INSERT INTO _migrations(name) VALUES ('095_agent_policies') ON CONFLICT DO NOTHING;
