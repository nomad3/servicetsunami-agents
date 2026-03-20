# Knowledge Backfill + Local ML Training Plan

> Feed Luna all available private data sources, then leverage M4 hardware to build auto-quality scoring, contextual routing, and domain-tuned embeddings.

**Date:** 2026-03-20
**Status:** Ready to execute
**Hardware:** MacBook M4 (16-core Neural Engine, 10-core GPU, unified memory)

---

## Part A: Knowledge Backfill — Feed Luna Everything

### Phase A1: Claude Code Sessions (Priority 1)

**Source:** `~/.claude/projects/` — 1GB, 25K user messages, 38K assistant messages across 10+ projects
**Runs on:** Host machine (not container — sessions are in user home dir)

| Task | Description | Script |
|------|-------------|--------|
| A1.1 | Parse all JSONL session files, extract user/assistant pairs | `scripts/backfill_knowledge_from_sessions.py` (exists) |
| A1.2 | Extract entities: projects, technologies, people, patterns | Pattern matching + keyword extraction |
| A1.3 | Extract observations: architecture decisions, debugging patterns, feature requests | Conversation analysis |
| A1.4 | Extract coding style preferences: naming, structure, tools chosen | Statistical analysis of assistant outputs |
| A1.5 | Seed into knowledge graph with embeddings | Direct DB insert + `embed_and_store()` |

**Estimated yield:** ~200 entities, ~2,000 observations, ~5,000 embeddings

### Phase A2: Git History (Priority 2)

**Source:** `~/Documents/GitHub/*/` — 29 repos, ~1,200 commits

| Task | Description |
|------|-------------|
| A2.1 | Extract commit history from all 29 repos (hash, author, message, files changed) |
| A2.2 | Build project entities with metadata (languages, frameworks, last activity) |
| A2.3 | Extract contributor entities (people who work with you) |
| A2.4 | Identify patterns: most-changed files, hotspots, commit frequency by project |
| A2.5 | Cross-reference commits with Claude Code sessions (same branch/timeframe) |

**Estimated yield:** ~29 project entities, ~50 contributor entities, ~500 commit observations

### Phase A3: GitHub PRs & Issues (Priority 3)

**Source:** GitHub API (29 repos)

| Task | Description |
|------|-------------|
| A3.1 | Fetch all PRs (merged, closed, open) via `gh` CLI |
| A3.2 | Extract PR review patterns: what gets approved, what gets reverted |
| A3.3 | Fetch issues: what problems were reported, what was fixed |
| A3.4 | Build RL training data: PR merged = positive reward, PR reverted = negative |
| A3.5 | Seed as observations + RL experiences |

**Estimated yield:** ~200 PR observations, ~100 RL experiences with real rewards

### Phase A4: Gmail Scan (Priority 4)

**Source:** Gmail API (already connected via OAuth)
**Runs via:** Luna MCP tools (`search_emails`, `read_email`)

| Task | Description |
|------|-------------|
| A4.1 | Scan last 6 months of inbox |
| A4.2 | Extract contacts: name, email, company, relationship |
| A4.3 | Extract deal/project mentions from email subjects and bodies |
| A4.4 | Build business relationship graph (who works with whom) |
| A4.5 | Flag important threads: invoices, proposals, contracts |

**Estimated yield:** ~100 contact entities, ~200 business observations

### Phase A5: Google Drive Documents (Priority 5)

**Source:** Google Drive API (already connected via OAuth)
**Runs via:** Luna MCP tools (`search_drive_files`, `read_drive_file`)

| Task | Description |
|------|-------------|
| A5.1 | List all documents and spreadsheets |
| A5.2 | Read text content of Google Docs and export Sheets as CSV |
| A5.3 | Extract entities: clients, projects, financial data |
| A5.4 | Seed document summaries as observations |

**Estimated yield:** ~50 document observations, ~30 entities

### Phase A6: Shell History + System Context (Priority 6)

**Source:** `~/.zsh_history`, `~/.ssh/config`, Docker, npm/pip

| Task | Description |
|------|-------------|
| A6.1 | Parse zsh_history for most-used commands and workflows |
| A6.2 | Extract SSH hosts/servers you manage |
| A6.3 | List Docker images and running services |
| A6.4 | List globally installed npm/pip packages (your toolchain) |
| A6.5 | Seed as "workflow" and "infrastructure" observations |

**Estimated yield:** ~20 infrastructure entities, ~50 workflow observations

### Phase A7: Jira + Calendar + WhatsApp (Priority 7)

**Source:** Already connected integrations
**Runs via:** Luna MCP tools

| Task | Description |
|------|-------------|
| A7.1 | Scan Jira ST project: all issues, statuses, assignees |
| A7.2 | Scan Calendar: upcoming + past events, attendees |
| A7.3 | Parse WhatsApp session history for business contacts |
| A7.4 | Cross-reference: who appears in email AND calendar AND WhatsApp |

**Estimated yield:** ~30 entities, ~100 observations

---

## Part B: Local ML on M4

### Phase B1: Auto-Quality Scorer (Priority 1 — Biggest Impact)

**Goal:** Automatically rate every Luna response 1-5 without waiting for user feedback. Turns 43 manual ratings into thousands of auto-scored experiences.

| Task | Description | Framework |
|------|-------------|-----------|
| B1.1 | Install Ollama on Mac | `brew install ollama` |
| B1.2 | Pull a small quality-scoring model (Phi-3.5-mini 3.8B or Qwen2.5-3B) | `ollama pull phi3.5` |
| B1.3 | Create quality scoring prompt template | System prompt that rates responses on helpfulness, accuracy, completeness |
| B1.4 | Build `auto_quality_scorer.py` service | Takes (user_message, agent_response) → score 1-5 + reasoning |
| B1.5 | Integrate into chat pipeline: after every response, async score it | Non-blocking — runs in background after response is sent |
| B1.6 | Feed scores back into RL as implicit rewards | `rl_experience_service.assign_reward()` with source="auto_quality" |
| B1.7 | Add quality scores to Learning page dashboard | New metric tile: "Auto Quality Avg" |

