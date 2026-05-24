-- 152_seed_substrate_sentinel_agent.down.sql

DELETE FROM agents
WHERE tenant_id = '752626d9-8b2c-4aa2-87ef-c458d48bd38a'
  AND lower(name) = 'substrate sentinel'
  AND role = 'substrate_sentinel';
