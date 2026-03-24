-- Gap 04 Phase 2: enforce one active rollout per decision point

-- Add decision_point to experiments for direct uniqueness enforcement
ALTER TABLE learning_experiments
ADD COLUMN IF NOT EXISTS decision_point VARCHAR(50);

-- Backfill from linked candidates
UPDATE learning_experiments le
SET decision_point = pc.decision_point
FROM policy_candidates pc
WHERE le.candidate_id = pc.id AND le.decision_point IS NULL;

-- Partial unique index: only one running split/shadow experiment per tenant+decision_point
CREATE UNIQUE INDEX IF NOT EXISTS uq_active_rollout_per_decision_point
ON learning_experiments(tenant_id, decision_point)
WHERE status = 'running' AND experiment_type IN ('split', 'shadow');
