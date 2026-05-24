-- 152_seed_substrate_sentinel_agent.sql
--
-- Seed the Substrate Sentinel agent for Simon's tenant.
--
-- Design: docs/plans/2026-05-24-substrate-sentinel-agent.md
-- FileSkill: apps/api/app/agents/_bundled/substrate-sentinel/skill.md
-- Scope: narrow rollout - Simon's tenant only.
--
-- The Sentinel is read-only by default: github + knowledge + meta.
-- It has no shell access and does not execute probes itself.

INSERT INTO agents (
    id,
    name,
    description,
    tenant_id,
    role,
    autonomy_level,
    max_delegation_depth,
    tool_groups,
    default_model_tier,
    memory_domains,
    status,
    version,
    tool_groups_review_required
)
SELECT
    gen_random_uuid(),
    'Substrate Sentinel',
    'Luna''s native protocol-integrity agent. Reviews tenant isolation, tool-scope enforcement, audit visibility, JWT propagation, fail-closed behavior, and native-CLI bypass risk. Read-only tool surface: github/knowledge/meta.',
    '752626d9-8b2c-4aa2-87ef-c458d48bd38a',
    'substrate_sentinel',
    'supervised',
    2,
    '["github", "knowledge", "meta"]'::jsonb,
    'full',
    '["security-incidents", "substrate-hardening-history", "mcp-auth", "audit-integrity", "tenant-isolation"]'::jsonb,
    'production',
    1,
    FALSE
WHERE NOT EXISTS (
    SELECT 1 FROM agents
    WHERE tenant_id = '752626d9-8b2c-4aa2-87ef-c458d48bd38a'
      AND lower(name) = 'substrate sentinel'
);

INSERT INTO _migrations(filename) VALUES ('152_seed_substrate_sentinel_agent.sql')
ON CONFLICT DO NOTHING;
