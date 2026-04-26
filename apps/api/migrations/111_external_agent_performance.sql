-- 111 — Cost / latency parity for external agents.
--
-- Two changes:
--   1. Make agent_performance_snapshots.agent_id nullable + add
--      external_agent_id nullable FK so the existing rollup can target
--      either kind of agent. CHECK constraint enforces exactly-one-set
--      so we don't end up with rows that point at both or neither.
--   2. New external_agent_call_logs — per-call metric rows analogous to
--      AgentAuditLog, but written by external_agent_call. The rollup
--      activity aggregates over these to populate the new
--      external_agent_id-keyed snapshots.
--
-- Idempotent.

-- 1. Nullable + new FK on the snapshot table -------------------------------
ALTER TABLE agent_performance_snapshots
    ALTER COLUMN agent_id DROP NOT NULL;

ALTER TABLE agent_performance_snapshots
    ADD COLUMN IF NOT EXISTS external_agent_id UUID
        REFERENCES external_agents(id) ON DELETE CASCADE;

-- Enforce that a snapshot row points at exactly one of the two — never
-- both, never neither.
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'agent_performance_snapshots_target_exclusive'
    ) THEN
        ALTER TABLE agent_performance_snapshots
            ADD CONSTRAINT agent_performance_snapshots_target_exclusive
            CHECK (
                (agent_id IS NOT NULL AND external_agent_id IS NULL)
                OR (agent_id IS NULL AND external_agent_id IS NOT NULL)
            );
    END IF;
END $$;

CREATE INDEX IF NOT EXISTS idx_agent_perf_snap_external_agent
    ON agent_performance_snapshots (external_agent_id, window_start DESC);

-- 2. External-agent per-call log -------------------------------------------
CREATE TABLE IF NOT EXISTS external_agent_call_logs (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id           UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    external_agent_id   UUID NOT NULL REFERENCES external_agents(id) ON DELETE CASCADE,
    started_at          TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW(),
    latency_ms          INTEGER,
    status              VARCHAR(32) NOT NULL,
    total_tokens        INTEGER NOT NULL DEFAULT 0,
    cost_usd            NUMERIC(12, 6) NOT NULL DEFAULT 0,
    error_message       TEXT
);

CREATE INDEX IF NOT EXISTS idx_external_agent_call_logs_lookup
    ON external_agent_call_logs (tenant_id, external_agent_id, started_at DESC);

INSERT INTO _migrations(filename) VALUES ('111_external_agent_performance.sql')
ON CONFLICT DO NOTHING;
