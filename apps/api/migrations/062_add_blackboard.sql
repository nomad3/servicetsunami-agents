-- Gap 06 Phase 1: shared blackboard for multi-agent collaboration

CREATE TABLE IF NOT EXISTS blackboards (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    plan_id UUID REFERENCES plans(id),
    goal_id UUID REFERENCES goal_records(id),
    title VARCHAR(500) NOT NULL,
    status VARCHAR(30) NOT NULL DEFAULT 'active',
    version INTEGER NOT NULL DEFAULT 0,
    created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMP WITHOUT TIME ZONE NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_blackboards_tenant_status
ON blackboards(tenant_id, status);


CREATE TABLE IF NOT EXISTS blackboard_entries (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    blackboard_id UUID NOT NULL REFERENCES blackboards(id) ON DELETE CASCADE,
    board_version INTEGER NOT NULL,
    entry_type VARCHAR(50) NOT NULL,
    content TEXT NOT NULL,
    evidence JSONB NOT NULL DEFAULT '[]'::jsonb,
    confidence DOUBLE PRECISION NOT NULL DEFAULT 0.7,
    author_agent_slug VARCHAR(100) NOT NULL,
    author_role VARCHAR(50) NOT NULL DEFAULT 'contributor',
    parent_entry_id UUID REFERENCES blackboard_entries(id),
    supersedes_entry_id UUID REFERENCES blackboard_entries(id),
    status VARCHAR(30) NOT NULL DEFAULT 'proposed',
    resolved_by_agent VARCHAR(100),
    resolution_reason TEXT,
    created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL DEFAULT NOW()
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_blackboard_entries_board_version
ON blackboard_entries(blackboard_id, board_version);

CREATE INDEX IF NOT EXISTS idx_blackboard_entries_board_status
ON blackboard_entries(blackboard_id, status)
WHERE status IN ('proposed', 'accepted', 'disputed');
