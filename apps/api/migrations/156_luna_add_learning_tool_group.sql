-- 156_luna_add_learning_tool_group.sql
--
-- Grant Luna Supervisor on Simon's tenant the new `learning` tool_group
-- so she can dispatch the Luna Learn from Media workflow (spec
-- 2026-05-25-luna-learn-from-media-design.md). The `learning` group
-- exposes the 7 MCP primitives registered in
-- apps/mcp-server/src/mcp_tools/learning.py:
--   extract_media, transcribe_url, synthesize_skill_draft,
--   dispatch_skill_review, run_synthetic_test, install_skill,
--   diffuse_learning
--
-- Luna's tool_groups live in BOTH the bundled agent skill.md frontmatter
-- (apps/api/app/agents/_bundled/luna/skill.md) AND the DB-side `agents`
-- row keyed by (name, tenant_id). Migration 154 expanded the DB row from
-- 6 → 18 groups; this migration appends `learning` as item 19. The
-- bundled skill.md frontmatter is updated in the same commit so the two
-- sources stay in lockstep (preventing drift caught by the matching test
-- in apps/api/tests/test_luna_learning_tool_group.py).
--
-- tool_groups_review_required is NOT touched — it was set FALSE by
-- migration 154 and stays FALSE (this is another operator-curated
-- expansion, not a default backfill that should land in the review
-- queue).
--
-- WHERE clause is conservative (mirrors migration 154's posture): only
-- rewrites the row if it matches the 18-group shape exactly. Re-running
-- on a row that already has `learning` is a no-op.

BEGIN;

UPDATE agents
SET tool_groups = '["calendar", "email", "drive", "data", "reports", "bookings", "monitor", "jira", "github", "workflows", "skills", "ecommerce", "competitor", "knowledge", "meta", "sales", "web_research", "higgsfield", "learning"]'::jsonb
WHERE name = 'Luna Supervisor'
  AND tenant_id = '752626d9-8b2c-4aa2-87ef-c458d48bd38a'
  AND tool_groups = '["calendar", "email", "drive", "data", "reports", "bookings", "monitor", "jira", "github", "workflows", "skills", "ecommerce", "competitor", "knowledge", "meta", "sales", "web_research", "higgsfield"]'::jsonb;

INSERT INTO _migrations(filename) VALUES ('156_luna_add_learning_tool_group.sql')
ON CONFLICT DO NOTHING;

COMMIT;
