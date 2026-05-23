-- 149_p0a_tool_permission_gate.down.sql

ALTER TABLE tenant_features
    DROP COLUMN IF EXISTS enforce_strict_tool_scope;

DROP INDEX IF EXISTS idx_agents_tool_groups_review_required;

ALTER TABLE agents
    DROP COLUMN IF EXISTS tool_groups_review_required;

-- NOTE: We do NOT restore tool_groups = NULL on rollback. Reverting
-- the read-only default ['knowledge', 'meta'] backfill would silently
-- re-broaden tool surfaces for previously-NULL agents in the wrong
-- direction. Operators who need to revert must manually clear
-- tool_groups after the schema rollback.
