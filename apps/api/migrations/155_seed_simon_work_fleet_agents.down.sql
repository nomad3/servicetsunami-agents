-- 155_seed_simon_work_fleet_agents.down.sql
--
-- Remove the Simon work-fleet agents seeded by migration 155.

BEGIN;

DELETE FROM agents
WHERE tenant_id = '752626d9-8b2c-4aa2-87ef-c458d48bd38a'
  AND lower(name) IN (
      'innovus aws platform',
      'innovus terraform infrastructure',
      'integral sre ops',
      'levi sre platform',
      'levi mdm pc9 triage'
  );

DELETE FROM _migrations
WHERE filename = '155_seed_simon_work_fleet_agents.sql';

COMMIT;
