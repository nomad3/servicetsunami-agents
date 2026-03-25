-- Simulation personas: synthetic industry users
CREATE TABLE IF NOT EXISTS simulation_personas (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    industry TEXT NOT NULL,
    role TEXT NOT NULL,
    typical_actions TEXT[] NOT NULL DEFAULT '{}',
    persona_config JSONB NOT NULL DEFAULT '{}',
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMP NOT NULL DEFAULT NOW()
);
CREATE INDEX idx_simulation_personas_tenant ON simulation_personas(tenant_id);

-- Simulation scenarios: generated test cases per persona
CREATE TABLE IF NOT EXISTS simulation_scenarios (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    persona_id UUID NOT NULL REFERENCES simulation_personas(id) ON DELETE CASCADE,
    cycle_date DATE NOT NULL DEFAULT CURRENT_DATE,
    scenario_type TEXT NOT NULL, -- simple_query, tool_exercise, memory_recall, multi_step, edge_case, industry_specific, adversarial
    message TEXT NOT NULL,
    expected_criteria JSONB NOT NULL DEFAULT '{}',
    status TEXT NOT NULL DEFAULT 'pending', -- pending, executing, completed, failed
    created_at TIMESTAMP NOT NULL DEFAULT NOW()
);
CREATE INDEX idx_simulation_scenarios_tenant ON simulation_scenarios(tenant_id, cycle_date);
CREATE INDEX idx_simulation_scenarios_persona ON simulation_scenarios(persona_id);

-- Simulation results: actual responses and scores
CREATE TABLE IF NOT EXISTS simulation_results (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    scenario_id UUID NOT NULL REFERENCES simulation_scenarios(id) ON DELETE CASCADE,
    response_text TEXT,
    quality_score NUMERIC(5,2),
    dimension_scores JSONB DEFAULT '{}',
    failure_type TEXT, -- tool_not_found, tool_failed, no_memory, wrong_memory, bad_reasoning, safety_blocked, timeout, hallucination
    failure_detail TEXT,
    is_simulation BOOLEAN NOT NULL DEFAULT TRUE,
    executed_at TIMESTAMP NOT NULL DEFAULT NOW()
);
CREATE INDEX idx_simulation_results_tenant ON simulation_results(tenant_id);
CREATE INDEX idx_simulation_results_scenario ON simulation_results(scenario_id);

-- Skill gaps: capability gaps detected from simulation failures
CREATE TABLE IF NOT EXISTS skill_gaps (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    gap_type TEXT NOT NULL, -- tool_missing, knowledge_gap, prompt_weakness
    description TEXT NOT NULL,
    industry TEXT,
    frequency INTEGER NOT NULL DEFAULT 1,
    severity TEXT NOT NULL DEFAULT 'medium', -- low, medium, high
    proposed_fix TEXT,
    status TEXT NOT NULL DEFAULT 'detected', -- detected, acknowledged, in_progress, resolved
    detected_at TIMESTAMP NOT NULL DEFAULT NOW(),
    resolved_at TIMESTAMP
);
CREATE INDEX idx_skill_gaps_tenant ON skill_gaps(tenant_id, status);
