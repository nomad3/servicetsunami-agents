-- Cost budgets: per-tenant daily/weekly/monthly spend caps for autonomous learning cycles
CREATE TABLE IF NOT EXISTS cost_budgets (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    budget_type TEXT NOT NULL,             -- daily, weekly, monthly
    amount_usd NUMERIC(10, 4) NOT NULL DEFAULT 5.0,
    current_period_start TIMESTAMP NOT NULL DEFAULT DATE_TRUNC('day', NOW()),
    current_spend_usd NUMERIC(10, 4) NOT NULL DEFAULT 0.0,
    alert_threshold NUMERIC(4, 3) NOT NULL DEFAULT 0.80, -- alert at 80% of budget
    updated_at TIMESTAMP NOT NULL DEFAULT NOW(),
    UNIQUE(tenant_id, budget_type)
);
CREATE INDEX idx_cost_budgets_tenant ON cost_budgets(tenant_id);

-- Cost tracking log: per-cycle spend records
CREATE TABLE IF NOT EXISTS cost_tracking_log (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    cycle_date DATE NOT NULL DEFAULT CURRENT_DATE,
    activity_name TEXT NOT NULL,   -- which learning activity incurred the cost
    tokens_used INTEGER DEFAULT 0,
    cost_usd NUMERIC(10, 6) DEFAULT 0.0,
    platform TEXT,                 -- claude, codex, local, etc.
    created_at TIMESTAMP NOT NULL DEFAULT NOW()
);
CREATE INDEX idx_cost_tracking_tenant ON cost_tracking_log(tenant_id);
CREATE INDEX idx_cost_tracking_date ON cost_tracking_log(cycle_date);
