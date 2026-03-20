# Data Cleanup + Backfill Execution Plan

> Clean stale data from the rebrand, align DB state, then run the full knowledge backfill with auto-scoring.

**Date:** 2026-03-20
**Status:** Ready to execute
**Pre-requisites:** All services running, Ollama with 2 models, 115GB free disk

---

## Phase 1: Database Cleanup (stale/orphan data)

### 1.1 Audit current state

| Check | Query | Why |
|-------|-------|-----|
| Orphan entities (no tenant) | `SELECT COUNT(*) FROM knowledge_entities WHERE tenant_id IS NULL` | Bad seeds |
| Duplicate entities | `SELECT name, entity_type, COUNT(*) FROM knowledge_entities WHERE tenant_id = :tid GROUP BY name, entity_type HAVING COUNT(*) > 1` | Backfill ran twice |
| Empty observations | `SELECT COUNT(*) FROM knowledge_observations WHERE observation_text IS NULL OR LENGTH(observation_text) < 10` | Junk rows |
| Stale RL experiences | `SELECT decision_point, COUNT(*) FROM rl_experiences WHERE tenant_id = :tid GROUP BY decision_point` | Understand current distribution |
| Orphan embeddings | `SELECT content_type, COUNT(*) FROM embeddings WHERE tenant_id = :tid GROUP BY content_type` | Know what's embedded |
| Old ADK references | `SELECT COUNT(*) FROM knowledge_entities WHERE extraction_platform LIKE '%adk%'` | Dead references |

### 1.2 Clean duplicates

- Deduplicate knowledge_entities by (name, entity_type, tenant_id) — keep the one with most recent updated_at
- Deduplicate knowledge_observations by observation_text hash — keep first created
- Remove entities with extraction_platform='adk' or source references to deleted code

### 1.3 Clean stale embeddings

- Delete embeddings whose content_id references a deleted entity/observation
- Re-embed any entity or observation that has NULL embedding

### 1.4 Validate tenant data

- Ensure all entities have valid tenant_id
- Ensure all observations have valid tenant_id
- Ensure rl_experiences have valid tenant_id
- Check for test user data that should be removed

---

## Phase 2: Schema Alignment (model vs DB drift)

Luna's recent PRs added columns to models that may not exist in the DB yet.

### 2.1 Check model-DB alignment

| Model | Column to check | Migration |
|-------|----------------|-----------|
| KnowledgeObservation | `confidence`, `updated_at` | Added manually, verify |
| KnowledgeEntity | `extraction_platform`, `extraction_agent` | Should exist from Luna's PR |
| RLExperience | `reward_components`, `reward_source` | Should exist from RL PR |
| WebhookConnector | All columns | Migration 047 |
| MCPServerConnector | All columns | Migration 048 |

### 2.2 Run any missing column adds

```sql
-- Verify and add if missing
ALTER TABLE knowledge_observations ADD COLUMN IF NOT EXISTS confidence FLOAT DEFAULT 0.9;
ALTER TABLE knowledge_observations ADD COLUMN IF NOT EXISTS updated_at TIMESTAMP DEFAULT NOW();
ALTER TABLE knowledge_observations ADD COLUMN IF NOT EXISTS source_platform VARCHAR(100);
ALTER TABLE knowledge_observations ADD COLUMN IF NOT EXISTS source_agent VARCHAR(100);
```

---

## Phase 3: Knowledge Backfill — Claude Code Sessions

### 3.1 Extract from host machine

| Source | Location | Messages | Approach |
|--------|----------|----------|----------|
| servicetsunami-agents | `~/.claude/projects/...-servicetsunami-agents/` | 14,240 user + 20,941 assistant | Full extraction |
| integral | `~/.claude/projects/...-integral/` | ~2,270 per session | Full extraction |
| pharmapp | `~/.claude/projects/...-pharmapp/` | ~1,300 total | Full extraction |
| health-pets | `~/.claude/projects/...-health-pets/` | ~877 per session | Full extraction |
| infra-control-plane | `~/.claude/projects/...-infra-control-plane-center/` | ~597 per session | Full extraction |
| dentalERP | `~/.claude/projects/...-dentalERP/` | ~312 total | Full extraction |
| Others (6 projects) | Various | ~2,000 total | Entities + observations only |

