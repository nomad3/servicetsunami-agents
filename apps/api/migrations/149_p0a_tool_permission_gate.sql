-- 149_p0a_tool_permission_gate.sql
--
-- P0a substrate changes for the tool-permission gate fix.
--
-- Design: docs/plans/2026-05-23-p0a-tool-permission-gate-fix.md
-- Motivating evidence: docs/report/2026-05-23-prompt-injection-tool-
--   permission-test.md (the round-3 breach: Luna ran execute_shell
--   outside her tool_groups).
-- Luna sign-off: dialogue session 05979efd-a06a-4956-9df9-3fd84ec3c10d.
--
-- Three changes:
--   1. Add `agents.tool_groups_review_required` column (NULL-backfill flag).
--   2. Backfill NULL `agents.tool_groups` with read-only default
--      ['knowledge', 'meta'] + mark them review_required=TRUE.
--   3. Add `tenant_features.enforce_strict_tool_scope` flag (for the
--      24h shadow → per-tenant ramp → universal cutover).
--
-- The Phase 4 `use_resilient_executor` flag is NOT dropped in this
-- migration — it stays as decorative/no-op for one release while
-- code-paths that previously read it are removed. Drop comes in a
-- follow-up migration to keep the schema change small and rollback
-- atomic.

-- ── 1. agents.tool_groups_review_required ─────────────────────────────

ALTER TABLE agents
    ADD COLUMN tool_groups_review_required BOOLEAN NOT NULL DEFAULT FALSE;

COMMENT ON COLUMN agents.tool_groups_review_required IS
    'P0a (2026-05-23): TRUE when tool_groups was auto-backfilled '
    'because it was NULL at migration time. Operator review queue '
    'surfaces these agents; cleared on operator action or after '
    '1-week auto-clear (requires BOTH zero shadow-denial activity '
    'AND observed activity — inactivity is not compatibility proof).';

CREATE INDEX idx_agents_tool_groups_review_required
    ON agents (tool_groups_review_required)
    WHERE tool_groups_review_required = TRUE;

-- ── 2. Backfill NULL tool_groups with read-only default ───────────────
--
-- Per Luna review: combined approach. Auto-backfill with read-only
-- default ['knowledge', 'meta'] (safe minimal surface; no shell, data,
-- integrations) AND mark review_required so operators can adjust
-- after 24h shadow observation. This preserves running workflows
-- while making the boundary visible.

UPDATE agents
SET tool_groups = '["knowledge", "meta"]'::jsonb,
    tool_groups_review_required = TRUE
WHERE tool_groups IS NULL;

-- ── 3. tenant_features.enforce_strict_tool_scope ──────────────────────
--
-- Per-tenant flag for the rollout ramp:
--   - DEFAULT FALSE during step 1-2 of the rollout (Fix B lands behind
--     this flag, observable but not enforced).
--   - FLIPPED TRUE for Simon's tenant first (step 3) for 24h watch.
--   - FLIPPED TRUE for all other tenants (step 4) once shadow is clean.
--   - Removed in a follow-up migration after 1 week stable (step 5)
--     — fail-closed becomes the only behavior.

ALTER TABLE tenant_features
    ADD COLUMN enforce_strict_tool_scope BOOLEAN NOT NULL DEFAULT FALSE;

COMMENT ON COLUMN tenant_features.enforce_strict_tool_scope IS
    'P0a (2026-05-23): per-tenant gate for the universal fail-closed '
    'default in apps/mcp-server/src/tool_audit.py. FALSE = shadow-log '
    'denials only (no enforcement). TRUE = enforce. Per Luna review '
    'rollout sequence: flip TRUE for Simon first, watch 24h, then '
    'flip all other tenants. Removed in follow-up migration after '
    '1 week stable.';
