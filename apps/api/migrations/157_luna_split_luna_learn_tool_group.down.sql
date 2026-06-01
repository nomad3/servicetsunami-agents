-- 157_luna_split_luna_learn_tool_group.down.sql
--
-- Revert migration 157 — drop `luna_learn` from both Luna rows on
-- Simon's tenant. Same EXISTENCE-based WHERE shape as the up: drop
-- only the literal "luna_learn" entry without disturbing anything
-- else an operator may have added.

BEGIN;

UPDATE agents
SET tool_groups = (
    SELECT jsonb_agg(elem)
    FROM jsonb_array_elements(tool_groups) elem
    WHERE elem <> '"luna_learn"'::jsonb
)
WHERE name IN ('Luna Supervisor', 'Luna')
  AND tenant_id = '752626d9-8b2c-4aa2-87ef-c458d48bd38a'
  AND tool_groups @> '["luna_learn"]'::jsonb;

DELETE FROM _migrations WHERE filename = '157_luna_split_luna_learn_tool_group.sql';

COMMIT;
