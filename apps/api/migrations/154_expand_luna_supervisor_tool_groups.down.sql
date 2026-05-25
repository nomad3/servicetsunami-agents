-- 154_expand_luna_supervisor_tool_groups.down.sql
--
-- Revert Luna Supervisor on Simon's tenant to the legacy 6-group set.
-- WHERE-clause guards against clobbering further operator edits made
-- after the up migration: only reverts if tool_groups still matches
-- exactly what the up migration wrote.

BEGIN;

UPDATE agents
SET tool_groups = '["competitor", "knowledge", "meta", "sales", "web_research", "higgsfield"]'::jsonb
WHERE name = 'Luna Supervisor'
  AND tenant_id = '752626d9-8b2c-4aa2-87ef-c458d48bd38a'
  AND tool_groups = '["calendar", "email", "drive", "data", "reports", "bookings", "monitor", "jira", "github", "workflows", "skills", "ecommerce", "competitor", "knowledge", "meta", "sales", "web_research", "higgsfield"]'::jsonb;

-- Remove the up's _migrations row so the up can be re-applied later
-- (per apps/api/migrations/README.md §"Down migrations").
DELETE FROM _migrations WHERE filename = '154_expand_luna_supervisor_tool_groups.sql';

COMMIT;
