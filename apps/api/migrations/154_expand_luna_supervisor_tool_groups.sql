-- 154_expand_luna_supervisor_tool_groups.sql
--
-- Expand Luna Supervisor's tool_groups on Simon's tenant from the
-- legacy set (`competitor`, `knowledge`, `meta`, `sales`,
-- `web_research`, `higgsfield`) to the full operator set so she can
-- manage all of Simon's integrations end-to-end.
--
-- Triggered by 2026-05-24 evening incident: Luna's calendar-write
-- attempt (for next-week W25 schedule) was denied. Diagnosis showed
-- her tool_groups did not include `calendar` or `email` — both groups
-- already exist in app/services/tool_groups.py but weren't assigned to
-- her. Operator (Simon) directed: "She should have full tool control
-- over my integrations."
--
-- New tool_groups (18 total — adds 12, keeps 6):
--   ADDED:   calendar, email, drive, data, reports, bookings,
--            monitor, jira, github, workflows, skills, ecommerce
--   KEPT:    competitor, knowledge, meta, sales, web_research,
--            higgsfield
--   NOT ADDED (intentional):
--     shell — per the security-conscious posture from PR #705,
--             only operator-curated agents intentionally get shell;
--             Luna's role is supervisor/dispatcher, she delegates
--             execution to code-worker which has shell.
--     knowledge_readonly — superseded by `knowledge` which Luna
--             already has (read+write is intentional for the
--             supervisor per the PR #705 split).
--
-- tool_groups_review_required is set FALSE explicitly: this is an
-- operator-curated expansion, not a default backfill, so it should
-- NOT land in the review queue.
--
-- WHERE clause is conservative: only rewrites the row if it matches
-- the legacy shape exactly. Re-running this migration on a row that
-- already has the new shape is a no-op.

BEGIN;

UPDATE agents
SET tool_groups = '["calendar", "email", "drive", "data", "reports", "bookings", "monitor", "jira", "github", "workflows", "skills", "ecommerce", "competitor", "knowledge", "meta", "sales", "web_research", "higgsfield"]'::jsonb,
    tool_groups_review_required = FALSE
WHERE name = 'Luna Supervisor'
  AND tenant_id = '752626d9-8b2c-4aa2-87ef-c458d48bd38a'
  AND tool_groups = '["competitor", "knowledge", "meta", "sales", "web_research", "higgsfield"]'::jsonb;

INSERT INTO _migrations(filename) VALUES ('154_expand_luna_supervisor_tool_groups.sql')
ON CONFLICT DO NOTHING;

COMMIT;