**Architecture:**
```
User sends message
  → Luna responds (Claude Code CLI)
  → Response returned to user immediately
  → Background: Ollama scores the response (1-5)
  → Score stored as RL reward (implicit)
  → Learning dashboard updates
```

**Cost:** $0 (runs locally on M4 Neural Engine)
**Latency:** ~500ms per scoring (async, doesn't block response)
**Impact:** 100x more RL training data

### Phase B2: Contextual Bandit Router (Priority 2)

**Goal:** Replace the simple keyword-based routing with a real ML model that learns which platform/agent handles which task type best.

| Task | Description | Framework |
|------|-------------|-----------|
| B2.1 | Feature engineering: extract features from user messages | scikit-learn |
| B2.2 | Build feature vector: task_type, entity_count, message_length, time_of_day, platform_history, user_rating_history | numpy |
| B2.3 | Train initial contextual bandit (LinUCB or Thompson Sampling) | Custom Python + numpy |
| B2.4 | Warm-start from existing 43 RL experiences | Load historical data |
| B2.5 | Replace `_infer_task_type()` in agent_router.py with bandit prediction | Direct integration |
| B2.6 | Incremental online learning: update model after each interaction | Train-on-predict pattern |
| B2.7 | A/B testing: run bandit alongside current router, compare | Exploration flag in RL |

**Architecture:**
```
User message
  → Feature extraction (task_type, entities, history)
  → Bandit predicts best (platform, agent) with uncertainty
  → If uncertain: explore (try different platform)
  → If confident: exploit (use best known platform)
  → After response + reward: update bandit weights
```

**Model:** LinUCB with ~20 features, ~5 arms (claude_code, gemini_cli, codex_cli, luna, data_analyst)
**Training:** Online — updates after every interaction, no batch needed
**Storage:** Model weights in PostgreSQL (JSON column on rl_policy_state)

### Phase B3: Domain-Tuned Embeddings (Priority 3)

**Goal:** Fine-tune nomic-embed-text on your domain data so semantic search is more relevant.

| Task | Description | Framework |
|------|-------------|-----------|
| B3.1 | Build training pairs from Claude Code sessions: (user_query, relevant_entity) | Custom extraction |
| B3.2 | Build negative pairs: (user_query, unrelated_entity) | Random sampling |
| B3.3 | Fine-tune nomic-embed with contrastive learning | sentence-transformers + PyTorch MPS |
| B3.4 | Evaluate: compare search quality before/after | A/B on knowledge search |
| B3.5 | Deploy: replace model in embedding_service.py | Hot-swap model path |
| B3.6 | Re-embed all 1,522 existing embeddings with new model | Batch backfill |

**Training data:** ~5,000 pairs from Claude Code sessions
**Training time:** ~30 min on M4 GPU (MPS backend)
**Improvement expected:** 20-40% better search relevance on domain queries

### Phase B4: Local Entity Extraction (Priority 4)

**Goal:** Extract entities from chat messages without Claude API calls — zero cost, 100ms latency.

| Task | Description | Framework |
|------|-------------|-----------|
| B4.1 | Fine-tune Llama 3.2 3B on entity extraction task | MLX or Ollama |
| B4.2 | Training data: existing knowledge entities + their source text | From backfill data |
| B4.3 | Build extraction pipeline: message → entities JSON | Structured output |
| B4.4 | Integrate into chat pipeline as post-processing step | Async, non-blocking |
| B4.5 | Feed extracted entities into knowledge graph automatically | `knowledge_service.create_entity()` |

**Cost:** $0 (local model)
**Latency:** ~200ms per message
**Impact:** Knowledge graph grows automatically from every conversation

---

## Implementation Order

```
Week 1: A1 (Claude sessions) + A2 (Git history) + B1.1-B1.3 (Install Ollama + scorer prompt)
Week 2: B1.4-B1.7 (Auto-quality scorer integration) + A3 (GitHub PRs)
Week 3: A4 (Gmail scan) + A5 (Drive scan) + B2.1-B2.4 (Bandit warm-start)
Week 4: B2.5-B2.7 (Bandit deployment) + A6 (Shell/system) + A7 (Jira/Calendar)
Week 5: B3 (Embedding fine-tuning) + backfill re-embedding
Week 6: B4 (Local entity extraction) + end-to-end testing
```

## Expected Results After 6 Weeks

| Metric | Before | After |
|--------|--------|-------|
| Knowledge entities | 295 | ~700+ |
| Observations | 1,087 | ~4,000+ |
| Embeddings | 1,522 | ~6,000+ |
| RL experiences | 43 | ~500+ (auto-scored) |
| RL training data quality | Manual only | Auto-scored + manual |
| Routing accuracy | Keyword-based | ML bandit with online learning |
| Search relevance | Generic embeddings | Domain-tuned |
| Entity extraction cost | Claude API ($) | Local model ($0) |
| Quality scoring | Manual thumbs up/down | Automatic 1-5 scoring |

## Dependencies

- Ollama installed (`brew install ollama`)
- PyTorch with MPS backend (`pip install torch` — auto-detects M4)
- scikit-learn (`pip install scikit-learn`)
- sentence-transformers (already installed in API container)
- MLX (optional, for Phase B4): `pip install mlx mlx-lm`
