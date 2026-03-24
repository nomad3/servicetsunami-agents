-- Gap 02 Phase 1: goal and commitment persistence for agent self-model

CREATE TABLE IF NOT EXISTS goal_records (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    owner_agent_slug VARCHAR(100) NOT NULL,
    created_by UUID REFERENCES users(id),
    title VARCHAR(500) NOT NULL,
    description TEXT,
    objective_type VARCHAR(50) NOT NULL DEFAULT 'operational',
    priority VARCHAR(20) NOT NULL DEFAULT 'normal',
    state VARCHAR(30) NOT NULL DEFAULT 'proposed',
    success_criteria JSONB NOT NULL DEFAULT '[]'::jsonb,
    deadline TIMESTAMP WITHOUT TIME ZONE,
    related_entity_ids JSONB NOT NULL DEFAULT '[]'::jsonb,
    parent_goal_id UUID REFERENCES goal_records(id),
    progress_summary TEXT,
    progress_pct INTEGER NOT NULL DEFAULT 0,
    last_reviewed_at TIMESTAMP WITHOUT TIME ZONE,
    completed_at TIMESTAMP WITHOUT TIME ZONE,
    abandoned_at TIMESTAMP WITHOUT TIME ZONE,
    abandoned_reason TEXT,
    created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMP WITHOUT TIME ZONE NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_goal_records_tenant_agent
ON goal_records(tenant_id, owner_agent_slug);

CREATE INDEX IF NOT EXISTS idx_goal_records_tenant_state
ON goal_records(tenant_id, state);

CREATE INDEX IF NOT EXISTS idx_goal_records_parent
ON goal_records(parent_goal_id)
WHERE parent_goal_id IS NOT NULL;


CREATE TABLE IF NOT EXISTS commitment_records (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    owner_agent_slug VARCHAR(100) NOT NULL,
    created_by UUID REFERENCES users(id),
    title VARCHAR(500) NOT NULL,
    description TEXT,
    commitment_type VARCHAR(50) NOT NULL DEFAULT 'action',
    state VARCHAR(30) NOT NULL DEFAULT 'open',
    priority VARCHAR(20) NOT NULL DEFAULT 'normal',
    source_type VARCHAR(50) NOT NULL DEFAULT 'tool_call',
    source_ref JSONB NOT NULL DEFAULT '{}'::jsonb,
    due_at TIMESTAMP WITHOUT TIME ZONE,
    fulfilled_at TIMESTAMP WITHOUT TIME ZONE,
    broken_at TIMESTAMP WITHOUT TIME ZONE,
    broken_reason TEXT,
    goal_id UUID REFERENCES goal_records(id),
    related_entity_ids JSONB NOT NULL DEFAULT '[]'::jsonb,
    created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMP WITHOUT TIME ZONE NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_commitment_records_tenant_agent
ON commitment_records(tenant_id, owner_agent_slug);

CREATE INDEX IF NOT EXISTS idx_commitment_records_tenant_state
ON commitment_records(tenant_id, state);

CREATE INDEX IF NOT EXISTS idx_commitment_records_due
ON commitment_records(tenant_id, due_at)
WHERE due_at IS NOT NULL AND state IN ('open', 'in_progress');

CREATE INDEX IF NOT EXISTS idx_commitment_records_goal
ON commitment_records(goal_id)
WHERE goal_id IS NOT NULL;
