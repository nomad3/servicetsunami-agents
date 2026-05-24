-- 150_seed_code_reviewer_agent.down.sql

DELETE FROM agents
WHERE tenant_id = '752626d9-8b2c-4aa2-87ef-c458d48bd38a'
  AND lower(name) = 'code reviewer'
  AND role = 'code_reviewer';
