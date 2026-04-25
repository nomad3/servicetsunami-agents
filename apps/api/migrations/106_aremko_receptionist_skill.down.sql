-- Rollback for 106_aremko_receptionist_skill.sql
--
-- To revert binding aremko's Luna agent to the receptionist skill:
--   1. Apply this file via the same migration runner.
--   2. Then DELETE FROM _migrations WHERE filename='106_aremko_receptionist_skill.sql'
--      so the up-migration can be re-applied later if needed.
--
-- Reversible: re-running 106_aremko_receptionist_skill.sql restores the binding.

UPDATE agents
SET config = COALESCE(config::jsonb, '{}'::jsonb) - 'skill_slug'
WHERE tenant_id = '73583e84-c025-4880-84b7-360f40602797'
  AND name IN ('Luna Supervisor', 'Luna');