### 3.2 Extraction pipeline

```
For each session file:
  1. Parse JSONL → user/assistant message pairs
  2. Extract entities (pattern matching: projects, technologies, people)
  3. Extract observations (architecture decisions, debugging patterns, feature requests)
  4. Score conversation pairs via Ollama (async, batched)
  5. Insert entities (deduplicated) → knowledge_entities
  6. Insert observations → knowledge_observations
  7. Insert scored pairs → rl_experiences (reward_source='auto_quality_backfill')
  8. Generate embeddings for new entities + observations
```

### 3.3 Scoring budget

| Pairs to score | Time per pair | Total time |
|----------------|---------------|------------|
| ~5,000 (capped at 20/session × ~250 sessions) | ~4 seconds (Ollama 1.5B) | ~5.5 hours |
| ~1,000 (capped at 10/session, top sessions only) | ~4 seconds | ~1.1 hours |

**Recommendation:** Start with 10 pairs per session (~1 hour), then run full scoring overnight.

---

## Phase 4: Knowledge Backfill — Git History

### 4.1 Commit extraction

For all 29 repos in `~/Documents/GitHub/`:
- Extract last 100 commits per repo (author, message, files, date)
- Create project entities with commit stats
- Create contributor entities (people who worked with you)
- Cross-reference branches with Claude Code sessions

### 4.2 GitHub PRs

Via `gh` CLI for repos with PRs:
- Fetch merged PRs → positive RL signal
- Fetch closed (not merged) PRs → negative RL signal
- Fetch PR review comments → observation text

---

## Phase 5: Knowledge Backfill — Connected Services

### 5.1 Gmail (via Luna MCP tools)

Ask Luna: "Scan my inbox for the last 6 months. Extract all contacts, build entity profiles for each person with their role, company, and our relationship context."

### 5.2 Google Drive (via Luna MCP tools)

Ask Luna: "List all my Google Drive documents. Read the text content of any proposals, contracts, or business documents and extract key entities and observations."

### 5.3 Gemini Sessions (future — protobuf decode needed)

The `.gemini/antigravity/brain/` (2GB) and `conversations/` (671MB) contain 67 sessions in protobuf format. Requires:
- Reverse-engineer or find the Gemini CLI proto schema
- Decode .pb files to text
- Same extraction pipeline as Claude Code sessions

---

## Phase 6: Embed Everything

After all data is seeded:

1. Count entities without embeddings
2. Count observations without embeddings
3. Run batch embedding backfill (existing `EmbeddingBackfillWorkflow` or direct)
4. Verify embedding count matches entity + observation count

---

## Execution Order

```
Step 1: Phase 1 (cleanup) — 15 minutes
Step 2: Phase 2 (schema alignment) — 5 minutes
Step 3: Phase 3 quick run (10 pairs/session) — ~1 hour
Step 4: Phase 4 (git history) — 10 minutes
Step 5: Phase 6 (embed everything) — 30 minutes
Step 6: Phase 3 full run (20 pairs/session) — overnight
Step 7: Phase 5 (Gmail/Drive via Luna) — manual, next day
```

## Expected Results

| Metric | Before | After Cleanup | After Backfill |
|--------|--------|---------------|----------------|
| Entities | 300 | ~280 (deduped) | ~500+ |
| Observations | 1,089 | ~1,000 (cleaned) | ~3,000+ |
| RL Experiences | 59 | 59 (unchanged) | ~1,000+ |
| Embeddings | 1,617 | ~1,500 (orphans removed) | ~4,000+ |
| Avg RL reward data quality | Manual only | Manual only | Auto-scored + manual |
