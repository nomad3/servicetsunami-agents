-- Gap 03 Phase 1: plan runtime with first-class steps, assumptions, and events

CREATE TABLE IF NOT EXISTS plans (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    goal_id UUID REFERENCES goal_records(id),
    owner_agent_slug VARCHAR(100) NOT NULL,
    title VARCHAR(500) NOT NULL,
    description TEXT,
    plan_version INTEGER NOT NULL DEFAULT 1,
    status VARCHAR(30) NOT NULL DEFAULT 'draft',
    current_step_index INTEGER NOT NULL DEFAULT 0,
    replan_count INTEGER NOT NULL DEFAULT 0,
    budget_max_actions INTEGER,
    budget_max_cost_usd DOUBLE PRECISION,
    budget_max_runtime_hours DOUBLE PRECISION,
    budget_actions_used INTEGER NOT NULL DEFAULT 0,
    budget_cost_used DOUBLE PRECISION NOT NULL DEFAULT 0.0,
    created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMP WITHOUT TIME ZONE NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_plans_tenant_status
ON plans(tenant_id, status);

CREATE INDEX IF NOT EXISTS idx_plans_tenant_goal
ON plans(tenant_id, goal_id)
WHERE goal_id IS NOT NULL;


CREATE TABLE IF NOT EXISTS plan_steps (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    plan_id UUID NOT NULL REFERENCES plans(id) ON DELETE CASCADE,
    step_index INTEGER NOT NULL,
    title VARCHAR(500) NOT NULL,
    description TEXT,
    owner_agent_slug VARCHAR(100),
    step_type VARCHAR(50) NOT NULL DEFAULT 'action',
    status VARCHAR(30) NOT NULL DEFAULT 'pending',
    expected_inputs JSONB NOT NULL DEFAULT '[]'::jsonb,
    expected_outputs JSONB NOT NULL DEFAULT '[]'::jsonb,
    required_tools JSONB NOT NULL DEFAULT '[]'::jsonb,
    side_effect_level VARCHAR(30) NOT NULL DEFAULT 'none',
    retry_policy JSONB NOT NULL DEFAULT '{}'::jsonb,
    fallback_step_index INTEGER,
    output JSONB,
    error TEXT,
    started_at TIMESTAMP WITHOUT TIME ZONE,
    completed_at TIMESTAMP WITHOUT TIME ZONE,
    created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMP WITHOUT TIME ZONE NOT NULL DEFAULT NOW()
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_plan_steps_plan_index
ON plan_steps(plan_id, step_index);


CREATE TABLE IF NOT EXISTS plan_assumptions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    plan_id UUID NOT NULL REFERENCES plans(id) ON DELETE CASCADE,
    description TEXT NOT NULL,
    status VARCHAR(30) NOT NULL DEFAULT 'unverified',
    assertion_id UUID REFERENCES world_state_assertions(id),
    invalidated_reason TEXT,
    created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMP WITHOUT TIME ZONE NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_plan_assumptions_plan
ON plan_assumptions(plan_id);


CREATE TABLE IF NOT EXISTS plan_events (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    plan_id UUID NOT NULL REFERENCES plans(id) ON DELETE CASCADE,
    step_id UUID REFERENCES plan_steps(id),
    event_type VARCHAR(50) NOT NULL,
    previous_status VARCHAR(30),
    new_status VARCHAR(30),
    reason TEXT,
    metadata_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    agent_slug VARCHAR(100),
    created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_plan_events_plan
ON plan_events(plan_id, created_at DESC);
