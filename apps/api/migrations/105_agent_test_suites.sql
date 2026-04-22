-- Migration: 105_agent_test_suites (ALM Pillar 10)
CREATE TABLE IF NOT EXISTS agent_test_cases (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    agent_id UUID NOT NULL REFERENCES agents(id) ON DELETE CASCADE,
    tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    name VARCHAR(200) NOT NULL,
    input TEXT NOT NULL,
    expected_output_contains JSONB NOT NULL DEFAULT '[]'::jsonb,
    expected_output_excludes JSONB NOT NULL DEFAULT '[]'::jsonb,
    min_quality_score NUMERIC(3,2) NOT NULL DEFAULT 0.6,
    max_latency_ms INTEGER NOT NULL DEFAULT 10000,
    tags JSONB NOT NULL DEFAULT '[]'::jsonb,
    enabled BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_agent_test_cases_agent_id ON agent_test_cases(agent_id);
CREATE INDEX IF NOT EXISTS idx_agent_test_cases_tenant_id ON agent_test_cases(tenant_id);

CREATE TABLE IF NOT EXISTS agent_test_runs (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    agent_id UUID NOT NULL REFERENCES agents(id) ON DELETE CASCADE,
    tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    agent_version INTEGER,
    triggered_by_user_id UUID REFERENCES users(id) ON DELETE SET NULL,
    run_type VARCHAR(20) NOT NULL DEFAULT 'manual',  -- manual|promotion_gate|shadow
    status VARCHAR(20) NOT NULL DEFAULT 'running',   -- running|passed|failed|error
    total_cases INTEGER NOT NULL DEFAULT 0,
    passed_count INTEGER NOT NULL DEFAULT 0,
    failed_count INTEGER NOT NULL DEFAULT 0,
    results JSONB NOT NULL DEFAULT '[]'::jsonb,      -- [{case_id, pass, actual, quality, latency_ms, reason}]
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    completed_at TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_agent_test_runs_agent_id ON agent_test_runs(agent_id);
CREATE INDEX IF NOT EXISTS idx_agent_test_runs_tenant_id ON agent_test_runs(tenant_id);
CREATE INDEX IF NOT EXISTS idx_agent_test_runs_created_at ON agent_test_runs(created_at DESC);

INSERT INTO _migrations(filename) VALUES ('105_agent_test_suites') ON CONFLICT DO NOTHING;
