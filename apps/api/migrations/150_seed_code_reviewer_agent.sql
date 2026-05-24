-- 150_seed_code_reviewer_agent.sql
--
-- Seed the Code Reviewer agent for Simon's tenant.
--
-- Design: docs/plans/2026-05-24-luna-team-population.md
-- FileSkill: apps/api/app/agents/_bundled/code-reviewer/skill.md
-- Luna sign-off: dialogue session 05979efd-a06a-4956-9df9-3fd84ec3c10d.
--
-- Scope: narrow rollout — Simon's tenant only (752626d9-8b2c-4aa2-87ef-c458d48bd38a).
-- Other tenants are NOT seeded in this migration. Pattern follows P0b's narrow-first
-- precedent: ship to one tenant, let it bake, then add a broader backfill migration
-- once the agent's invocation patterns are validated.
--
-- The Agent row exists alongside the bundled FileSkill that defines the persona.
-- Runtime: cli_session_manager resolves agent_slug='code-reviewer' to BOTH
-- (a) the FileSkill via skill_manager.get_skill_by_slug for persona content,
-- (b) this Agent row via the slug-normalized name match for tool_groups + JWT scope.
--
-- tool_groups is read-only safe by design: github + knowledge + meta only.
-- No `shell`. The Code Reviewer cannot execute code — review is read-only.

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
VALUES (
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
)
ON CONFLICT ON CONSTRAINT idx_agents_tenant_name_unique DO NOTHING;

-- Self-record per the migrations README belt-and-suspenders convention.
INSERT INTO _migrations(filename) VALUES ('150_seed_code_reviewer_agent.sql')
ON CONFLICT DO NOTHING;
