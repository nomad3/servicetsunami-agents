-- 112 — Tighten external_agent_call_logs from PR-E.
--
-- Two fixes called out in code review of PR #201:
--   1. status column had no CHECK — a typo in _record_call_log would
--      silently land in a row that the rollup's enum filter ignores
--      (silent metric loss). Add CHECK matching the producer enum.
--   2. The lookup index put tenant_id first, but the rollup query
--      filters by external_agent_id + started_at only. Drop the old
--      index and replace with one whose leading column matches the
--      hot path; the rollup also gains a tenant_id filter (see
--      apps/api/app/workflows/activities/agent_performance.py) so
--      tenant isolation holds.
--
-- Idempotent.

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'external_agent_call_logs_status_check'
    ) THEN
        ALTER TABLE external_agent_call_logs
            ADD CONSTRAINT external_agent_call_logs_status_check
            CHECK (status IN ('success', 'error', 'non_retryable', 'breaker_open'));
    END IF;
END $$;

DROP INDEX IF EXISTS idx_external_agent_call_logs_lookup;
CREATE INDEX IF NOT EXISTS idx_external_agent_call_logs_rollup
    ON external_agent_call_logs (external_agent_id, started_at DESC);

INSERT INTO _migrations(filename) VALUES ('112_external_agent_call_log_constraints.sql')
ON CONFLICT DO NOTHING;
