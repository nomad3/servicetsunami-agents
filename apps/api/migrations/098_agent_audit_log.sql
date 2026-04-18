-- Migration: 098_agent_audit_log
CREATE TABLE IF NOT EXISTS agent_audit_logs (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID NOT NULL REFERENCES tenants(id),
    agent_id UUID REFERENCES agents(id) ON DELETE SET NULL,
    external_agent_id UUID,
    invoked_by_user_id UUID REFERENCES users(id) ON DELETE SET NULL,
    invoked_by_agent_id UUID REFERENCES agents(id) ON DELETE SET NULL,
    session_id UUID,
    invocation_type VARCHAR(20) NOT NULL,
    input_summary TEXT,
    output_summary TEXT,
    input_tokens INTEGER,
    output_tokens INTEGER,
    cost_usd FLOAT,
    latency_ms INTEGER,
    status VARCHAR(30) NOT NULL,
    error_message TEXT,
    policy_violations JSONB,
    quality_score FLOAT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_agent_audit_logs_tenant_id ON agent_audit_logs(tenant_id);
CREATE INDEX IF NOT EXISTS idx_agent_audit_logs_agent_id ON agent_audit_logs(agent_id);
CREATE INDEX IF NOT EXISTS idx_agent_audit_logs_created_at ON agent_audit_logs(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_agent_audit_logs_user ON agent_audit_logs(invoked_by_user_id);

INSERT INTO _migrations(name) VALUES ('098_agent_audit_log') ON CONFLICT DO NOTHING;
