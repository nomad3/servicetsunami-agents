-- 108 — Tool-call audit table.
--
-- Captures every MCP tool invocation (success and failure) so we can
-- measure whether agents are actually grounding responses in tool data
-- vs fabricating. Population happens in the FastMCP server side
-- (apps/mcp-server) via a wrapper around mcp.call_tool — see
-- apps/mcp-server/src/tool_audit.py.
--
-- Correlation back to a chat turn is by (tenant_id, started_at) — the
-- closest assistant chat_message in the +/- N seconds window. This is
-- imprecise but sufficient for the diagnostic we need (count tool calls
-- per turn, flag turns with specific names/numbers but zero tool calls).
-- A precise per-turn correlation would require threading session_id
-- through CLAUDE.md and the MCP protocol — out of scope here.
--
-- Idempotent via IF NOT EXISTS.

CREATE TABLE IF NOT EXISTS tool_calls (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id   UUID NOT NULL,
    tool_name   TEXT NOT NULL,
    arguments   JSONB,
    result_status TEXT NOT NULL,         -- 'ok' | 'error'
    result_summary TEXT,                 -- truncated repr of result for ok
    error       TEXT,                    -- error message for error
    duration_ms INTEGER,
    started_at  TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW(),
    ended_at    TIMESTAMP WITH TIME ZONE
);

CREATE INDEX IF NOT EXISTS ix_tool_calls_tenant_started
    ON tool_calls (tenant_id, started_at DESC);

CREATE INDEX IF NOT EXISTS ix_tool_calls_tool_name
    ON tool_calls (tool_name);

-- Tenant-scoped error feed: "show errors for tenant X in time window Y".
-- Combines the tenant_id + time predicate with the error filter so a
-- single index serves the canonical operational query.
CREATE INDEX IF NOT EXISTS ix_tool_calls_tenant_errors
    ON tool_calls (tenant_id, started_at DESC)
    WHERE result_status = 'error';

-- No FK to tenants(id): the FastMCP server might log calls for tenant ids
-- that race with tenant deletion; we want audit rows to survive. Soft-link
-- via tenant_id only.

COMMENT ON TABLE tool_calls IS
    'Per-invocation audit of MCP tool calls. Populated by apps/mcp-server. Used to measure tool-grounding rate vs fabrication.';
