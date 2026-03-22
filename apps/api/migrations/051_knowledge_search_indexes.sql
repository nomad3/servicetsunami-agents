-- HNSW vector indexes for fast approximate nearest neighbor search
CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_knowledge_entities_embedding_hnsw
ON knowledge_entities USING hnsw (embedding vector_cosine_ops)
WITH (m = 16, ef_construction = 64);

CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_knowledge_observations_embedding_hnsw
ON knowledge_observations USING hnsw (embedding vector_cosine_ops)
WITH (m = 16, ef_construction = 64);

-- GIN trigram indexes for fast ILIKE text search fallback
CREATE EXTENSION IF NOT EXISTS pg_trgm;

CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_knowledge_entities_name_trgm
ON knowledge_entities USING gin (name gin_trgm_ops);

CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_knowledge_entities_desc_trgm
ON knowledge_entities USING gin (description gin_trgm_ops);
