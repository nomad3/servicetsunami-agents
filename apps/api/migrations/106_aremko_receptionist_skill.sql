-- 106 — Bind aremko's Luna agent to the aremko_receptionist skill.
--
-- Why: Jorge (aremko's owner) reported Luna inventing cabin names
-- and prices. The receptionist skill at
-- apps/api/app/skills/tenant_73583e84-c025-4880-84b7-360f40602797/aremko_receptionist/
-- contains the real catalog (5 cabañas, 8 tinajas, 1 masaje, 1 desayuno),
-- a hard tool-use policy, and aremko's brand voice. Pointing the
-- supervisor agent at this skill puts those constraints into CLAUDE.md
-- on every aremko Luna turn.
--
-- Idempotent: applies only if the agent exists for the tenant. Other
-- tenants are unaffected.

UPDATE agents
SET config = COALESCE(config::jsonb, '{}'::jsonb) || '{"skill_slug": "aremko_receptionist"}'::jsonb
WHERE tenant_id = '73583e84-c025-4880-84b7-360f40602797'
  AND name IN ('Luna Supervisor', 'Luna');
