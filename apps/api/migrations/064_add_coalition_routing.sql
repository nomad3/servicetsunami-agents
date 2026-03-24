-- Gap 06 Phase 3: coalition templates and outcome tracking

CREATE TABLE IF NOT EXISTS coalition_templates (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    name VARCHAR(200) NOT NULL,
    description TEXT,
    pattern VARCHAR(50) NOT NULL,
    role_agent_map JSONB NOT NULL DEFAULT '{}'::jsonb,
    task_types JSONB NOT NULL DEFAULT '[]'::jsonb,
    total_uses INTEGER NOT NULL DEFAULT 0,
    success_count INTEGER NOT NULL DEFAULT 0,
    avg_quality_score DOUBLE PRECISION NOT NULL DEFAULT 0.0,
    avg_rounds_to_consensus DOUBLE PRECISION NOT NULL DEFAULT 0.0,
    avg_cost_usd DOUBLE PRECISION NOT NULL DEFAULT 0.0,
    status VARCHAR(30) NOT NULL DEFAULT 'active',
    created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMP WITHOUT TIME ZONE NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_coalition_templates_tenant
ON coalition_templates(tenant_id, status);


CREATE TABLE IF NOT EXISTS coalition_outcomes (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    template_id UUID REFERENCES coalition_templates(id),
    collaboration_id UUID REFERENCES collaboration_sessions(id),
    task_type VARCHAR(50) NOT NULL,
    pattern VARCHAR(50) NOT NULL,
    role_agent_map JSONB NOT NULL DEFAULT '{}'::jsonb,
    success VARCHAR(10) NOT NULL,
    quality_score DOUBLE PRECISION,
    rounds_completed INTEGER NOT NULL DEFAULT 1,
    consensus_reached VARCHAR(10),
    cost_usd DOUBLE PRECISION NOT NULL DEFAULT 0.0,
    duration_seconds INTEGER,
    created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_coalition_outcomes_tenant_type
ON coalition_outcomes(tenant_id, task_type);

CREATE INDEX IF NOT EXISTS idx_coalition_outcomes_template
ON coalition_outcomes(template_id)
WHERE template_id IS NOT NULL;
