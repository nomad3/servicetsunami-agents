-- Migration 142: per-tenant kill-switch for NightlyReflectionWorkflow.
--
-- Per canonical design §5/O2 + locked decision #4 (Luna's sign-off
-- 2026-05-20): "Per-tenant kill-switch required before this schedules
-- anything. Operators can pause synthesis per tenant from the Den."
--
-- Default OFF in prod. Synthesis is sensitive — it writes reflections
-- back to memory, and the canonical design treats the kill-switch as
-- the hard gate before any tenant gets bulk-synthesis runs. Operators
-- opt in per tenant once they've reviewed the dry-run output.
--
-- Idempotent — safe to re-run.
ALTER TABLE tenant_features
    ADD COLUMN IF NOT EXISTS nightly_reflection_enabled BOOLEAN NOT NULL DEFAULT FALSE;

COMMENT ON COLUMN tenant_features.nightly_reflection_enabled IS
    'Gate for NightlyReflectionWorkflow (O2 of #616). Default OFF; '
    'operators flip to TRUE per tenant after reviewing the dry-run '
    'output. When FALSE, the workflow short-circuits at top-of-run.';
