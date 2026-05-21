-- Migration 143: per-tenant opt-in for `creative` reflection kind.
--
-- Per canonical design §8 locked decision #1: "creative reflections —
-- opt-in per tenant, default off." Without this gate the synthesis
-- path could produce creative reflections for tenants that didn't
-- ask for them.
--
-- Idempotent — safe to re-run.
ALTER TABLE tenant_features
    ADD COLUMN IF NOT EXISTS creative_reflections_enabled BOOLEAN NOT NULL DEFAULT FALSE;

COMMENT ON COLUMN tenant_features.creative_reflections_enabled IS
    'Gate for `creative` kind reflections produced by the offline '
    'synthesis loop. Default OFF; operators flip per tenant. '
    'Enforced by reflection_validators.validate_creative_opt_in '
    'inside write_reflections.';
