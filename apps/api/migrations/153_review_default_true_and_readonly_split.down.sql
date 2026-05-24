-- 153_review_default_true_and_readonly_split.down.sql

-- Revert the column default to FALSE
ALTER TABLE agents
    ALTER COLUMN tool_groups_review_required SET DEFAULT FALSE;

-- Restore the two agents to their pre-153 state.
UPDATE agents
SET tool_groups = '["github", "knowledge", "meta"]'::jsonb,
    tool_groups_review_required = FALSE
WHERE tenant_id = '752626d9-8b2c-4aa2-87ef-c458d48bd38a'
  AND role IN ('code_reviewer', 'substrate_sentinel')
  AND lower(name) IN ('code reviewer', 'substrate sentinel');
