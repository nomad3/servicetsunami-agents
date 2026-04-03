-- Migration 081: Set default CLI platform to claude_code for all tenants
-- Previously some tenants may have been set to codex; standardize on claude_code

UPDATE tenant_features
SET default_cli_platform = 'claude_code'
WHERE default_cli_platform != 'claude_code'
   OR default_cli_platform IS NULL;

-- Also ensure the column default is claude_code
ALTER TABLE tenant_features
ALTER COLUMN default_cli_platform SET DEFAULT 'claude_code';
