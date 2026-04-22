-- Migration: 104_agent_marketplace_listings (ALM Pillar 9)
CREATE TABLE IF NOT EXISTS agent_marketplace_listings (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    agent_id UUID NOT NULL REFERENCES agents(id) ON DELETE CASCADE,
    publisher_tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    name VARCHAR(200) NOT NULL,
    description TEXT,
    capabilities JSONB NOT NULL DEFAULT '[]'::jsonb,
    protocol VARCHAR(30) NOT NULL,           -- openai_chat | mcp_sse | webhook | a2a
    endpoint_url VARCHAR(500),
    pricing_model VARCHAR(20) NOT NULL DEFAULT 'free',  -- free | per_call | subscription
    price_per_call_usd NUMERIC(10,4),
    install_count INTEGER NOT NULL DEFAULT 0,
    avg_rating NUMERIC(3,2),
    public BOOLEAN NOT NULL DEFAULT FALSE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_marketplace_listings_agent_id ON agent_marketplace_listings(agent_id);
CREATE INDEX IF NOT EXISTS idx_marketplace_listings_publisher ON agent_marketplace_listings(publisher_tenant_id);
CREATE INDEX IF NOT EXISTS idx_marketplace_listings_public ON agent_marketplace_listings(public) WHERE public = TRUE;

CREATE TABLE IF NOT EXISTS agent_marketplace_subscriptions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    listing_id UUID NOT NULL REFERENCES agent_marketplace_listings(id) ON DELETE CASCADE,
    subscriber_tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    external_agent_id UUID REFERENCES external_agents(id) ON DELETE SET NULL,
    status VARCHAR(20) NOT NULL DEFAULT 'pending',  -- pending | approved | revoked | denied
    call_count INTEGER NOT NULL DEFAULT 0,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE(listing_id, subscriber_tenant_id)
);

CREATE INDEX IF NOT EXISTS idx_marketplace_subs_listing ON agent_marketplace_subscriptions(listing_id);
CREATE INDEX IF NOT EXISTS idx_marketplace_subs_subscriber ON agent_marketplace_subscriptions(subscriber_tenant_id);

INSERT INTO _migrations(filename) VALUES ('104_agent_marketplace_listings') ON CONFLICT DO NOTHING;
