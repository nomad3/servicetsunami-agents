-- 045_add_rl_framework.sql
-- Reinforcement Learning Framework tables

-- Enable pgvector if not already
CREATE EXTENSION IF NOT EXISTS vector;

-- RL Experiences table
CREATE TABLE IF NOT EXISTS rl_experiences (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    trajectory_id UUID NOT NULL,
    step_index INTEGER NOT NULL DEFAULT 0,
    decision_point VARCHAR(50) NOT NULL,
    state JSONB NOT NULL DEFAULT '{}',
    state_embedding vector(768),
    action JSONB NOT NULL DEFAULT '{}',
    alternatives JSONB DEFAULT '[]',
    reward FLOAT,
    reward_components JSONB,
    reward_source VARCHAR(50),
    explanation JSONB,
    policy_version VARCHAR(50),
    exploration BOOLEAN NOT NULL DEFAULT FALSE,
    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
    rewarded_at TIMESTAMP,
    archived_at TIMESTAMP
);

-- Indexes for rl_experiences
CREATE INDEX idx_rl_exp_tenant_dp_created
    ON rl_experiences (tenant_id, decision_point, created_at DESC);
CREATE INDEX idx_rl_exp_trajectory
    ON rl_experiences (trajectory_id);
CREATE INDEX idx_rl_exp_tenant_archived
    ON rl_experiences (tenant_id, archived_at)
    WHERE archived_at IS NULL;

-- HNSW index for state_embedding similarity search
-- Using HNSW instead of IVFFlat because IVFFlat requires data to build clusters.
-- Since rl_experiences starts empty, HNSW works correctly from zero rows.
CREATE INDEX idx_rl_exp_state_embedding
    ON rl_experiences
    USING hnsw (state_embedding vector_cosine_ops)
    WITH (m = 16, ef_construction = 64);

-- RL Policy States table
CREATE TABLE IF NOT EXISTS rl_policy_states (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID REFERENCES tenants(id) ON DELETE CASCADE,
    decision_point VARCHAR(50) NOT NULL,
    weights JSONB NOT NULL DEFAULT '{}',
    version VARCHAR(50) NOT NULL DEFAULT 'v1',
    experience_count INTEGER NOT NULL DEFAULT 0,
    last_updated_at TIMESTAMP NOT NULL DEFAULT NOW(),
    exploration_rate FLOAT NOT NULL DEFAULT 0.1
);

-- Unique constraint: one policy per tenant per decision point
CREATE UNIQUE INDEX idx_rl_policy_tenant_dp
    ON rl_policy_states (tenant_id, decision_point)
    WHERE tenant_id IS NOT NULL;

-- Global baseline: one per decision point where tenant_id IS NULL
CREATE UNIQUE INDEX idx_rl_policy_global_dp
    ON rl_policy_states (decision_point)
    WHERE tenant_id IS NULL;

-- Add rl_settings JSON column to tenant_features
ALTER TABLE tenant_features
    ADD COLUMN IF NOT EXISTS rl_enabled BOOLEAN NOT NULL DEFAULT FALSE,
    ADD COLUMN IF NOT EXISTS rl_settings JSONB NOT NULL DEFAULT '{
        "exploration_rate": 0.1,
        "opt_in_global_learning": true,
        "use_global_baseline": true,
        "min_tenant_experiences": 50,
        "blend_alpha_growth": 0.01,
        "reward_weights": {"implicit": 0.3, "explicit": 0.5, "admin": 0.2},
        "review_schedule": "weekly",
        "per_decision_overrides": {}
    }';

-- Add updated_at to notifications (prerequisite for implicit reward signals)
ALTER TABLE notifications
    ADD COLUMN IF NOT EXISTS updated_at TIMESTAMP DEFAULT NOW();
