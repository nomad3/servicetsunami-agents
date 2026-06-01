-- 157_luna_split_luna_learn_tool_group.sql
--
-- PR #728 review IMPORTANT4 — silent privilege escalation.
--
-- Migration 156 added `learning` to Luna Supervisor's tool_groups, and
-- PR #726 quietly expanded the `learning` group's tool list from the
-- original 5 autonomous-learning tools to also include the 7 Luna Learn
-- from Media primitives (extract_media, transcribe_url,
-- synthesize_skill_draft, dispatch_skill_review, run_synthetic_test,
-- install_skill, diffuse_learning). The effect: every agent already
-- granted `learning` silently gained install_skill (writes to the skills
-- library) and diffuse_learning (writes KG observations) — including any
-- future read-only learner agent that asks for `learning` thinking it
-- means the pre-existing autonomous-learning subsystem.
--
-- Fix: revert tool_groups.py so `learning` only carries the original 5
-- tools, and add a new `luna_learn` group for the 7 video→skill
-- primitives. This migration updates the DB-side `agents.tool_groups`
-- on Simon's tenant for BOTH Luna rows (Luna Supervisor — supervisor
-- persona — and the chat-facing Luna agent) so the runtime gate matches
-- the new code-side group registry.
--
-- The WHERE clauses are EXISTENCE-based (jsonb @>) rather than
-- shape-exact (= literal). This is intentional: migration 154's
-- shape-exact pattern fits a one-shot expansion, but here we want the
-- migration to land idempotently regardless of whatever ad-hoc tool
-- group additions an operator may have made by hand.

BEGIN;

-- Luna Supervisor (the supervisor persona — id 9d85ff11-... per
-- agentprovision_product_family memory).
UPDATE agents
SET tool_groups = tool_groups || '["luna_learn"]'::jsonb
WHERE name = 'Luna Supervisor'
  AND tenant_id = '752626d9-8b2c-4aa2-87ef-c458d48bd38a'
  AND NOT (tool_groups @> '["luna_learn"]'::jsonb);

-- Chat-facing Luna (id cfb6dd14-... per the memory file). This row was
-- hot-patched in tonight's session to add `learning`; we now add
-- `luna_learn` alongside it. We keep `learning` in place — she also
-- legitimately uses the autonomous-learning subsystem tools, and
-- removing it would force another live hot-patch cycle.
UPDATE agents
SET tool_groups = tool_groups || '["luna_learn"]'::jsonb
WHERE name = 'Luna'
  AND tenant_id = '752626d9-8b2c-4aa2-87ef-c458d48bd38a'
  AND NOT (tool_groups @> '["luna_learn"]'::jsonb);

INSERT INTO _migrations(filename) VALUES ('157_luna_split_luna_learn_tool_group.sql')
ON CONFLICT DO NOTHING;

COMMIT;
