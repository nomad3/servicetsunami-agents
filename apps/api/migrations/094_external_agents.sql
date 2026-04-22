-- Migration: 094_external_agents
CREATE TABLE IF NOT EXISTS external_agents (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    name VARCHAR NOT NULL,
    description TEXT,
    avatar_url VARCHAR,
    protocol VARCHAR NOT NULL,
    endpoint_url VARCHAR NOT NULL,
    auth_type VARCHAR NOT NULL DEFAULT 'bearer',
    credential_id UUID REFERENCES integration_credentials(id) ON DELETE SET NULL,
    capabilities JSONB NOT NULL DEFAULT '[]',
    health_check_path VARCHAR NOT NULL DEFAULT '/health',
    status VARCHAR NOT NULL DEFAULT 'offline',
    last_seen_at TIMESTAMPTZ,
    task_count INTEGER NOT NULL DEFAULT 0,
    success_count INTEGER NOT NULL DEFAULT 0,
    error_count INTEGER NOT NULL DEFAULT 0,
    avg_latency_ms INTEGER,
    metadata JSONB NOT NULL DEFAULT '{}',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_external_agents_tenant_id ON external_agents(tenant_id);
CREATE INDEX IF NOT EXISTS idx_external_agents_status ON external_agents(status);

INSERT INTO _migrations(filename) VALUES ('094_external_agents') ON CONFLICT DO NOTHING;
