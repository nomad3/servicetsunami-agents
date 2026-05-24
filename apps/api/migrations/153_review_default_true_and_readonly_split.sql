-- 153_review_default_true_and_readonly_split.sql
--
-- Two related fixes per Luna's step-4 self-application findings on
-- 2026-05-24 (concern observation b0533a44, decision e884cd93):
--
--   1. agents.tool_groups_review_required default flips FALSE → TRUE.
--      New agents should land in the review queue by default;
--      operator explicitly clears the flag after confirming the
--      tool_groups match the agent's advertised capability.
--      Existing FALSE rows are left as-is EXCEPT the two newly-
--      introduced ones below.
--
--   2. Code Reviewer and Substrate Sentinel — both seeded in the
--      last day with tool_groups=["github","knowledge","meta"] —
--      claim "read-only" but "knowledge" contains mutating tools
--      (record_observation, create_entity, merge_entities,
--      update_entity). Two corrections:
--
--      (a) tool_groups updated to use "knowledge_readonly" instead
--          of "knowledge". The split was added to
--          apps/api/app/services/tool_groups.py in the same PR.
--          Backwards-compatible: existing agents using "knowledge"
--          continue to work unchanged.
--
--      (b) tool_groups_review_required set TRUE for both, so they
--          surface on the operator dashboard for a deliberate
--          review pass on the corrected tool_groups.
--
-- Pre-existing operator-curated agents (Luna Supervisor, Luna General
-- Assistant) are NOT touched — they have intentional "knowledge"
-- mutating access and are not in the review queue by operator
-- choice. The default change applies to future agents.
--
-- Existing review_required=TRUE agents (Triage Agent, Data
-- Investigator, Root Cause Analyst, Incident Commander — all set
-- TRUE by migration 149's NULL backfill) are also untouched. They
-- remain in the review queue per the original P0a sequence.

BEGIN;

-- ── 1. Flip column default ─────────────────────────────────────────────

ALTER TABLE agents
    ALTER COLUMN tool_groups_review_required SET DEFAULT TRUE;

COMMENT ON COLUMN agents.tool_groups_review_required IS
    '2026-05-24: default is TRUE — new agents land in the future '
    'operator review queue by default. NOTE: as of 2026-05-24 there '
    'is no review-queue endpoint or UI yet; the flag is queryable '
    'only via SQL/CLI (the queue surface is a known follow-up). The '
    'flag still has value as a runtime gate and audit signal even '
    'without UI. Original P0a (2026-05-23) logic for NULL-backfilled '
    'agents continues to apply: cleared on operator action or '
    '1-week auto-clear that requires BOTH zero shadow-denial '
    'activity AND observed activity.';

-- ── 2. Retroactive flip + tool_groups correction for the 2 new agents ──

-- Code Reviewer: was [github, knowledge, meta] + review_required=FALSE
UPDATE agents
SET tool_groups = '["github", "knowledge_readonly", "meta"]'::jsonb,
    tool_groups_review_required = TRUE
WHERE tenant_id = '752626d9-8b2c-4aa2-87ef-c458d48bd38a'
  AND role = 'code_reviewer'
  AND lower(name) = 'code reviewer';

-- Substrate Sentinel: was [github, knowledge, meta] + review_required=FALSE
UPDATE agents
SET tool_groups = '["github", "knowledge_readonly", "meta"]'::jsonb,
    tool_groups_review_required = TRUE
WHERE tenant_id = '752626d9-8b2c-4aa2-87ef-c458d48bd38a'
  AND role = 'substrate_sentinel'
  AND lower(name) = 'substrate sentinel';

INSERT INTO _migrations(filename) VALUES ('153_review_default_true_and_readonly_split.sql')
ON CONFLICT DO NOTHING;

COMMIT;
