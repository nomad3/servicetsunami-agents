# Memory-First Agent Platform — Design Document

**Status:** Draft
**Author:** Simon Aguilera (via brainstorming session with Claude)
**Date:** 2026-04-07
**Scope:** Core platform redesign around memory and workflow orchestration as the two product pillars.

---

## 1. Vision

agentprovision.com evolves into a **memory-first, Kubernetes-native, multi-agent, multi-source agentic orchestration platform** where memory and workflow orchestration are the two product pillars. Every other subsystem — chat, MCP tools, CLI runtimes, WhatsApp, web UI, integrations — is an interface layered on those two pillars.

The platform is optimized for:

1. **Low-latency conversational interfaces** (<2s fast path for ~70% of turns)
2. **Enterprise on-prem adoption** — each customer (Integral, Levi's, HealthPets, future tenants) runs the platform inside their own K8s cluster with data sovereignty
3. **Multi-agent specialization** — many agents (Luna, Sales, Data Analyst, Code, SRE, Support, Marketing Analyst, Vet Cardiac, etc.) sharing a coherent memory substrate with clean access-control boundaries
4. **Multi-source ingestion** — chat, email, calendar, Jira, GitHub, ads platforms, scraped web data, voice notes, uploaded documents, Databricks, devices, MCP servers, inbox monitor — all feeding one canonical memory layer
5. **Rust where it earns its place** — embedding service from day one, memory-core extraction in Phase 2, federation daemon in Phase 4

The ultimate product story: **"Enterprise agentic orchestration with a memory-first architecture, Rust core, deployed on Kubernetes."**

---

## 2. Goals and Non-Goals

### Goals

1. **Luna thinks like a human.** Memory is always on and pre-loaded into the model's context before it sees the user's turn. No explicit "recall tool" that Luna has to remember to call. Relevant entities, past conversations, commitments, goals, world state, and episodes surface automatically.

2. **Conversational latency that works.** Fast path under 2 seconds end-to-end for ~70% of turns (greetings, simple Q&A, quick recalls, acknowledgments). Slow path 5–15 seconds for the 30% of turns that need actual tool orchestration.

3. **Enterprise-grade deployment.** K8s-native from day one. Each customer runs their own cluster. Data stays in-cluster. Helm charts ship the whole platform. Installation is one `helm install` + OAuth configuration.

4. **Multi-agent memory with proper scoping.** Every agent sees tenant-wide memory plus its own scoped memory. Safety/trust policies govern cross-agent access. No cross-tenant leakage under any circumstances.

5. **Multi-source ingestion with attribution.** Every memory record knows its source (chat, email, calendar, etc.), source ID (for deduplication), ingestion time, and confidence. Disputes between sources are reconciled and surfaced transparently.

6. **Auditability and durability.** All non-trivial memory mutations run as Temporal workflows. You can replay, retry, and audit every write operation.

### Non-Goals (in this design)

- **No full event-sourcing rewrite.** Existing tables stay; we add embedding columns and one new `memory_events` table where needed.
- **No rewrite of existing Temporal workflows.** Business workflows (`DynamicWorkflowExecutor`, `CodeTaskWorkflow`, `DealPipelineWorkflow`, etc.) continue unchanged. They gain memory access via a new gRPC API.
- **No MemGPT-style hierarchical memory paging.** Our three-layer model (working / episodic / semantic) is sufficient.
- **No decentralized marketplace in Phases 1–3.** Phase 4 introduces cluster federation. Marketplace economics (creator/operator revenue splits) are a separate future spec.
- **No Rust rewrite of the whole stack.** Only memory-core, embedding-service, and (later) the federation daemon are Rust. Python stays for API, workflows, and business logic.
- **No dependency on Claude Code CLI's `--resume` flag.** Session continuity is the platform's responsibility via chat-runtime pods and the memory layer. We intentionally killed `--resume` and will not re-enable it.

---

## 3. Architecture Overview

### 3.1. The three pillars

1. **Memory layer** — canonical store of everything the platform knows. Exposes a read API (`recall`) and small sync write API (`record_*`). Large or expensive writes happen via memory workflows. Rust core in Phase 2; Python in Phase 1.

2. **Workflow orchestration (Temporal)** — the execution substrate for all durable, async, retriable operations. Three categories of workflow:
   - **Ingestion workflows** — pull external data into memory (per source)
   - **Memory workflows** — process memory (extraction, summarization, reconciliation, consolidation)
   - **Business workflows** — user-defined dynamic workflows that read memory and orchestrate actions (existing subsystem, unchanged)

3. **Runtime layer** — the agents themselves. `chat-runtime` pods run warm Claude CLI processes. `code-worker` runs coding tasks. Future runtimes (local Gemma4, OpenCode, other CLIs) plug into the same pattern.

### 3.2. High-level component diagram

```
                            ┌─── K8s Cluster (per tenant) ───────┐
┌──────────────┐            │                                     │
│  External    │            │  ┌──────────┐   ┌─────────────┐     │
│  Sources     │            │  │          │   │             │     │
│  • chat      │────────────┼─►│ api      │──►│ chat-runtime│     │
│  • email     │            │  │ (Python) │   │ Deployment  │     │
│  • calendar  │            │  │          │   │ (warm Claude│     │
│  • jira      │            │  └────┬─────┘   │  CLI pool)  │     │
│  • github    │            │       │ gRPC    └─────────────┘     │
│  • ads       │            │       │              ▲              │
│  • scraper   │            │       ▼              │              │
│  • upload    │            │  ┌──────────┐        │              │
│  • voice     │            │  │ memory-  │────────┘              │
│  • sql       │            │  │ core     │  gRPC                 │
│  • devices   │            │  │ (Rust P2)│                       │
│  • inbox     │            │  └────┬─────┘                       │
│  • mcp       │            │       │                             │
│    servers   │            │       │ SQL                         │
└──────────────┘            │       ▼                             │
                            │  ┌──────────┐   ┌─────────────────┐ │
                            │  │ postgres │   │ temporal        │ │
                            │  │+pgvector │   │ workers:        │ │
                            │  └──────────┘   │ • ingestion     │ │
                            │                 │ • memory        │ │
                            │                 │ • business      │ │
                            │                 │ • code          │ │
                            │                 └─────────────────┘ │
                            │                                     │
                            │  ┌──────────┐   ┌─────────────────┐ │
                            │  │embedding-│   │ ollama          │ │
                            │  │ service  │   │ (gemma4, nomic) │ │
                            │  │ (Rust P1)│   │ GPU node / host │ │
                            │  └──────────┘   └─────────────────┘ │
                            │                                     │
                            │  ┌──────────┐                       │
                            │  │cloudflared│ (tunnel for external)│
                            │  └──────────┘                       │
                            └─────────────────────────────────────┘
                                        │
                                        │ (Phase 4: federation)
                                        ▼
                              [ Rust node daemon ]
                             cluster-to-cluster mesh
                             optional coordinator
```

### 3.3. Component responsibilities

**api (Python, existing, refactored)**
- FastAPI HTTP layer
- Auth, tenant management, OAuth flows, chat endpoints
- Pre-loads memory context on every chat request (via gRPC to memory-core)
- Dispatches ChatCliWorkflow to Temporal with session affinity
- Saves chat messages to DB and triggers PostChatMemoryWorkflow (async)
- No direct DB access for memory operations — always goes through memory-core

**memory-core (Rust Phase 2, Python Phase 1)**
- Exposes gRPC API: `Recall`, `Record`, `Embed`, `EmbedBatch`, `Rank`, `Reconcile`
- Owns embedding inference (via embedding-service in Phase 1, in-process in Phase 2)
- Owns vector search (pgvector queries)
- Owns ranking and scoping logic
- Owns entity resolution, deduplication, merge logic
- Owns world state reconciliation
- Postgres is the canonical store — memory-core is stateless
- Horizontally scalable

**embedding-service (Rust, Phase 1)**
- Thin Rust service shipping from Phase 1
- gRPC API: `Embed(text) → vector`, `EmbedBatch(texts) → vectors`, `Health()`
- Model: `nomic-embed-text-v1.5` via `candle` (or `ort` — decide in Phase 1 kickoff benchmark)
- 2–5x faster than Python sentence-transformers
- First Rust service in production, template for memory-core extraction

**chat-runtime (new K8s Deployment, Phase 3)**
- Pods running warm Claude CLI subprocesses (one process per pod)
- Subscribes to `servicetsunami-chat` Temporal task queue
- Session affinity: same `session_id` routes to same pod via Temporal session API
- HPA scales on queue depth and CPU
- Replaces today's per-message cold-start subprocess pattern
- Tenant isolation: per-call OAuth token, `--no-session-persistence`, no disk writes

**ingestion-worker (new Temporal worker)**
- Runs source ingestion workflows:
  - `ChatIngestionWorkflow` (per turn)
  - `EmailIngestionWorkflow` (triggered by inbox monitor, batch per sync)
  - `CalendarIngestionWorkflow`
  - `JiraIngestionWorkflow`
  - `GitHubIngestionWorkflow`
  - `AdsIngestionWorkflow` (Meta, Google, TikTok)
  - `ScraperIngestionWorkflow` (competitor monitor)
  - `UploadIngestionWorkflow`
  - `VoiceIngestionWorkflow`
  - `SqlIngestionWorkflow`
  - `DeviceIngestionWorkflow`
- Each workflow calls a source adapter, converts raw events into `MemoryEvent`s, writes via memory-core

**memory-worker (new Temporal worker)**
- Runs memory processing workflows:
  - `PostChatMemoryWorkflow` — after every chat turn
  - `EpisodeWorkflow` — rolling conversation summaries
  - `NightlyConsolidationWorkflow` — cron, per tenant
  - `EntityMergeWorkflow` — on-demand
  - `WorldStateReconciliationWorkflow` — on-demand

**business-worker (existing Temporal worker)**
- Runs user-defined dynamic workflows (`DynamicWorkflowExecutor`)
- Runs legacy static workflows (DealPipelineWorkflow, etc.)
- Reads memory via memory-core gRPC (no DB access)
- No changes to business logic

**code-worker (existing Temporal worker)**
- Runs `CodeTaskWorkflow` for long coding tasks
- Runs `ChatCliWorkflow` until Phase 3 migration to chat-runtime
- `ProviderReviewWorkflow` stays here

### 3.4. Decommissioning map for existing files

Explicit fate of every file in the current memory/chat path. Prevents ambiguity during implementation.

| File | Current role | Phase 1 action |
|---|---|---|
| `apps/api/app/services/chat.py` | Chat HTTP handler, history building, session memory | **Refactor**: becomes thin HTTP layer that calls `memory.recall()` and dispatches workflow. History building moves to memory package. |
| `apps/api/app/services/cli_session_manager.py` | Builds CLAUDE.md, dispatches ChatCliWorkflow | **Refactor**: `generate_cli_instructions()` stays, but memory context injection moves to calling `memory.recall()` instead of assembling from multiple services. Hardcoded brain-gap blocks stay removed. |
| `apps/api/app/services/enhanced_chat.py` | Legacy enhanced chat service (mostly unused) | **Delete** in Phase 1 if confirmed unused; otherwise delete in Phase 2. |
| `apps/api/app/services/context_manager.py` | Conversation summarization, token counting | **Refactor**: token counting stays as utility; conversation summarization moves into `EpisodeWorkflow`. |
| `apps/api/app/services/commitment_extractor.py` | Currently a no-op stub (disabled yesterday) | **Delete** in Phase 1. Replaced by `PostChatMemoryWorkflow.detect_commitment` Gemma4 activity. |
| `apps/api/app/services/memory_recall.py` | Builds memory context blob for CLI prompt | **Delete** in Phase 1. Logic moves into `memory.recall()` in the new package. |
| `apps/api/app/services/agent_router.py` | Routes incoming messages to agent/platform | **Keep**, gains one line: it now calls `memory.recall()` before dispatching and passes the result through. |
| `apps/api/app/services/knowledge.py` | CRUD over knowledge graph | **Keep as thin wrapper**; `memory.record_*` delegates to it in Phase 1. In Phase 2 the memory-core Rust service replaces it. |
| `apps/api/app/services/commitment_service.py` | CRUD over commitments | **Keep**; memory-core wraps it. |
| `apps/api/app/services/goal_service.py` | CRUD over goals | **Keep**; memory-core wraps it. |
| `apps/api/app/services/session_journals.py` | Half-wired episode synthesis | **Refactor into** the new `EpisodeWorkflow`; delete the direct-call wiring in `cli_session_manager.py`. |
| `apps/api/app/services/behavioral_signals.py` | Half-wired, disabled | **Refactor**: writer moves into `PostChatMemoryWorkflow.update_behavioral_signals` activity; reader stays as-is, called from `memory.recall()`. |

Everything else in `apps/api/app/services/` stays untouched in Phase 1.

---

## 4. Data Model

### 4.1. Existing tables that stay

All existing memory tables (from the audit) stay with additive changes only. No destructive migrations.

- `knowledge_entities` — add `embedding vector(768)` if not already present
- `knowledge_observations` — already has embedding
- `knowledge_relations` — no change
- `chat_sessions` — no change
- `chat_messages` — **add `embedding vector(768)` column** (new)
- `commitment_records` — **add `embedding vector(768)` column** (new)
- `goal_records` — **add `embedding vector(768)` column** (new)
- `behavioral_signals` — already has embedding
- `world_state_assertions` — no change
- `world_state_snapshots` — no change
- `agent_memories` — already has `content_embedding`
- `memory_activities` — no change
- `plans`, `plan_steps`, `plan_assumptions` — no change
- `embeddings` — no change (generic embedding table)

### 4.2. New tables

**`session_journals`** — rolling conversation episode summaries.
```sql
CREATE TABLE session_journals (
    id UUID PRIMARY KEY,
    tenant_id UUID NOT NULL REFERENCES tenants(id),
    chat_session_id UUID NOT NULL REFERENCES chat_sessions(id),
    agent_slug VARCHAR(100),             -- participating agent
    window_start TIMESTAMPTZ NOT NULL,   -- first message in the episode
    window_end TIMESTAMPTZ NOT NULL,     -- last message in the episode
    message_count INTEGER NOT NULL,
    summary TEXT NOT NULL,               -- Gemma4-generated narrative summary
    key_entities TEXT[],                 -- extracted entity names
    topics TEXT[],                       -- inferred topics
    sentiment VARCHAR(20),               -- positive / neutral / concerned / escalated
    embedding VECTOR(768),
    generated_by VARCHAR(50) NOT NULL,   -- "gemma4" | "sonnet" | etc.
    trigger_reason VARCHAR(30) NOT NULL, -- "window_full" | "idle_timeout" | "end_of_day" | "manual"
    created_at TIMESTAMPTZ DEFAULT NOW(),
    CONSTRAINT uk_session_window UNIQUE (chat_session_id, window_start, window_end)
);
CREATE INDEX idx_session_journals_tenant_time ON session_journals(tenant_id, window_end DESC);
CREATE INDEX idx_session_journals_embedding ON session_journals USING ivfflat (embedding vector_cosine_ops);
```

**`memory_events`** — append-only log of memory mutations (for audit + future event-sourcing).
```sql
CREATE TABLE memory_events (
    id UUID PRIMARY KEY,
    tenant_id UUID NOT NULL REFERENCES tenants(id),
    event_type VARCHAR(50) NOT NULL,     -- "entity_created" | "commitment_created" | ...
    source_type VARCHAR(50) NOT NULL,    -- "chat" | "email" | "calendar" | "jira" | ...
    source_id VARCHAR(200),              -- raw source's native ID
    actor_slug VARCHAR(100),             -- agent or system that created the memory
    target_table VARCHAR(50) NOT NULL,   -- which memory table was affected
    target_id UUID NOT NULL,             -- PK of the affected record
    payload JSONB,                       -- minimal event payload
    visibility VARCHAR(20) DEFAULT 'tenant_wide',
    workflow_id VARCHAR(200),            -- Temporal workflow id (for replay/audit)
    workflow_run_id VARCHAR(200),
    confidence REAL DEFAULT 1.0,
    created_at TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX idx_memory_events_tenant_time ON memory_events(tenant_id, created_at DESC);
CREATE INDEX idx_memory_events_source ON memory_events(tenant_id, source_type, source_id);
CREATE INDEX idx_memory_events_target ON memory_events(target_table, target_id);
```

This is NOT a full event-sourcing rewrite — the canonical state still lives in the typed tables. `memory_events` is an audit log. Phase 2 or later can add projections from the log if we want event-sourcing properly.

### 4.3. Existing columns that become first-class

Add to tables that currently store visibility implicitly:

- `knowledge_entities.visibility VARCHAR(20) DEFAULT 'tenant_wide'` (values: `tenant_wide`, `agent_scoped`, `agent_group`)
- `knowledge_entities.visible_to TEXT[]` (agent slugs, when `visibility = 'agent_group'`)
- Same pair added to: `commitment_records`, `goal_records`, `behavioral_signals`, `agent_memories`

`owner_agent_slug` already exists on most of these.

### 4.4. Backfill strategy for existing data

Current production state (as of 2026-04-07, tenant `0f134606`):
- 1,239+ chat messages in `chat_messages`
- 331+ entities in `knowledge_entities`
- 4,817+ observations in `knowledge_observations`
- 2,625+ RL experiences (not memory, but relevant for historical context)

None of the chat messages, commitments, or goals currently have embeddings. Without a backfill, the "recall past conversations" promise is broken for everything older than the Phase 1 rollout date.

**Backfill plan** (Phase 1 deliverable):

- `BackfillEmbeddingsWorkflow` (Temporal, per tenant, resumable)
  - Batches of 50 rows, throttled to respect embedding-service rate limits
  - Target: backfill 1,239 chat messages in ~2 minutes, 331 entities + 4,817 observations in ~5 minutes
  - Idempotent: skip rows where `embedding IS NOT NULL`
  - Progress reported via workflow heartbeat
- Entry point: `POST /internal/memory/backfill` (admin-only) or CLI command
- Runs automatically on first startup after Phase 1 deployment for each existing tenant

For really large historical datasets (100k+ chat messages), the workflow continues-as-new every 10k rows and emits progress events.

### 4.5. MemoryEvent (the canonical ingestion shape)

```python
@dataclass
class MemoryEvent:
    tenant_id: UUID
    source_type: Literal["chat", "email", "calendar", "jira", "github",
                          "ads", "scraper", "upload", "voice", "sql",
                          "device", "mcp", "inbox_monitor"]
    source_id: str                # raw source's native ID
    source_metadata: dict         # arbitrary source-specific metadata
    actor_slug: str | None        # agent or user that created the source data
    occurred_at: datetime         # when the event happened in the source
    ingested_at: datetime         # when we processed it
    kind: Literal["text", "structured", "media"]
    text: str | None              # for text-based events
    structured: dict | None       # for structured events
    media_ref: str | None         # for media (image, audio, file)
    proposed_entities: list[dict] # pre-extracted hints from the adapter
    proposed_observations: list[dict]
    proposed_relations: list[dict]
    proposed_commitments: list[dict]
    confidence: float             # adapter's initial assessment
    visibility: str               # default tenant_wide
```

Every source adapter takes raw source data and emits a list of `MemoryEvent` objects. The downstream write pipeline turns those into typed rows across the memory tables and creates `memory_events` log entries.

---

## 5. gRPC APIs

### 5.1. embedding-service (Rust, Phase 1)

```protobuf
syntax = "proto3";
package embedding.v1;

service EmbeddingService {
  rpc Embed(EmbedRequest) returns (EmbedResponse);
  rpc EmbedBatch(EmbedBatchRequest) returns (EmbedBatchResponse);
  rpc Health(google.protobuf.Empty) returns (HealthResponse);
}

message EmbedRequest {
  string text = 1;
  string task_type = 2;          // "search_query" | "search_document" | "classification"
}
message EmbedResponse {
  repeated float vector = 1;     // 768 floats for nomic-embed-text-v1.5
  string model = 2;
  int32 dimensions = 3;
}

message EmbedBatchRequest {
  repeated string texts = 1;
  string task_type = 2;
}
message EmbedBatchResponse {
  repeated EmbedResponse results = 1;
}

message HealthResponse {
  string status = 1;             // "ok" | "degraded" | "loading"
  string model = 2;
  int64 uptime_seconds = 3;
}
```

Ships in Phase 1 as a standalone Rust container. Deployed as a K8s Deployment with 2 replicas behind a ClusterIP service. The Python memory package calls it for all embedding operations.

### 5.2. memory-core (Rust Phase 2, Python Phase 1 with same API)

```protobuf
syntax = "proto3";
package memory.v1;

service MemoryCore {
  // Read
  rpc Recall(RecallRequest) returns (RecallResponse);
  rpc SearchConversations(SearchConversationsRequest) returns (SearchConversationsResponse);
  rpc GetCommitments(GetCommitmentsRequest) returns (GetCommitmentsResponse);
  rpc GetGoals(GetGoalsRequest) returns (GetGoalsResponse);
  rpc GetEntity(GetEntityRequest) returns (EntityResponse);

  // Write (sync)
  rpc RecordObservation(RecordObservationRequest) returns (ObservationResponse);
  rpc UpdateConfidence(UpdateConfidenceRequest) returns (google.protobuf.Empty);
  rpc RecordBehavioralSignal(RecordBehavioralSignalRequest) returns (BehavioralSignalResponse);

  // Write (ingestion — bulk)
  rpc IngestEvents(IngestEventsRequest) returns (IngestEventsResponse);

  // Admin
  rpc Health(google.protobuf.Empty) returns (HealthResponse);
}

message RecallRequest {
  string tenant_id = 1;
  string agent_slug = 2;           // applies visibility filter
  string query = 3;                // natural language query
  string chat_session_id = 4;      // for session-aware recall
  int32 top_k_per_type = 5;        // default 5
  int32 total_token_budget = 6;    // default 8000
  repeated string source_filter = 7; // optional: restrict to these sources
}

message RecallResponse {
  repeated EntitySummary entities = 1;
  repeated ObservationSummary observations = 2;
  repeated RelationSummary relations = 3;
  repeated CommitmentSummary commitments = 4;
  repeated GoalSummary goals = 5;
  repeated ConversationSummary past_conversations = 6;
  repeated EpisodeSummary episodes = 7;
  repeated WorldStateAssertion contradictions = 8;
  int32 total_tokens_estimate = 9;
  RecallMetadata metadata = 10;    // timing, scores, etc.
}

message IngestEventsRequest {
  string tenant_id = 1;
  repeated MemoryEvent events = 2;
  string workflow_id = 3;          // Temporal workflow id for audit trail
}
```

(Full IDL is extensive — abbreviated here. The implementation plan will include the complete `.proto` files.)

### 5.3. Backwards compatibility in Phase 1

In Phase 1, the Python `apps/api/app/memory/` package exposes the SAME API as the eventual Rust service, but implemented as Python function calls (not gRPC). This means:

- Python callers (api, temporal workers) call `memory.recall(...)` as a normal Python function
- In Phase 2, the Python package becomes a thin gRPC client that proxies to the Rust memory-core
- Business logic on top of memory doesn't know or care which backend is serving

This is the critical design constraint that makes Phase 2 a rewrite (not a re-architecture). Every Phase 1 Python function that will move to Rust is designed around the gRPC contract now.

---

## 6. Data Flow

### 6.1. Hot path: receiving a chat message

```
1. User message arrives at api (HTTP POST /chat/sessions/{id}/messages)
2. api authenticates, loads session, rolls back any poisoned DB state
3. api calls embedding-service.Embed(message) via gRPC       [~20ms]
4. api calls memory.recall(tenant_id, agent_slug, message_embedding)
   ├── Python (Phase 1) or Rust gRPC (Phase 2)
   ├── pgvector queries in parallel: entities, observations,
   │   past_conversations, episodes, commitments, goals, world_state
   ├── Ranked by weighted score (semantic 0.55, recency 0.20,
   │   confidence 0.15, source_priority 0.10)
   ├── Filtered by agent visibility
   ├── Bounded by total token budget (default 8K)
   └── Returns MemoryContext object                          [~80-150ms]
5. api builds CLAUDE.md with pre-loaded memory context
   (no tool call needed — it's all already there)
6. api dispatches ChatCliWorkflow to Temporal with session affinity
7. Temporal routes to a chat-runtime pod (same session → same pod)
   ├── If the pod has a warm Claude CLI process, message streams to it
   └── If not, pod spins one up (cold path, ~5s)
8. Claude responds (Haiku for fast path, Sonnet for slow path)
9. Response streams back to api
10. api saves user message + assistant message to DB
11. api calls embedding-service.EmbedBatch([user_msg, assistant_msg])
    └── Writes embedding to chat_messages.embedding so they're
        recallable on the NEXT turn                          [~40ms]
12. api dispatches PostChatMemoryWorkflow (async, fire-and-forget)
13. api returns HTTP 201 to user
```

Target latencies:
- **Fast path** (greetings, simple Q&A, Haiku, no tool calls): 1.5–2.5s
- **Slow path** (Sonnet, 1–3 tool calls, analytical turns): 5–15s
- **Cold chat-runtime pod** (first turn after scale-up): add ~3–5s one-time

### 6.2. Write path: PostChatMemoryWorkflow

Fires async after every turn. Runs on `memory-worker` Temporal worker.

```
PostChatMemoryWorkflow(tenant_id, chat_session_id, user_msg_id, assistant_msg_id):

  [Activity 1: extract_knowledge]
    - Load user + assistant message content
    - Call Gemma4 (via Ollama) with extraction prompt
    - Parse structured output: entities, observations, relations
    - Call memory.IngestEvents with the extracted items
    - Update memory_events audit log

  [Activity 2: detect_commitment]
    - Call Gemma4 with commitment classification prompt
    - Structured output: { is_commitment: bool, title, due_at, type }
    - If yes: memory.record_commitment (sync API)

  [Activity 3: update_world_state]
    - For each new observation, check against existing world_state_assertions
    - If conflict: create dispute, flag for reconciliation
    - If corroboration: increment corroboration_count, update confidence
    - If novel: create new assertion

  [Activity 4: update_behavioral_signals]
    - If previous assistant message contained a suggestion:
      - Check if user's current message confirms/denies acted_on
      - Update behavioral_signal row

  [Activity 5: maybe_trigger_episode]
    - Check if chat_session has ≥30 unsummarized messages
    - If yes: dispatch EpisodeWorkflow (child workflow)
```

Each activity is independent and retriable. Failures in one don't block the others. All are bounded by a 60-second timeout.

### 6.3. Episode workflow (trigger: N=30 OR idle=10min OR end-of-day)

```
EpisodeWorkflow(tenant_id, chat_session_id, window_start, window_end, trigger_reason):

  [Activity 1: fetch_messages]
    - Load all chat_messages in [window_start, window_end]

  [Activity 2: summarize_window]
    - Call Gemma4 with summarization prompt
    - Extract: summary text, key_entities, topics, sentiment

  [Activity 3: embed_and_store]
    - Embed summary text via embedding-service
    - Insert row into session_journals

  [Activity 4: link_entities]
    - For each entity mentioned in key_entities, update that entity's
      "recent_mentions" counter
```

Triggers are dispatched by:
- **N=30**: PostChatMemoryWorkflow activity 5 checks the count and signals
- **Idle=10min**: a periodic `IdleEpisodeScanWorkflow` (per tenant, continues-as-new every hour) sweeps for sessions idle ≥10 minutes
- **End-of-day**: a nightly cron workflow wraps up any remaining open windows

**Idempotency and race handling**: multiple triggers could fire for the same session window. The workflow ID is deterministic: `episode-{chat_session_id}-{window_start_iso}`. Temporal rejects duplicate workflow IDs, so only one of concurrent triggers wins. The `UNIQUE (chat_session_id, window_start, window_end)` constraint is a backstop against any race that slips through (different window boundaries are possible if triggers see different message counts). On conflict, the `idle` trigger yields to `N=30` (N wins because it has a tighter window).

### 6.4. Nightly consolidation workflow

Runs once per tenant per night (staggered to avoid thundering herd).

```
NightlyConsolidationWorkflow(tenant_id):

  [Activity 1: merge_duplicate_entities]
    - For each entity with low recall count, find semantic neighbors
    - If similarity > 0.95 and same category, merge with higher-confidence one
    - Create memory_events audit entries

  [Activity 2: decay_old_confidences]
    - For assertions older than N days, apply exponential decay
    - Flag as "stale" if confidence drops below threshold

  [Activity 3: consolidate_weekly_theme]
    - Group last 7 days of episodes into a "weekly theme" summary
    - Store as a higher-level episode in session_journals

  [Activity 4: retrain_rl_policies]
    - Aggregate RL experiences from the last day
    - Update routing policies
    - Store updated policy snapshot
```

### 6.5. Failure modes and degradation strategy

| Failure | Detection | User-visible behavior | Recovery |
|---|---|---|---|
| **embedding-service down** | gRPC connection refused | Hot path falls back to keyword search (ILIKE on content). Degraded recall quality but no outage. | K8s liveness probe restarts pod; auto-heal. |
| **memory-core down** | gRPC timeout or connection refused | Chat degrades: system prompt gets only the 20-message window, no semantic recall. User sees "Luna is recalling less context right now" banner on the UI. | K8s restarts; memory is read-through from Postgres so no data loss. |
| **Gemma4 / Ollama down** | HTTP timeout on `/api/generate` | `PostChatMemoryWorkflow` activities (extraction, commitment classification, episode summary) retry with exponential backoff for 5 minutes, then fail and log. User-facing chat is unaffected (memory extraction is async). | Ollama native host process auto-restarts; workflow retries resume. |
| **PostChatMemoryWorkflow backed up** | Temporal queue depth > threshold | Older turns process late. Commitments/episodes may lag 5-30 minutes. | Per-tenant `max_concurrent_post_chat_workflows` cap (default 3). If cap hit, workflows queue rather than spawning unlimited. HPA on memory-worker scales up. |
| **Episode trigger collision** | Workflow ID conflict on `episode-{session_id}-{window_start}` | Loser gets a Temporal "already exists" error. No duplicate episodes created. | Idempotent workflow ID handles this automatically. |
| **Embedding service returns bad vector** (NaN, wrong dim) | Validation on receive | Skip embedding for that row, log warning, backfill later. | Retry via `BackfillEmbeddingsWorkflow`. |
| **Concurrent entity update race** | Postgres serialization failure | First writer wins, second retries with fresh read. | Use `SELECT ... FOR UPDATE` in memory-core for entity merges and reconciliation. |
| **Chat-runtime warm pod crashes mid-turn** (Phase 3+) | Temporal activity failure | Turn retries on a different pod, user sees 3-5s added latency (cold start). | HPA scales up replacement; K8s restarts dead pods. |
| **pgvector query timeout** | PostgreSQL statement_timeout | Chat falls back to keyword search for that turn; logs the slow query. | Index warmup + nightly `VACUUM ANALYZE`; consider HNSW over ivfflat if latency degrades. |
| **Memory recall returns contradictions** | `RecallResponse.contradictions` non-empty | System prompt includes "⚠ I have conflicting information about X from [source A] vs [source B]. The most recent is [winner]." Luna is instructed to mention the dispute if directly relevant. | Async `WorldStateReconciliationWorkflow` handles permanent resolution. |
| **Claude Code CLI version upgrade breaks chat-runtime** | Pod liveness probe fails / activity errors | Old pod continues serving traffic until new version is validated. | Version-pinned Docker image; upgrade via rolling Deployment update; rollback via helm. |

**Degradation ordering (hot path)**: if multiple systems are unhealthy, degrade in this order:
1. No episodes (skip `EpisodeWorkflow`) — user sees no morning briefing but chat works
2. No semantic recall (fall back to keyword) — Luna loses some recall quality
3. No memory extraction (skip `PostChatMemoryWorkflow` knowledge extraction) — graph doesn't grow
4. No memory at all (window-only, the 20 most recent messages) — Luna is "dumb" but functional
5. Full outage — 500 error

The chat API has a hard timeout of **3 seconds** on the `memory.recall` call. If recall hasn't returned, the chat proceeds with an empty memory context (degradation level 4 above) and the user still gets a response. Better to be dumb-but-fast than smart-but-hung.

---

## 7. Multi-Agent Scoping and Access Control

Every memory record has:

- `tenant_id` (hard boundary — **no cross-tenant reads under any circumstances**)
- `owner_agent_slug` (the agent that created the record, or NULL for shared writes)
- `visibility` enum: `tenant_wide` (default), `agent_scoped`, `agent_group`
- `visible_to TEXT[]` (list of agent slugs when visibility = `agent_group`)

### Recall rules

```python
def visible_records_for(agent_slug: str, records: Query):
    return records.filter(
        or_(
            records.c.visibility == "tenant_wide",
            and_(
                records.c.visibility == "agent_scoped",
                records.c.owner_agent_slug == agent_slug,
            ),
            and_(
                records.c.visibility == "agent_group",
                records.c.visible_to.contains([agent_slug]),
            ),
        )
    )
```

Applied at the memory-core query layer. Business logic above never filters — the memory API does it.

### Examples

- **Shared knowledge** (Ray Aristy, Integral's business details, Levi's SRE agenda): `tenant_wide`. All agents in the tenant see it.
- **Agent-private memory** (Luna's conversation style preferences for this user, Sales Agent's follow-up cadence): `agent_scoped`, `owner_agent_slug = "luna"` or `"sales_agent"`.
- **Team memory** (SRE + DevOps share infrastructure observations, but Customer Support does not): `agent_group`, `visible_to = ["sre_agent", "devops_agent"]`.

### Cross-agent handoff

When Luna delegates to Code Agent, the chat session stays the same. Code Agent queries memory as `agent_slug = "code_agent"` and gets tenant_wide + its own scoped memory. No special "handoff context" mechanism needed — the memory layer IS the handoff context.

---

## 8. Multi-Source Ingestion

### 8.1. Source adapter contract

```python
class SourceAdapter(Protocol):
    source_type: str   # "email" | "calendar" | ...

    async def ingest(
        self,
        raw: Any,
        source_metadata: dict,
        tenant_id: UUID,
    ) -> list[MemoryEvent]: ...

    def deduplication_key(self, raw: Any) -> str: ...
```

Each adapter lives in `apps/api/app/memory/ingestion/adapters/`. One file per source. Adapters are pure functions: raw data in, MemoryEvents out. No side effects, no DB writes. The ingestion workflow handles the write path.

### 8.2. Source priority for implementation

From the brainstorming session (option E):

1. **Chat** (Phase 1) — highest priority, fixes Luna's current problem, proves the pattern
2. **Email** (Phase 3) — highest new business value, unlocks "Luna reads your inbox"
3. **Calendar, Jira, GitHub, Ads** (Phase 3) — structured, copy-paste of the chat adapter pattern
4. **Voice, Devices, Scraper, Upload, SQL, MCP** (Phase 4+) — exotic sources, new modality complexity

### 8.3. Example: chat adapter

```python
class ChatAdapter:
    source_type = "chat"

    async def ingest(self, raw_message: ChatMessage, metadata, tenant_id):
        return [MemoryEvent(
            tenant_id=tenant_id,
            source_type="chat",
            source_id=str(raw_message.id),
            source_metadata={"session_id": str(raw_message.session_id),
                             "role": raw_message.role},
            actor_slug=raw_message.agent_slug if raw_message.role == "assistant" else None,
            occurred_at=raw_message.created_at,
            ingested_at=datetime.utcnow(),
            kind="text",
            text=raw_message.content,
            proposed_entities=[],  # populated by knowledge extraction activity
            proposed_observations=[],
            proposed_relations=[],
            proposed_commitments=[],
            confidence=1.0,
            visibility="tenant_wide",
        )]

    def deduplication_key(self, raw: ChatMessage) -> str:
        return f"chat:{raw.id}"
```

### 8.4. Source attribution and dispute reconciliation

Every memory record stores `source_type`, `source_id`, `ingested_at`, `confidence`, `superseded_by_id`.

When two sources disagree — e.g., email says "meeting at 3pm", calendar says 4pm:

1. Both observations are inserted with `status = "active"`
2. `WorldStateReconciliationWorkflow` fires
3. Workflow compares the two, uses source_priority + confidence to pick a winner
4. Winner stays `active`, loser gets `status = "disputed"` + `superseded_by_id = winner.id`
5. Both are retrievable; the disputed one is surfaced to Luna via `contradictions` field in RecallResponse

Source priority defaults (can be overridden per tenant):
```
calendar > chat > jira > github > email > scraper > voice
```

**Rationale**: calendar is the highest-fidelity source for temporal claims (user explicitly set it). Chat is the user telling Luna something directly — higher trust than inferred data from parsed emails or scraped HTML. Email and scraper have OCR/parsing errors. Voice has transcription errors.

---

## 9. Kubernetes Deployment

### 9.1. Helm chart structure

Reactivate and extend the existing helm charts:

```
helm/
├── charts/
│   ├── microservice/              # existing reusable base chart
│   └── agentprovision/            # new umbrella chart
│       ├── Chart.yaml
│       ├── values.yaml
│       ├── templates/
│       │   ├── api-deployment.yaml
│       │   ├── memory-core-deployment.yaml       # new
│       │   ├── embedding-service-deployment.yaml # new
│       │   ├── chat-runtime-deployment.yaml      # new
│       │   ├── ingestion-worker-deployment.yaml  # new
│       │   ├── memory-worker-deployment.yaml     # new
│       │   ├── business-worker-deployment.yaml   # existing
│       │   ├── code-worker-deployment.yaml       # existing
│       │   ├── temporal-statefulset.yaml         # existing
│       │   ├── postgres-statefulset.yaml         # existing
│       │   ├── cloudflared-daemonset.yaml        # existing
│       │   ├── hpa.yaml
│       │   ├── services.yaml
│       │   ├── configmaps.yaml
│       │   ├── secrets.yaml                       # externalSecrets
│       │   ├── ingress.yaml
│       │   └── networkpolicies.yaml
│       └── templates/tests/
```

### 9.2. Service layout (per tenant cluster)

| Service | Kind | Replicas (default) | Scaling | Notes |
|---|---|---|---|---|
| api | Deployment | 2 | HPA on CPU + req/s | Stateless |
| memory-core | Deployment | 2 | HPA on gRPC req/s | Stateless (P2 Rust, P1 Python-in-api) |
| embedding-service | Deployment | 2 | HPA on gRPC req/s | Rust from P1 |
| chat-runtime | Deployment | 3 | HPA on Temporal queue depth | Warm CLI pool (P3+) |
| ingestion-worker | Deployment | 2 | HPA on queue depth | Temporal worker |
| memory-worker | Deployment | 2 | HPA on queue depth | Temporal worker |
| business-worker | Deployment | 2 | HPA on queue depth | Temporal worker (existing) |
| code-worker | Deployment | 1 | HPA on queue depth | Temporal worker (existing) |
| temporal | StatefulSet | 1 | — | Existing |
| postgres + pgvector | StatefulSet | 1 | — | Canonical store |
| ollama | external (host or GPU node) | — | — | Gemma4 + nomic locally |
| cloudflared | DaemonSet | 1 | — | Tunnel |

### 9.3. Local development (kind / k3s)

- `kind create cluster --config infra/kind/kind-config.yaml` spins up a 1-control-plane + 2-worker local cluster
- `helm install agentprovision charts/agentprovision -f values/local.yaml` deploys the stack
- `make dev` wraps these into a single command for the dev loop
- The existing `local-deploy.yaml` GitHub Actions workflow deploys to the self-hosted runner

### 9.4. Tenant isolation strategies (both supported)

**Strategy A — shared cluster, namespace per tenant** (default for dev / small tenants):
- One K8s cluster runs multiple tenants
- Each tenant gets its own namespace
- Shared postgres with per-tenant schemas OR shared schema with tenant_id filter
- Shared embedding-service, memory-core (they're stateless and tenant-aware via API)
- Per-tenant chat-runtime pools for warmth guarantees

**Strategy B — cluster per tenant** (default for enterprise):
- Each enterprise customer (Integral, Levi's) gets their own K8s cluster
- Full stack deployed per cluster
- Data never leaves
- Federated later via Rust node daemon (Phase 4)

Both strategies use the same helm chart; only the values differ.

---

## 10. Phasing

### Phase 1: Python memory layer + embedding service + chat ingester (3-4 weeks)

**Goal**: fix Luna's current problem. Docker Compose still in use. No K8s yet.

Deliverables:
- `apps/api/app/memory/` package with `recall`, `record_*`, `ingest_events` APIs (Python, designed around the future gRPC contract)
- Embedding columns added to `chat_messages`, `commitment_records`, `goal_records`
- `session_journals` and `memory_events` tables created (migrations)
- Embedding-service Rust container with gRPC, dockerized, wired via gRPC
- Python memory package calls embedding-service for all embeddings
- ChatAdapter (source adapter for chat messages)
- PostChatMemoryWorkflow (Temporal, on memory-worker queue — reuses orchestration-worker for now)
- EpisodeWorkflow + IdleEpisodeScanWorkflow
- Gemma4-based commitment classification (replaces the disabled regex extractor)
- Pre-loaded memory context in the chat hot path (replaces ad-hoc context building)
- Full conversation history (untruncated, 50K budget) in CLAUDE.md — keeps yesterday's fix
- End-to-end test: Luna recalls entities, commitments, past conversations without tool calls

### Phase 2: Rust memory-core extraction (4-6 weeks)

**Goal**: migrate memory computation to Rust. Python memory package becomes a gRPC client.

Deliverables:
- `memory-core/` Rust crate with `candle` embedding, pgvector queries, ranking, ingestion, reconciliation
- gRPC IDL finalized (from the Phase 1 Python API)
- Port embedding path first (biggest perf win)
- Port ranking next (hot path)
- Port ingestion adapters for chat (Phase 1 adapter re-implemented in Rust)
- Python memory package rewritten as thin gRPC client
- Benchmark: 2-5x faster hot path, lower memory footprint
- Memory-core Deployment added to helm charts (even though still Docker Compose)
- NightlyConsolidationWorkflow
- EntityMergeWorkflow, WorldStateReconciliationWorkflow

### Phase 3a: K8s migration + chat-runtime (4-5 weeks)

**Goal**: move from Docker Compose to Kubernetes and land warm Claude CLI pods.

Deliverables:
- Helm charts reactivated and adapted:
  - Remove GCP-specific bits (ManagedCertificates, GKE ingress, GCP IAM workload identity)
  - Add kind/k3s-friendly defaults (NodePort or Traefik ingress)
  - Externalize secrets via ExternalSecrets or sealed-secrets
  - Add new Deployments: memory-core, embedding-service, chat-runtime, ingestion-worker, memory-worker
- Local dev on kind with `make dev` one-command setup
- All services running in K8s locally (laptop) and on self-hosted runner
- chat-runtime Deployment with warm Claude CLI pools
- Session affinity: same `chat_session_id` → same chat-runtime pod
  - Prototype session affinity first (unknown-unknown risk — may need Redis-backed session-to-pod mapping if Temporal session API doesn't fit)
  - Fallback plan: sticky hash on `session_id` via a custom workflow routing rule
- `BackfillEmbeddingsWorkflow` migrated to K8s memory-worker
- GitOps deployment via existing `local-deploy.yaml` workflow
- Documentation: "How to deploy agentprovision on your own K8s cluster"
- Migration runbook: export from Docker Compose postgres, import to K8s postgres, DNS cutover

**Prerequisites** (must be true before Phase 3a starts):
- Phase 1 stable in production for ≥1 week
- Phase 2 Rust memory-core passing dual-write validation
- `candle` vs `ort` benchmark resolved (blocks embedding-service image)

### Phase 3b: Additional source ingesters (3-4 weeks)

**Goal**: unlock "Luna reads your email / calendar / Jira / GitHub / ads" capabilities.

Deliverables:
- Email ingester (adapter + `EmailIngestionWorkflow`) — triggered by existing inbox monitor
- Calendar ingester (`CalendarIngestionWorkflow`) — triggered on sync or webhook
- Jira ingester (`JiraIngestionWorkflow`) — scheduled poll or webhook
- GitHub ingester (`GitHubIngestionWorkflow`) — existing integration, new ingestion path
- Ads ingesters (Meta, Google, TikTok) via `AdsIngestionWorkflow`
- Each adapter initially in Python; high-volume ones (email, calendar) ported to Rust once stable
- End-to-end test: email arrives → entity created in KG → Luna recalls it in chat 30 seconds later
- Installation guide for Integral and Levi's first customer deployments

### Phase 4: Federation + advanced sources (parallel / later)

- Rust federation daemon (cluster-to-cluster mesh from the AgentOps conversation)
- Optional coordinator for cross-cluster discovery
- Voice ingester (audio transcription → MemoryEvents)
- Device ingester (IoT sensor data)
- Scraper ingester (web scraping → entity updates)
- Upload ingester (PDF, docx, images → text + embeddings)
- SQL ingester (scheduled Databricks/data warehouse sync)
- Marketplace mechanics (separate spec)

### Phasing summary

| Phase | Duration | Infra | Deliverable |
|---|---|---|---|
| 1 | 3-4 weeks | Docker Compose | Python memory layer, Rust embedding-service, chat ingester, fix Luna's current problems |
| 2 | 4-6 weeks | Docker Compose | Rust memory-core extraction, dual-write validation, flip to Rust-primary |
| 3a | 4-5 weeks | **K8s** (first use) | Helm charts, kind/k3s local, chat-runtime warm pods, session affinity |
| 3b | 3-4 weeks | K8s | Email, calendar, Jira, GitHub, ads ingesters + customer onboarding |
| 4 | parallel | K8s | Rust federation daemon, exotic sources, marketplace |

**Total to "customer-ready enterprise platform": 14-19 weeks.** Start date 2026-04-08, Phase 3b done between late July and early August 2026.

---

## 11. Success Criteria

**Technical**

- Fast path latency: p50 < 2s, p95 < 4s for conversational turns
- Slow path latency: p50 < 10s, p95 < 30s for tool-orchestration turns
- Memory recall accuracy: Luna correctly answers "who is X" / "what did we discuss" / "what are my open commitments" without tool calls, for X/topics discussed up to 30 days ago
- Zero cross-tenant data leaks in automated integration tests
- Zero `InFailedSqlTransaction` errors in production logs
- Chat message embedding lag: < 500ms from save to recallable
- PostChatMemoryWorkflow completion: p95 < 10s (async, doesn't affect user latency)
- Embedding service throughput: ≥ 200 req/s per replica (vs ~40 req/s with Python sentence-transformers)

**Product**

- Luna can handle a full day of conversation on WhatsApp without losing context on entities, commitments, or past topics
- Integral and Levi's can each run the platform on their own K8s cluster with a single `helm install`
- Adding a new source adapter takes ≤ 3 days of work (pattern is established)
- Onboarding a new agent type (e.g., HR Agent for Levi's) takes ≤ 1 day (scoping, tool access, identity profile)

**Operational**

- Helm upgrade rolls forward with zero downtime
- pgvector index size stays manageable with NightlyConsolidationWorkflow merging duplicates
- Temporal history size per workflow stays < 1MB (otherwise refactor into child workflows)
- Memory-core pod memory stays < 1GB under normal load

### 11.1. Anti-success criteria (rollback triggers)

Explicit things we roll back if they happen:

- **Fast-path p95 regresses > 30% from pre-Phase-1 baseline** → roll back feature flag `USE_MEMORY_V2`
- **Error rate on chat endpoint increases > 2x** → roll back feature flag
- **Gemma4 commitment classification F1 < 0.7** on the gold set (built as a Phase 1 prerequisite) → keep the regex stub no-op in place, do not retire it
- **`InFailedSqlTransaction` errors re-appear at > 0.1/min** → roll back any DB session changes
- **Chat-runtime pod memory > 2GB per replica** → revert to per-message subprocess (Phase 3a rollback)
- **Phase 2 Rust memory-core dual-read divergence > 10% of queries** → keep Python primary, do not flip
- **Any cross-tenant data leak detected in integration tests** → HARD STOP. Full rollback. Incident review.

### 11.2. Acceptance gates between phases

No phase starts until the previous phase meets its anti-success criteria AND its positive success criteria for 1 week of production use.

---

## 12. Testing Strategy

### Unit tests
- Memory API: each read/write function has unit tests with mocked pgvector
- Source adapters: raw → MemoryEvent transformations
- Ranking formula: golden-dataset tests for recall scoring
- Commitment classifier: fixture-based tests against Gemma4 output

### Integration tests
- End-to-end chat turn with memory pre-load: verify the correct context is injected into CLAUDE.md
- Multi-source reconciliation: simulate email + calendar disagreement, verify reconciliation workflow runs and surfaces contradiction
- Cross-tenant isolation: spawn two tenants, verify Tenant A's memory cannot be recalled by Tenant B's agents
- Agent scoping: verify agent_scoped records are only visible to the owning agent
- Episode generation: simulate 30 messages, verify EpisodeWorkflow fires and creates session_journal row

### Load tests
- Embedding-service: 500 concurrent embed requests
- Memory-core recall: 100 concurrent tenants, 10 recalls/sec each
- Chat-runtime warm pool: 100 concurrent chats, verify session affinity

### Chaos tests
- Kill memory-worker mid-workflow; verify Temporal retries and completes
- Kill chat-runtime pod mid-turn; verify next message lands on a different pod and succeeds (cold start acceptable)
- Kill embedding-service; verify api degrades gracefully (falls back to keyword search) and recovers when service returns

### Observability
- Prometheus metrics for all gRPC endpoints (duration, error rate, req/s)
- Tracing (OTEL) for the full chat turn: HTTP → api → memory-core → embedding-service → Temporal → chat-runtime
- Logs structured with tenant_id, agent_slug, chat_session_id, workflow_id
- Grafana dashboards: latency breakdown, memory recall hit rate, ingestion queue depth, Rust service perf

---

## 13. Migration and Rollout Plan

### Phase 1 rollout (in-place, no infra change)

1. Create feature branch
2. Implement `apps/api/app/memory/` package with tests
3. Add migrations for new columns and tables
4. Deploy to local Docker Compose, verify chat works with new memory layer
5. Deploy to staging tenant (or a test tenant) for 1 week
6. Deploy to production (saguilera1608@gmail.com first, then Integral, then Levi's)
7. Monitor: latency, recall accuracy, error rates

### Phase 2 rollout (Rust memory-core)

1. Stand up memory-core Rust service in parallel with Python memory package
2. Dual-write: every memory operation goes to BOTH Python and Rust
3. Shadow reads: Python serves the result, Rust result is compared in logs
4. Once Rust matches Python for 99%+ of queries, flip to Rust-primary
5. Keep Python as fallback for 1 release cycle
6. Remove Python memory package

### Phase 3 rollout (K8s migration)

1. Deploy full stack to kind locally (dev verifies)
2. Deploy to self-hosted runner staging
3. Write migration runbook
4. Migrate tenants one by one:
   - Export data from Docker Compose postgres
   - Import to K8s postgres
   - Cut over DNS (cloudflared config change)
   - Verify
5. Decommission Docker Compose once all tenants are on K8s

### Rollback strategy

- Each phase is independent and fully rollback-able
- Phase 1: feature flag `USE_MEMORY_V2` in settings; if disabled, old code paths run
- Phase 2: Python memory package stays in tree as fallback; flip env var to switch
- Phase 3: K8s and Docker Compose run in parallel during cutover; DNS flip is atomic

---

## 14. Open Questions

### Blocking — must resolve BEFORE Phase 1 starts

1. **`candle` vs `ort` for embedding-service** — 1-2 day benchmark. Load nomic-embed-text-v1.5 in both, measure throughput and memory footprint. Pick the winner. **Prerequisite to the first embedding-service container build.**
2. **Gemma4 commitment-classification accuracy** — build a 200-message gold-labeled fixture set (user + assistant turns, half with genuine commitments, half without). Run Gemma4 classification against it, measure F1. Must hit ≥ 0.7 to retire the regex stub; ideally ≥ 0.85. **Prerequisite to Phase 1 deliverable 'retire commitment_extractor.py'.**
3. **gRPC IDL full freeze** — publish the complete `.proto` files (not abbreviated) as `2026-04-07-memory-first-grpc-idl.proto` alongside this design doc before Phase 1 Python signatures are finalized.

### To resolve in implementation plan (non-blocking)

4. **Exact Temporal session affinity wiring** — Temporal session API vs custom queue routing vs Redis-backed sticky hash. Prototype during Phase 3a; keep Redis fallback ready.
5. **pgvector index type** — `ivfflat` vs `hnsw`; HNSW is newer but has higher memory. Start with `ivfflat`, benchmark and switch if needed.
6. **Episode summary length** — 200 words? 500? Tune against recall quality on a gold set.
7. **Consolidation aggressiveness** — how aggressively to merge entities in the nightly job. Start conservative (similarity > 0.95 AND same category), loosen over time.
8. **Observability stack choice** — Prometheus + Grafana + OTEL (probably). The dormant GKE deployment had Prometheus; reuse it.
9. **Rust async runtime** — tokio (default) vs async-std; tokio is the safe choice.
10. **gRPC vs HTTP+protobuf** — gRPC is the default; HTTP+protobuf as fallback if any component has trouble with HTTP/2.
11. **Schema migration tooling** — current `migrations/` folder is manual SQL; consider Alembic for Phase 1.
12. **Ollama deployment model in K8s** — native host is fine on a dev Mac (M-series GPU), but enterprise K8s clusters need either a GPU node pool or external Ollama. Document both options in Phase 3a.
13. **`memory_events` retention policy** — partition by month, drop after 12 months by default. Tenant-configurable.
14. **Fast-path intent gating** — should trivial messages ("hey", "thanks", "ok") skip the recall step entirely? Local Gemma4 intent classifier at ~30ms could shave recall overhead off ~30% of turns. Worth considering for the fast-path target.

---

## 15. Appendix: Alignment with Existing Code

### What this design keeps

- Temporal as the workflow engine
- Postgres + pgvector as the canonical store
- FastAPI as the HTTP layer
- Claude Code CLI as the primary reasoning runtime
- Gemma4 via Ollama for local extraction
- MCP tools (existing and future) as the external integration layer
- Knowledge graph (entities + observations + relations)
- Dynamic workflows + static workflows (business layer)
- RL experience logging + auto-scoring + provider council
- Multi-tenancy via tenant_id

### What this design changes

- Memory is now a distinct layer with a clean API boundary (instead of scattered service calls)
- Chat history is embedded (new)
- Commitments and goals are embedded (new)
- Session journals are populated (new — was half-wired, now implemented)
- Auto-extraction of commitments uses Gemma4 classification instead of regex
- Pre-loaded memory in the chat hot path (replaces ad-hoc context building)
- Warm CLI pods (replaces per-message subprocess spawn)
- Rust embedding service (replaces Python sentence-transformers in the hot path)
- K8s deployment (replaces Docker Compose)
- Single gRPC API for memory operations (replaces direct DB access from multiple places)

### What this design removes or retires

- Regex-based commitment extractor (retired in favor of Gemma4 classification)
- Hardcoded Gap 1/2/3 system prompt injection blocks (retired — memory is now surfaced via unified recall)
- `claude -p --resume` session bloat path (retired — session management is platform-owned)
- 800-char chat history truncation (retired — full messages up to 50K budget)
- `chat_session.memory_context` JSONB blob as a primary state holder (kept but deprecated in favor of proper memory layer)
- Ad-hoc threading for auto-scoring / knowledge extraction (retired — all async writes go through Temporal workflows)
