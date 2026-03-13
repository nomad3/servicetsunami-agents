-- Migration 046: Add active LLM provider selection to tenant_features
-- Allows tenants to choose their preferred LLM provider for agent chat

ALTER TABLE tenant_features
    ADD COLUMN IF NOT EXISTS active_llm_provider VARCHAR(50) DEFAULT 'gemini_llm';

COMMENT ON COLUMN tenant_features.active_llm_provider IS 'Integration name of the active LLM provider (e.g. gemini_llm, anthropic_llm)';
