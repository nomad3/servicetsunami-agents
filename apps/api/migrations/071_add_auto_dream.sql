-- Migration 071: Auto-dream insight table
-- Stores patterns discovered during REM-style RL experience consolidation cycles.
-- Each row = one action-pattern for one decision_point in one dream cycle.

CREATE TABLE IF NOT EXISTS auto_dream_insights (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id       UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    dream_cycle_id  UUID NOT NULL,
    decision_point  VARCHAR(50) NOT NULL,
    insight_type    VARCHAR(50) NOT NULL,   -- 'pattern', 'anomaly', 'opportunity'
    action_key      VARCHAR(200),
    context_summary TEXT,
    avg_reward      FLOAT,
    experience_count INTEGER NOT NULL DEFAULT 0,
    confidence      FLOAT NOT NULL DEFAULT 0.5,
    properties      JSONB DEFAULT '{}',
    applied_to_policy BOOLEAN NOT NULL DEFAULT FALSE,
    synthetic_memory_id UUID,
    generated_at    TIMESTAMP NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_auto_dream_tenant        ON auto_dream_insights (tenant_id);
CREATE INDEX IF NOT EXISTS idx_auto_dream_cycle         ON auto_dream_insights (dream_cycle_id);
CREATE INDEX IF NOT EXISTS idx_auto_dream_decision_pt   ON auto_dream_insights (decision_point);
CREATE INDEX IF NOT EXISTS idx_auto_dream_generated_at  ON auto_dream_insights (generated_at DESC);
