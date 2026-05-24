-- 151_fix_code_reviewer_seed.sql
--
-- Fix migration 150's ON CONFLICT clause. The clause referenced
-- `idx_agents_tenant_name_unique` as a CONSTRAINT, but it's a UNIQUE
-- INDEX (with a functional column `lower(name::text)`). Postgres
-- distinguishes — INDEX requires the column-expression form.
--
-- Migration 150's INSERT failed at deploy time, so the Code Reviewer
-- row was NEVER seeded. This migration both seeds the row AND uses
-- the correct guard clause (NOT EXISTS — simpler than ON CONFLICT
-- with a functional unique index).
--
-- Idempotent: re-run is safe (NOT EXISTS guard).

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
    'Code Reviewer',
    'Luna''s native PR/code review agent. Applies superpowers code-review methodology + AgentProvision tenant memory + platform invariants from the 2026-05-23 substrate-hardening sprint. Tool surface is read-only (github/knowledge/meta) — review never executes code.',
    '752626d9-8b2c-4aa2-87ef-c458d48bd38a',
    'code_reviewer',
    'supervised',
    2,
    '["github", "knowledge", "meta"]'::jsonb,
    'full',
    '["repo-architecture", "security-incidents", "code-review-norms", "substrate-hardening-history"]'::jsonb,
    'production',
    1,
    FALSE
WHERE NOT EXISTS (
    SELECT 1 FROM agents
    WHERE tenant_id = '752626d9-8b2c-4aa2-87ef-c458d48bd38a'
      AND lower(name) = 'code reviewer'
);

INSERT INTO _migrations(filename) VALUES ('151_fix_code_reviewer_seed.sql')
ON CONFLICT DO NOTHING;
