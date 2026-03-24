-- Gap 01 Phase 1: world state assertions and snapshots

CREATE TABLE IF NOT EXISTS world_state_assertions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    subject_entity_id UUID REFERENCES knowledge_entities(id),
    subject_slug VARCHAR(200) NOT NULL,
    attribute_path VARCHAR(300) NOT NULL,
    value_json JSONB NOT NULL,
    previous_value_json JSONB,
    confidence DOUBLE PRECISION NOT NULL DEFAULT 0.7,
    source_observation_id UUID REFERENCES knowledge_observations(id),
    source_type VARCHAR(50) NOT NULL DEFAULT 'observation',
    corroboration_count INTEGER NOT NULL DEFAULT 1,
    status VARCHAR(30) NOT NULL DEFAULT 'active',
    superseded_by_id UUID REFERENCES world_state_assertions(id),
    valid_from TIMESTAMP WITHOUT TIME ZONE NOT NULL DEFAULT NOW(),
    valid_to TIMESTAMP WITHOUT TIME ZONE,
    freshness_ttl_hours INTEGER NOT NULL DEFAULT 168,
    created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMP WITHOUT TIME ZONE NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_wsa_tenant_subject_attr
ON world_state_assertions(tenant_id, subject_slug, attribute_path)
WHERE status = 'active';

CREATE INDEX IF NOT EXISTS idx_wsa_tenant_status
ON world_state_assertions(tenant_id, status);

CREATE INDEX IF NOT EXISTS idx_wsa_source_observation
ON world_state_assertions(source_observation_id)
WHERE source_observation_id IS NOT NULL;


CREATE TABLE IF NOT EXISTS world_state_snapshots (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    subject_entity_id UUID REFERENCES knowledge_entities(id),
    subject_slug VARCHAR(200) NOT NULL,
    projected_state JSONB NOT NULL DEFAULT '{}'::jsonb,
    assertion_count INTEGER NOT NULL DEFAULT 0,
    min_confidence DOUBLE PRECISION NOT NULL DEFAULT 1.0,
    avg_confidence DOUBLE PRECISION NOT NULL DEFAULT 1.0,
    unstable_attributes JSONB NOT NULL DEFAULT '[]'::jsonb,
    last_projected_at TIMESTAMP WITHOUT TIME ZONE NOT NULL DEFAULT NOW(),
    created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMP WITHOUT TIME ZONE NOT NULL DEFAULT NOW()
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_world_state_snapshot
ON world_state_snapshots(tenant_id, subject_slug);
