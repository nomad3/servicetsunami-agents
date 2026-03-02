-- Migration 036: Add account_email to skill_configs for multi-account OAuth
-- Allows multiple OAuth accounts per skill per tenant (e.g., 2 Gmail accounts)

-- Add account_email column to identify which provider account this config belongs to
ALTER TABLE skill_configs ADD COLUMN IF NOT EXISTS account_email VARCHAR;

-- Drop the old unique index that only allows one config per skill per tenant
DROP INDEX IF EXISTS idx_skill_configs_tenant_skill;

-- Create new unique index that allows multiple accounts per skill (keyed by email)
-- NULLs are treated as distinct by PostgreSQL, so manual skills (no email) still work
CREATE UNIQUE INDEX IF NOT EXISTS idx_skill_configs_tenant_skill_email
    ON skill_configs(tenant_id, skill_name, account_email);
