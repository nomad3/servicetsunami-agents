DROP INDEX IF EXISTS uq_value_set_version;
ALTER TABLE tenant_features
    DROP COLUMN IF EXISTS value_layer_enabled;
