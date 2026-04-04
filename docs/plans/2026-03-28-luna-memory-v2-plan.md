# Luna Memory v2 — Advanced Memory System Plan

**Date**: 2026-03-28
**Master Plan**: `2026-03-29-luna-native-operating-system-plan.md`
**Prerequisite**: Memory v1 (merged: PR #68, #70, #75)
**Goal**: Luna remembers like a person, not a database

---

## What v1 does today

- Hybrid semantic + keyword recall (pgvector cosine + name boost)
- Post-response entity/relation/memory extraction
- Session entity boosting across turns
- Memory decay (SQL-side, time-weighted)
- Dream consolidation (nightly RL pattern learning)
- Recall feedback tracking (used/unused entities)
- Scorer confidence weighting
- Claude --resume for native session continuity

## What's missing

Luna can look up facts but can't remember *experiences*. She knows "Phoebe is a desk robot" but not "we spent an hour yesterday researching desk robots and you were excited about the servo tracking." She can't anticipate what you need, detect contradictions, adapt her style, or forget noise.

---

## Module A: Episodic Memory

**Problem**: Luna extracts entities but loses the *story* of conversations. She has facts without narrative.

**What to build**:

### A.1 — Conversation episode model
- New table: `conversation_episodes`
  - `id, tenant_id, session_id, summary, key_topics (JSONB), key_entities (JSONB), mood, outcome, duration_minutes, message_count, created_at`
- After each conversation turn (or when session goes idle for 5+ min), generate a 2-3 sentence episode summary via Gemma 4
- Store with embedded vector for semantic search

### A.2 — Episode extraction activity
- New background task in `chat.py` post-response block
- Trigger: when a session has 4+ new messages since last episode
- Uses `local_inference.py` to summarize: "User and Luna discussed desk robot options. User was excited about Phoebe (RPi Zero 2W, servo tracking). Luna found GitHub repos."
- Links to entities mentioned in the episode

### A.3 — Episode recall in instructions
- In `memory_recall.py build_memory_context()`, add episode search
- Query: top 3 episodes semantically similar to current message
- Inject as "## Recent Conversations" section in CLAUDE.md:
  ```
  ## Recent Conversations
  - Yesterday: discussed desk robot options, you liked Phoebe for servo tracking
  - 2 days ago: reviewed PR #62, fixed auto-dream pipeline
  - Last week: set up WhatsApp integration for Desi Store tenant
  ```

### A.4 — Episode-entity links
- Episodes reference entities → when an entity is recalled, also show which conversations it appeared in
- "Phoebe (desk robot) — discussed in 3 conversations, most recently yesterday"

**Reuse**: `local_inference.py` for summarization, `embedding_service.py` for vectors, `memory_recall.py` for search. Migration for new table.

**Effort**: 3-4 days

---

## Module B: User Preference Learning

**Problem**: Luna responds the same way regardless of how the user prefers to communicate. She doesn't learn from feedback patterns.

**What to build**:

### B.1 — Preference profile model
- New table: `user_preferences`
  - `id, tenant_id, user_id, preference_type, value, confidence, evidence_count, updated_at`
- Preference types:
  - `response_length`: short / medium / detailed
  - `tone`: casual / professional / technical
  - `emoji_usage`: none / minimal / frequent
  - `explanation_depth`: brief / thorough
  - `format`: prose / bullet_points / structured
  - `language`: en / es / auto

### B.2 — Preference inference from feedback
- In `auto_quality_scorer.py`, after scoring, analyze patterns:
  - If user consistently thumbs-down long responses → set `response_length: short`
  - If user thumbs-up responses with bullet points → set `format: bullet_points`
  - Track in `recall_feedback` MemoryActivity: which response styles get used vs ignored
- Run as part of the nightly dream cycle (aggregate feedback patterns)

### B.3 — Preference injection
- In `cli_session_manager.py generate_cli_instructions()`, add section:
  ```
  ## User Preferences (learned from feedback)
  - Prefers short, direct responses
  - Uses casual tone
  - Likes bullet points over paragraphs
  - Rarely uses emoji
  ```
- Weight by confidence (only inject preferences with 3+ evidence points)

### B.4 — Explicit preference capture
- Luna can ask "Would you prefer shorter responses?" and store the answer
- MCP tool: `set_user_preference(type, value)` → writes to table
- Luna's skill prompt tells her to respect these preferences

**Reuse**: `rl_experience` table for feedback patterns, `auto_quality_scorer.py` for analysis, `memory_activities` for tracking, dream cycle for batch learning.

**Effort**: 2-3 days

---

## Module C: Anticipatory Context

**Problem**: Luna only recalls when asked. She should proactively inject relevant context based on time, calendar, and patterns.

**What to build**:

### C.1 — Time-aware context injection
- In `memory_recall.py`, before semantic search, check:
  - Time of day → morning (inject daily agenda), afternoon (inject pending tasks), evening (inject summary)
  - Day of week → Monday (weekend recap), Friday (week summary)
- Add to memory_context as `## Today's Context`

### C.2 — Calendar-aware injection
- Query upcoming calendar events (next 4 hours) from `channel_events` or Google Calendar API
- If user has a meeting in 30 min, inject: "You have a meeting with [person] at [time] about [topic]"
- Connect to knowledge graph: if meeting attendee is a known entity, pull their observations

### C.3 — Pattern-based anticipation
- Track recurring queries: "Every Monday morning the user asks about sales pipeline"
- Store as `anticipation_rules` in agent_memories with type `pattern`
- Dream cycle identifies recurring patterns from RL experiences
- Pre-load anticipated context before user asks

### C.4 — Proactive WhatsApp nudges
- Morning: "Good morning! You have 3 meetings today. Your deploy from yesterday is running fine."
- Pre-meeting: "Your meeting with [person] starts in 15 min. Last time you discussed [topic]."
- Uses `proactive_actions` table (already exists) + WhatsApp send

**Reuse**: `InboxMonitorWorkflow` (already fetches calendar), `proactive_actions` model (exists), `whatsapp_service.send_message()`, `channel_events` table.

**Effort**: 3-4 days

---

## Module D: Contradiction Detection

**Problem**: Luna stores conflicting facts without noticing. "Phoebe is a desk robot" and "Phoebe is a contact" can coexist.

**What to build**:

### D.1 — Assertion conflict check
- In `knowledge_extraction.py`, before persisting a new entity or observation:
  - Search for existing entities with same name
  - If entity_type differs (person vs product), flag as potential conflict
  - If observation contradicts existing observation on same attribute, create a `world_state_assertion` with status `disputed`

### D.2 — Conflict resolution prompt
- When Luna encounters a conflicting memory during recall:
  - Inject into instructions: "Note: conflicting info about [entity] — you said X on [date], but Y on [date]. Verify with the user."
  - Luna asks: "I have two different records for Phoebe — a desk robot project and a contact. Which one did you mean?"

### D.3 — Supersession tracking
- When user clarifies, mark old assertion as `superseded_by` (field exists on `world_state_assertions`)
- Log as `MemoryActivity` event_type `contradiction_resolved`

**Reuse**: `world_state_assertions` table (exists, has `dispute_reason`, `superseded_by_id`), `knowledge_extraction.py` (extend), `memory_recall.py` (extend).

**Effort**: 2 days

---

## Module E: Source Attribution

**Problem**: Luna knows facts but not WHERE she learned them. "Who told me about Phoebe?" has no answer.

**What to build**:

### E.1 — Source tracking on extraction
- In `knowledge_extraction.py extract_from_content()`, already accepts `content_type` param
- Extend entity/observation creation to store `source_channel` and `source_date`:
  - `content_type=chat_transcript` → source=`chat`, channel=`web` or `whatsapp`
  - `content_type=email` → source=`gmail`
  - `content_type=calendar` → source=`calendar`
- Add `source_channel` column to `knowledge_observations`

### E.2 — Source display in recall
- In `memory_recall.py`, when fetching observations per entity, include source info
- In `cli_session_manager.py`, inject source with each observation:
  ```
  - **Phoebe** (product):
    - Open source desk robot with RPi Zero 2W (from WhatsApp, Mar 27)
    - Has servo head tracking and STL files (from WhatsApp, Mar 27)
  ```

### E.3 — "How do I know this?" query support
- Luna can answer "Where did I learn about X?" by querying observations + source
- MCP tool: `get_entity_sources(entity_name)` → returns list of sources with dates

**Reuse**: `knowledge_observations` table (add column), `knowledge_extraction.py` (extend), `memory_recall.py` (extend), migration for new column.

**Effort**: 1-2 days

---

## Module F: Emotional Memory

**Problem**: Luna doesn't remember how the user *felt* about topics. She can't distinguish "something you were excited about" from "something that frustrated you."

**What to build**:

### F.1 — Sentiment tagging on extraction
- In `knowledge_extraction.py`, extend the LLM prompt to also extract sentiment:
  - "User was excited about Phoebe" → tag observation with `sentiment: positive`
  - "User was frustrated about deploy failures" → tag with `sentiment: negative`
- Add `sentiment` column to `knowledge_observations` (enum: positive, negative, neutral, excited, frustrated, curious)

### F.2 — Sentiment-aware recall
- In `memory_recall.py`, boost entities with strong sentiment (positive or negative)
- Entities the user feels strongly about should be recalled more often
- Add sentiment to observation display: "Phoebe (product) — you were excited about this"

### F.3 — Emotional continuity
- If user was frustrated about X last time, Luna should acknowledge it:
  "Last time we discussed deploys you were frustrated about the disk space issues. Let me check if that's resolved."
- Inject as part of episode recall (Module A)

### F.4 — Sentiment trends
- Track sentiment over time per entity
- Dream cycle analyzes: "User sentiment about X is declining" → flag for attention
- Morning report: "Topics trending negative: deploy reliability, Docker disk space"

**Reuse**: `knowledge_observations` (add column), `knowledge_extraction.py` (extend prompt), `memory_recall.py` (boost logic), dream cycle (trend analysis).

**Effort**: 2-3 days

---

## Module G: Active Forgetting

**Problem**: Knowledge graph accumulates noise. 338 entities with zero observations. Recall feedback shows many entities are never used.

**What to build**:

### G.1 — Entity health scoring
- Nightly job (dream cycle activity): score each entity on:
  - `recall_count` — how often recalled
  - `recall_feedback` — how often used when recalled
  - `observation_count` — how many facts attached
  - `age` — days since creation
  - `last_recalled_at` — recency
- Score formula: `health = (recall_used_ratio * 0.4) + (observation_count > 0) * 0.3 + recency_factor * 0.3`

### G.2 — Automatic archival
- Entities with health < 0.1 and age > 30 days → archive (soft delete)
- Observations with no entity reference → archive
- Agent memories with 0 access_count and age > 60 days → archive
- Log as `MemoryActivity` event_type `entity_archived_stale`

### G.3 — Duplicate entity merge
- Find entities with similar names (cosine distance < 0.1 on embeddings)
- Present merge candidates in morning report
- Luna can auto-merge obvious duplicates (e.g. "John Smith" and "john smith")
- MCP tool: `merge_entities(source_id, target_id)` — already exists

### G.4 — Noise detection
- Flag entities that are too generic: "project", "meeting", "task"
- Flag entities extracted from system messages (not real user content)
- Blocklist expansion based on patterns

**Reuse**: `recall_feedback` MemoryActivity data (already collecting), dream cycle (nightly processing), `knowledge_entity` fields (recall_count, last_recalled_at exist), `embedding_service` (similarity search for duplicates).

**Effort**: 2-3 days

---

## Implementation Order

| Phase | Modules | Duration | Why first |
|-------|---------|----------|-----------|
| **Phase 1** | E (Source Attribution) + D (Contradictions) | 3-4 days | Lowest effort, highest data quality impact |
| **Phase 2** | A (Episodic Memory) | 3-4 days | Biggest UX improvement — Luna remembers stories |
| **Phase 3** | G (Active Forgetting) + B (Preferences) | 4-5 days | Clean up noise, personalize responses |
| **Phase 4** | F (Emotional Memory) + C (Anticipatory) | 5-6 days | Advanced — Luna becomes proactive and empathetic |

**Total**: ~16-19 days across 7 modules, 24 sub-tasks

---

## Architecture Principle

All modules follow the same pattern:
1. **Extract** — derive signal from existing data (extraction, scoring, feedback)
2. **Store** — persist in existing tables (observations, memories, episodes) + minimal new columns
3. **Recall** — surface during `build_memory_context()` → inject into CLAUDE.md
4. **Learn** — dream cycle consolidates patterns nightly

No new microservices. No new databases. Everything extends the existing knowledge graph + memory recall pipeline.
