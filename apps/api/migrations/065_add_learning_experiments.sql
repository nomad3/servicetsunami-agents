-- Gap 04 Phase 1: self-improvement pipeline

CREATE TABLE IF NOT EXISTS policy_candidates (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    policy_type VARCHAR(50) NOT NULL,
    decision_point VARCHAR(50) NOT NULL,
    description TEXT NOT NULL,
    current_policy JSONB NOT NULL DEFAULT '{}'::jsonb,
    proposed_policy JSONB NOT NULL DEFAULT '{}'::jsonb,
    rationale TEXT NOT NULL,
    source_experience_count INTEGER NOT NULL DEFAULT 0,
    source_query JSONB NOT NULL DEFAULT '{}'::jsonb,
    baseline_reward DOUBLE PRECISION,
    expected_improvement DOUBLE PRECISION,
    status VARCHAR(30) NOT NULL DEFAULT 'proposed',
    promoted_at TIMESTAMP WITHOUT TIME ZONE,
    rejected_at TIMESTAMP WITHOUT TIME ZONE,
    rejection_reason TEXT,
    created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMP WITHOUT TIME ZONE NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_policy_candidates_tenant_status
ON policy_candidates(tenant_id, status);


CREATE TABLE IF NOT EXISTS learning_experiments (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    candidate_id UUID NOT NULL REFERENCES policy_candidates(id) ON DELETE CASCADE,
    experiment_type VARCHAR(30) NOT NULL DEFAULT 'shadow',
    rollout_pct DOUBLE PRECISION NOT NULL DEFAULT 0.0,
    min_sample_size INTEGER NOT NULL DEFAULT 20,
    max_duration_hours INTEGER NOT NULL DEFAULT 168,
    status VARCHAR(30) NOT NULL DEFAULT 'pending',
    started_at TIMESTAMP WITHOUT TIME ZONE,
    completed_at TIMESTAMP WITHOUT TIME ZONE,
    control_sample_size INTEGER NOT NULL DEFAULT 0,
    treatment_sample_size INTEGER NOT NULL DEFAULT 0,
    control_avg_reward DOUBLE PRECISION,
    treatment_avg_reward DOUBLE PRECISION,
    improvement_pct DOUBLE PRECISION,
    is_significant VARCHAR(10),
    conclusion TEXT,
    created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMP WITHOUT TIME ZONE NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_learning_experiments_tenant_status
ON learning_experiments(tenant_id, status);

CREATE INDEX IF NOT EXISTS idx_learning_experiments_candidate
ON learning_experiments(candidate_id);
