-- 050: Dynamic Workflows
-- User-defined workflows with JSON definitions, executed on Temporal.

CREATE TABLE IF NOT EXISTS dynamic_workflows (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    name VARCHAR(255) NOT NULL,
    description TEXT,
    definition JSONB NOT NULL DEFAULT '{"steps": []}'::jsonb,
    version INT NOT NULL DEFAULT 1,
    status VARCHAR(20) NOT NULL DEFAULT 'draft'
        CHECK (status IN ('draft', 'active', 'paused', 'archived')),
    trigger_config JSONB,
    created_by UUID,
    tags TEXT[] DEFAULT '{}',
    -- Marketplace
    tier VARCHAR(20) DEFAULT 'custom'
        CHECK (tier IN ('native', 'community', 'custom')),
    source_template_id UUID,
    public BOOLEAN DEFAULT false,
    installs INT DEFAULT 0,
    rating FLOAT,
    -- Stats
    run_count INT DEFAULT 0,
    last_run_at TIMESTAMP,
    avg_duration_ms INT,
    success_rate FLOAT,
    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_dw_tenant ON dynamic_workflows(tenant_id);
CREATE INDEX IF NOT EXISTS idx_dw_tenant_status ON dynamic_workflows(tenant_id, status);
CREATE INDEX IF NOT EXISTS idx_dw_tier ON dynamic_workflows(tier) WHERE public = true;

CREATE TABLE IF NOT EXISTS workflow_runs (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    workflow_id UUID NOT NULL REFERENCES dynamic_workflows(id) ON DELETE CASCADE,
    workflow_version INT,
    trigger_type VARCHAR(20),
    status VARCHAR(20) NOT NULL DEFAULT 'running'
        CHECK (status IN ('pending', 'running', 'completed', 'failed', 'cancelled', 'waiting_approval')),
    started_at TIMESTAMP NOT NULL DEFAULT NOW(),
    completed_at TIMESTAMP,
    duration_ms INT,
    step_results JSONB DEFAULT '{}'::jsonb,
    current_step VARCHAR(100),
    error TEXT,
    input_data JSONB,
    output_data JSONB,
    total_tokens INT DEFAULT 0,
    total_cost_usd FLOAT DEFAULT 0,
    platform VARCHAR(50),
    temporal_workflow_id VARCHAR(255)
);

CREATE INDEX IF NOT EXISTS idx_wr_workflow ON workflow_runs(workflow_id);
CREATE INDEX IF NOT EXISTS idx_wr_tenant ON workflow_runs(tenant_id);
CREATE INDEX IF NOT EXISTS idx_wr_status ON workflow_runs(status);
CREATE INDEX IF NOT EXISTS idx_wr_started ON workflow_runs(started_at DESC);

CREATE TABLE IF NOT EXISTS workflow_step_logs (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    run_id UUID NOT NULL REFERENCES workflow_runs(id) ON DELETE CASCADE,
    step_id VARCHAR(100) NOT NULL,
    step_type VARCHAR(50) NOT NULL,
    step_name VARCHAR(255),
    status VARCHAR(20) NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending', 'running', 'completed', 'failed', 'skipped')),
    started_at TIMESTAMP,
    completed_at TIMESTAMP,
    duration_ms INT,
    input_data JSONB,
    output_data JSONB,
    error TEXT,
    tokens_used INT DEFAULT 0,
    cost_usd FLOAT DEFAULT 0,
    platform VARCHAR(50),
    retry_count INT DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_wsl_run ON workflow_step_logs(run_id);
CREATE INDEX IF NOT EXISTS idx_wsl_step ON workflow_step_logs(run_id, step_id);
