-- 148_tool_audit_drops.sql
--
-- Tool-audit drops breadcrumb table — P0c §6.
--
-- Design: docs/plans/2026-05-23-p0c-audit-log-fail-loud.md §6.
-- Hard-test report: docs/report/2026-05-23-prompt-injection-tool-permission-test.md §3.4.
-- Luna sign-off: dialogue session 05979efd-a06a-4956-9df9-3fd84ec3c10d.
--
-- Purpose: when the main `tool_calls` write cannot proceed (tenant_id
-- unresolvable, SQL failure, executor scheduling failure), this table
-- carries a minimal breadcrumb so operators can correlate against
-- session_events + chat_messages by timestamp. Without it, breaches
-- leave zero DB forensic record — which is the exact failure mode
-- round 3 of the 2026-05-23 hard-tests exposed.
--
-- NO tenant_id column by design: the whole point is we couldn't
-- resolve one. Top-level argument keys only (no values) to avoid
-- leaking PII through the safety net.
--
-- Cardinality expectation: very low in healthy operation (zero is
-- the goal). The `> 0 in 5min` Prometheus alert fires on any drop.

CREATE TABLE tool_audit_drops (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tool_name       TEXT NOT NULL,
    -- 'no_tenant_id' | 'sql_insert_failed' | 'scheduling_failed'
    drop_reason     TEXT NOT NULL,
    -- 'agent_token' | 'tenant_header' | 'internal_key' | 'anonymous' | null
    tier            TEXT,
    -- Top-level argument keys only — NEVER values. Cap at 20 keys.
    args_keys       TEXT[],
    -- Redacted exception summary (truncated to 600 chars to match the
    -- _truncate cap used by tool_audit._log_call for errors).
    error_message   TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Recent-drops dashboard scan + correlation queries.
CREATE INDEX idx_tool_audit_drops_created_at
    ON tool_audit_drops (created_at DESC);

-- Per-reason aggregation for the audit-health dashboard.
CREATE INDEX idx_tool_audit_drops_reason_created
    ON tool_audit_drops (drop_reason, created_at DESC);
