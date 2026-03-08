-- 040_skills_and_integration_rename.sql
-- Rename skill_configs/skill_credentials to integration_configs/integration_credentials
-- Create new skills and skill_executions tables
-- Add skill_id to execution_traces

-- 1. Rename existing tables
ALTER TABLE IF EXISTS skill_configs RENAME TO integration_configs;
ALTER TABLE IF EXISTS skill_credentials RENAME TO integration_credentials;

-- 2. Rename FK constraint references (update column names in integration_credentials)
-- The FK column skill_config_id stays as-is for now (rename is cosmetic overhead)
-- PostgreSQL auto-updates FK constraints when table is renamed

-- 3. Create skills table
CREATE TABLE IF NOT EXISTS skills (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    name VARCHAR NOT NULL,
    description VARCHAR,
    skill_type VARCHAR NOT NULL,
    config JSON,
    is_system BOOLEAN DEFAULT false,
    enabled BOOLEAN DEFAULT true,
    created_at TIMESTAMP DEFAULT now(),
    updated_at TIMESTAMP DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_skills_tenant ON skills(tenant_id);
CREATE INDEX IF NOT EXISTS idx_skills_type ON skills(skill_type);

-- 4. Create skill_executions table
CREATE TABLE IF NOT EXISTS skill_executions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    skill_id UUID NOT NULL REFERENCES skills(id) ON DELETE CASCADE,
    entity_id UUID REFERENCES knowledge_entities(id) ON DELETE SET NULL,
    agent_id UUID REFERENCES agents(id) ON DELETE SET NULL,
    workflow_run_id UUID REFERENCES pipeline_runs(id) ON DELETE SET NULL,
    input JSON,
    output JSON,
    status VARCHAR NOT NULL,
    duration_ms INTEGER,
    created_at TIMESTAMP DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_skill_exec_tenant ON skill_executions(tenant_id);
CREATE INDEX IF NOT EXISTS idx_skill_exec_skill ON skill_executions(skill_id);
CREATE INDEX IF NOT EXISTS idx_skill_exec_entity ON skill_executions(entity_id);

-- 5. Add skill_id to execution_traces
ALTER TABLE execution_traces ADD COLUMN IF NOT EXISTS skill_id UUID REFERENCES skills(id) ON DELETE SET NULL;
