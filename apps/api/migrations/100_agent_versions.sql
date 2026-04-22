-- Migration: 100_agent_versions
CREATE TABLE IF NOT EXISTS agent_versions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    agent_id UUID NOT NULL REFERENCES agents(id) ON DELETE CASCADE,
    tenant_id UUID NOT NULL REFERENCES tenants(id),
    version INTEGER NOT NULL,
    config_snapshot JSONB NOT NULL,
    status VARCHAR(20) NOT NULL DEFAULT 'draft',
    notes TEXT,
    promoted_by UUID REFERENCES users(id) ON DELETE SET NULL,
    promoted_at TIMESTAMPTZ,
    performance_snapshot JSONB,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE(agent_id, version)
);

CREATE INDEX IF NOT EXISTS idx_agent_versions_agent_id ON agent_versions(agent_id);
CREATE INDEX IF NOT EXISTS idx_agent_versions_tenant_id ON agent_versions(tenant_id);

INSERT INTO _migrations(filename) VALUES ('100_agent_versions') ON CONFLICT DO NOTHING;
