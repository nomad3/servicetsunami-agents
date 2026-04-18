-- Migration: 099_agent_performance_rollup
CREATE TABLE IF NOT EXISTS agent_performance_snapshots (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    agent_id UUID NOT NULL REFERENCES agents(id) ON DELETE CASCADE,
    tenant_id UUID NOT NULL REFERENCES tenants(id),
    window_start TIMESTAMPTZ NOT NULL,
    window_hours INTEGER NOT NULL DEFAULT 24,
    invocation_count INTEGER NOT NULL DEFAULT 0,
    success_count INTEGER NOT NULL DEFAULT 0,
    error_count INTEGER NOT NULL DEFAULT 0,
    timeout_count INTEGER NOT NULL DEFAULT 0,
    latency_p50_ms INTEGER,
    latency_p95_ms INTEGER,
    latency_p99_ms INTEGER,
    avg_quality_score FLOAT,
    total_tokens INTEGER NOT NULL DEFAULT 0,
    total_cost_usd FLOAT NOT NULL DEFAULT 0.0,
    cost_per_quality_point FLOAT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_agent_perf_agent_id ON agent_performance_snapshots(agent_id);
CREATE INDEX IF NOT EXISTS idx_agent_perf_tenant_id ON agent_performance_snapshots(tenant_id);
CREATE INDEX IF NOT EXISTS idx_agent_perf_window ON agent_performance_snapshots(window_start DESC);
CREATE UNIQUE INDEX IF NOT EXISTS idx_agent_perf_unique_window ON agent_performance_snapshots(agent_id, window_start, window_hours);

INSERT INTO _migrations(name) VALUES ('099_agent_performance_rollup') ON CONFLICT DO NOTHING;
