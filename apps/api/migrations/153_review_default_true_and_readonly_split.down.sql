-- 153_review_default_true_and_readonly_split.down.sql
--
-- Reverses the column-default flip and reverts the 2 retroactively-
-- modified agents. The WHERE clause additionally requires the row to
-- still match the up's targeted shape — this protects against
-- clobbering operator edits made between up and down (e.g. if an
-- operator manually expanded tool_groups beyond the readonly variant,
-- this down won't silently revert that change).

BEGIN;

ALTER TABLE agents
    ALTER COLUMN tool_groups_review_required SET DEFAULT FALSE;

UPDATE agents
SET tool_groups = '["github", "knowledge", "meta"]'::jsonb,
    tool_groups_review_required = FALSE
WHERE tenant_id = '752626d9-8b2c-4aa2-87ef-c458d48bd38a'
  AND role IN ('code_reviewer', 'substrate_sentinel')
  AND lower(name) IN ('code reviewer', 'substrate sentinel')
  AND tool_groups = '["github", "knowledge_readonly", "meta"]'::jsonb;

COMMIT;
