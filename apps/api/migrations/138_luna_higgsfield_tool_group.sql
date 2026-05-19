-- Migration 137 — add `higgsfield` tool_group to every Luna agent.
--
-- Why: PR #550 ships per-tenant Higgsfield MCP connector registration via
-- `apps/api/app/services/higgsfield_mcp.register_for_tenant`. The connector
-- is auto-injected into the chat CLI's mcp_config by
-- `cli_session_manager.generate_mcp_config`, BUT the CLI only invokes tools
-- whose names match the `--allowedTools` allow-list that we compute from the
-- agent's `tool_groups` via `tool_groups.format_allowed_tools`. Luna's
-- current tool_groups (set by migration 125) are
-- ["web_research", "knowledge", "sales", "competitor"] — none of which
-- emits `mcp__higgsfield__*`, so chat-driven image / video generation calls
-- to the Higgsfield connector get silently filtered out.
--
-- The accompanying `tool_groups.format_allowed_tools` change collapses any
-- `higgsfield_*` tool name down to the connector wildcard
-- `mcp__higgsfield__*`. This migration makes sure Luna actually carries the
-- `higgsfield` group so that wildcard is emitted.
--
-- The `higgsfield` group is defined in
-- `apps/api/app/services/tool_groups.py` and is sourced from
-- `higgsfield_mcp.HIGGSFIELD_TOOL_NAMES` — a tool rename only has to land in
-- one place.
--
-- Idempotent:
--   * NULL tool_groups → set to ["higgsfield"]
--   * already contains "higgsfield" → no-op
--   * else → append "higgsfield"

UPDATE agents
SET tool_groups = (
    CASE
        WHEN tool_groups IS NULL THEN '["higgsfield"]'::jsonb
        WHEN tool_groups @> '["higgsfield"]'::jsonb THEN tool_groups
        ELSE tool_groups || '["higgsfield"]'::jsonb
    END
)
WHERE name = 'Luna' OR name ILIKE 'luna%';

-- Self-record so re-applying this migration on a fresh DB is a clean no-op.
INSERT INTO _migrations(filename) VALUES ('137_luna_higgsfield_tool_group.sql')
ON CONFLICT DO NOTHING;
