# `ap quickstart` — design

**Date:** 2026-05-11
**Owner:** `apps/agentprovision-cli` + `apps/agentprovision-core` + `apps/api`
**Status:** Proposed
**Related plans:**
- `docs/plans/2026-05-11-ap-cli-multi-runtime-dispatch-plan.md`
- `docs/plans/2026-04-07-memory-first-agent-platform-design.md`

## 1. Strategic frame

The whole reason `ap`/AgentProvision exists is to **simplify agent adoption**. A platform is only as adopted as its first 5 minutes. Today the first-5-minute path for a new user looks like:

1. Sign up
2. Pick which LLM to bring (4 options)
3. Connect OAuth integrations (Google, Slack, WhatsApp, GitHub… → 12 forms)
4. Create an agent (5-step wizard, 8 templates)
5. Compose tool_groups + memory_domains + persona_prompt
6. Wait for memory to be populated by inbox monitor (15 min cycle)
7. Send first chat — but the agent has no context yet, so the response is generic

That's 30+ steps and ~30 minutes before the user sees value. Most never get there.

**`ap quickstart` collapses this to one command and ~2 minutes.** The unifying insight: agents don't feel useful until they *know things about the user*. Loading that context — **initial training** — is the actual first step, not an afterthought. Everything else (agent picker, persona, tool config) can be defaulted automatically from what the training reveals.

## 2. The flow

```
$ ap quickstart          (or: auto-fires on first ap login for new tenants)
   │
   ├─ (1) login           — device-flow, opens browser; idempotent
   ├─ (2) tenant resolve  — auto-join existing or create new based on email domain
   ├─ (3) wedge channel   — interactive picker, biased by what's detected locally
   ├─ (4) connect         — OAuth (Gmail/Slack/GH) or local scan (CLI/git/Claude-history)
   ├─ (5) INITIAL TRAINING — bulk extract entities/observations/commitments from channel
   ├─ (6) recommend agent — derived from extracted entities (industry hints, tool hints)
   └─ (7) first chat      — pre-loaded with the right memory; opens REPL
```

Step (5) is the centerpiece. Everything else is plumbing.

## 2.1 Auto-trigger: this is NOT a hidden command

Quickstart isn't something users have to discover and type. **It auto-fires on first contact** with the platform on both surfaces:

### Web (UI)

After a successful registration → first dashboard load, the SPA checks the tenant's onboarding status. If unfinished, it routes to `/onboarding/*` (modal-style, can't dismiss until done or skipped) which walks the same 7-step flow but rendered as React screens. On completion, the server stamps `tenants.onboarded_at` and the user lands on the dashboard with memory already pre-loaded.

### CLI

After a successful `ap login`, the CLI calls `GET /api/v1/onboarding/status`. If the tenant is unfinished, the CLI prints:

```
Welcome to AgentProvision. Let's set up your first agent (~2 minutes).
Skip with --no-onboarding next time.

[1/4] Pick a source so I can learn your context →
```

…and dispatches straight into the wedge-picker. The user can press `q` to skip; that records `onboarding.deferred_at` so the next `ap login` doesn't re-prompt but `ap quickstart` still works to opt in later.

### Idempotency rules

- `tenants.onboarded_at` (timestamp): once set, onboarding never auto-triggers again. Manual `ap quickstart` or `/onboarding` URL still works for re-syncing.
- `tenants.onboarding_deferred_at` (timestamp): user said "skip"; auto-trigger suppressed until they explicitly request it.
- `--force` flag on `ap quickstart` ignores both flags and re-runs the flow.

### Why on first login and not on first registration

Registration sets the user/tenant up but doesn't know which device the user is on or what local context exists. By moving auto-trigger to first **login** (the moment the user actually shows up on a surface), we can:
- Detect installed local CLIs (`ap`) or detect which OAuth providers the email domain already uses (web)
- Make a smart channel recommendation based on real signal
- Avoid forcing onboarding on a user who registered just to invite a teammate

## 3. The wedge channels

| Wedge | Friction | Signal strength | Best for |
|---|---|---|---|
| **Claude Code** (history + repos touched) | none — reads `~/.claude/projects/*.jsonl` | very high for Claude users | engineers using Claude |
| **Codex** (history) | none — reads `~/.codex/*` | very high for Codex users | engineers using Codex |
| **Gemini CLI** (history) | none — reads `~/.gemini/*` | high | engineers using Gemini |
| **GitHub Copilot CLI** (config) | none — local config | medium | Copilot users |
| **OpenCode + local Gemma 4** (history) | none — reads `~/.local/share/opencode/storage/*` | high; **runs zero-cost on user's GPU** | privacy-sensitive / cost-sensitive / air-gapped |
| **GitHub CLI** (`gh`) | none — uses existing `gh auth` | very high for OSS / repo work | engineers, OSS contributors |
| **Gmail + Calendar** | one OAuth click | very high | managers, salespeople, ops |
| **Slack** | one OAuth click + workspace pick | medium-high | team workers |
| **WhatsApp** | QR pair | high (personal context) | SMB owners, LATAM markets |

All "Local AI CLI" + "GitHub CLI" wedges share the same property that the user **already** authenticated their tool (`claude login`, `gh auth login`, `gemini auth`, etc.) — quickstart just *reads* the resulting local state. No new OAuth dance, no platform-side credential exchange.

Quickstart's interactive picker biases the order based on local detection:

- `gh auth status` returns "Logged in" → suggest GitHub CLI first (single API call, broadest dev coverage).
- `which claude` + `~/.claude/projects/` non-empty → suggest Claude Code.
- `which codex` + `~/.codex/` non-empty → suggest Codex.
- `which gemini` + `~/.gemini/` non-empty → suggest Gemini.
- `which copilot` → suggest Copilot.
- Else if email domain looks like a company → suggest Gmail.
- Always show all wedges; user can override.

The picker is greedy: if multiple AI-CLI signals fire (user has all four installed), default to the one with the most recent activity (`stat` the newest file under each `~/.<cli>/` directory). A single quickstart run can chain multiple wedges — after the first one trains, the picker offers "Add another source? (recommended for richer context)".

## 4. Initial training — the actual work

For each wedge, the training pipeline:

```
source → fetch raw items → batched Gemma extract → upsert entities/observations/commitments → embed
```

### 4.1 Local AI CLI wedge (the novel one)

The CLI scans the local machine (with explicit consent — opt-in prompt):

```rust
// agentprovision-core::training::local_scan
pub struct LocalTrainingSnapshot {
    pub user_email: Option<String>,          // git config user.email
    pub user_name: Option<String>,           // git config user.name
    pub repos: Vec<RepoSnapshot>,            // top 20 most-recent-commit repos under $HOME
    pub claude_sessions: Vec<ClaudeSessionSnapshot>, // ~/.claude/projects/*.jsonl
    pub installed_runtimes: Vec<RuntimeId>,  // which claude/codex/gemini/copilot
}
```

For each repo: `{path, name, languages_detected (from file extensions), recent_commits[20]{sha,subject,date,files_changed}}`.

For each Claude session: `{project_path, started_at, last_message_at, message_count, derived_topics (Gemma-extracted from first user message in each turn)}`. Note: **we never upload raw conversation content** — only extracted topics, project paths, and timestamps. The local file is read; the wire payload is the extracted metadata.

The snapshot is POSTed to `/api/v1/memory/training/bulk-ingest`. Backend extracts entities:
- Person (user themselves) — high-confidence, pinned.
- Projects (one per active repo) — category=`project`, attributes `languages`, `repo_path`, `last_commit`.
- Technologies (Rust, Python, etc.) — observations linked to projects.
- Recurring topics from Claude history — observations linked to relevant projects.

### 4.1b GitHub CLI wedge

When `gh auth status` is authenticated, quickstart shells out to:

```
gh api user                                  # → person entity (self)
gh repo list --limit 50 --json ...           # → project entities
gh api /user/orgs                            # → organization entities
gh search prs --author=@me --limit 50 ...    # → recent activity observations
gh search issues --involves=@me ...          # → recent issues observations
```

Why this is different from the AI-CLI wedge: a dev might not use any AI CLI yet but already have `gh` set up. This wedge captures their **public** repo activity (open source, work-org-public repos they have access to) without ever needing OAuth on the AgentProvision side — the `gh` CLI already has the token.

Wire payload: just the structured `gh api` JSON, batched and ingested by the bulk endpoint. No raw repo content is read.

### 4.2 Gmail/Calendar wedge

Reuses existing `inbox_monitor` activities but in a one-shot bulk mode:
- Last 200 emails (by date) → Gemma extracts people, orgs, projects, commitments.
- Next 30 days of calendar events → events + attendees → entities.
- Rate-limited: 20 items/batch, 5 batches in parallel, with backpressure.

### 4.3 Slack wedge

OAuth → `users.list` (workspace members → person entities), `conversations.list` for joined channels (channel entities), recent message history per channel → topic observations.

### 4.4 WhatsApp wedge

Reuses neonize WhatsApp service. Contact list → person entities. Recent group chats → group entities + recent topic observations.

## 5. Agent recommendation

After training the platform looks at the extracted entity profile and picks an agent template:

| Signal | Recommended agent |
|---|---|
| `language=rust|python|js` projects present | "Code Assistant" (tool_groups=`code,github,shell`) |
| `category=deal` or `prospect` entities present | "Sales Co-pilot" (tool_groups=`sales,knowledge,competitor`) |
| `category=client` and `appointment` entities | "Customer Success Agent" (tool_groups=`crm,calendar,email`) |
| domain ∈ vet vocab (cardiac, patient) | "Vet Triage" (HealthPets pack) |
| domain ∈ hospitality vocab (booking, guest) | "Hospitality Concierge" (Aremko pack) |
| nothing distinctive | "Luna" (general) |

The user can override at the picker step; default lands them somewhere useful.

## 6. First chat

After training + agent select, the CLI opens an interactive REPL pre-bound to the new agent. The very first system message in the REPL transcript is a one-line summary the user sees:

```
> Welcome, Simon. I've loaded 47 facts about your last 3 projects (agentprovision-agents,
  aremko, deal-research) plus 12 open commitments from your recent activity. What
  should we work on?
```

That sentence is the entire product pitch. If the user reads it and types a follow-up, adoption succeeded.

## 7. Backend precursors

### 7.0 Tenant onboarding state — schema + endpoints

Add to `tenants` table (migration 126 or next):

```sql
ALTER TABLE tenants
  ADD COLUMN onboarded_at         TIMESTAMP NULL,
  ADD COLUMN onboarding_deferred_at TIMESTAMP NULL,
  ADD COLUMN onboarding_source    VARCHAR(32) NULL;  -- 'cli' | 'web'
```

Endpoints:

```
GET  /api/v1/onboarding/status
     → 200 { onboarded: bool, deferred: bool, recommended_channel: str, detected_signals: {...} }

POST /api/v1/onboarding/defer
     → 204 (sets onboarding_deferred_at = now())

POST /api/v1/onboarding/complete
     → 204 (sets onboarded_at = now(); idempotent if already set)
```

`status` is what auto-trigger keys off. `recommended_channel` is server-side logic (email domain ∈ known-corp-mailservers list → gmail; presence of GitHub OAuth in tenant signals → github; etc.) — keeps the wedge-picker bias logic in one place.

### 7.1 `POST /api/v1/memory/training/bulk-ingest`

```python
class BulkIngestRequest(BaseModel):
    source: Literal["local_cli", "gmail", "calendar", "slack", "whatsapp"]
    items: List[Dict[str, Any]]            # source-specific schema
    snapshot_id: uuid.UUID                 # idempotency key — server dedups by this

class BulkIngestResponse(BaseModel):
    training_run_id: uuid.UUID             # to subscribe to progress SSE
    estimated_seconds: int
```

Body validated per-source by a discriminated union. Endpoint enqueues a `TrainingIngestionWorkflow` on `agentprovision-orchestration`.

### 7.2 `TrainingIngestionWorkflow`

```python
@workflow.defn
class TrainingIngestionWorkflow:
    @workflow.run
    async def run(self, tenant_id, source, items, snapshot_id):
        # Idempotent: bail if snapshot_id already processed
        if already_processed(tenant_id, snapshot_id):
            return
        for batch in chunked(items, 20):
            entities = await extract_entities_activity(tenant_id, source, batch)
            await upsert_entities_activity(tenant_id, entities)
            await embed_entities_activity(tenant_id, [e.id for e in entities])
            await publish_progress_event(tenant_id, training_run_id, ...)
```

Heartbeats every 60s; total runtime bounded by `items.len() / 20 * ~3s` (Gemma local inference).

### 7.3 `GET /api/v1/memory/training/{run_id}/events/stream`

SSE endpoint emitting `progress`, `batch_complete`, `done` events. CLI consumes these and renders a progress bar.

## 8. CLI surface

```
ap quickstart                          # interactive
ap quickstart --channel local-cli      # skip the picker
ap quickstart --channel gmail
ap quickstart --no-chat                # skip step (7), useful for scripts
ap quickstart --resume                 # resume an interrupted run
```

State is persisted to `~/.config/agentprovision/quickstart.toml` so a crash mid-training is recoverable.

## 9. PR breakdown (chained branches)

- **PR-Q0** (S, 1d): tenant onboarding state migration + `/onboarding/status|defer|complete` endpoints. Web SPA auto-trigger route guard. Doesn't require any new training pipeline yet — just the routing flag.
- **PR-Q1** (M, 2-3d): backend training endpoint + `TrainingIngestionWorkflow` + SSE progress stream. No CLI changes. Tested via `curl` + a script-driven test tenant.
- **PR-Q2** (S, 1d): CLI `quickstart` command skeleton — auth gate, tenant resolve, channel picker UI, calls training endpoint with a stub `local_cli` items payload, renders SSE progress, fires first chat. Wired into `ap login` post-success auto-trigger via `/onboarding/status`.
- **PR-Q3a** (M, 2d): Local AI CLI scanner (`agentprovision-core::training::local_ai`) — git config + repo enumeration + Claude / Codex / Gemini / Copilot session reading + opt-in consent prompt + entity extraction. One adapter per CLI; each ships its own session-file parser.
- **PR-Q3b** (S, 1d): GitHub CLI wedge — shells out to `gh api` / `gh repo list` / `gh search`.
- **PR-Q4** (M, 2d): Gmail/Calendar wedge — reuses existing inbox_monitor activities in bulk mode.
- **PR-Q5** (S, 1d each): Slack, WhatsApp wedges.
- **PR-Q6** (M, 2d): Web SPA onboarding screens — mirror the CLI wedge picker as React pages mounted at `/onboarding/*`. Shares the same training pipeline.

Chained branching per the project rule (each PR off the previous, not main).

## 10. Privacy + security

- **Local CLI scan is opt-in per run.** First-time `ap quickstart` prompts: "Read your local CLI history to seed memory? Only extracted topics are uploaded, never raw conversations. [y/N]"
- **Snapshot upload uses the user's Bearer JWT**, scoped to tenant. The training endpoint is NOT under `/internal/*`.
- **Idempotent** via `snapshot_id` so re-runs don't double-write entities.
- **Rate-limited per tenant** (~5 trainings/hour) to avoid abusive bulk-extract loops blowing up the orchestration queue.
- **PII shielding** — extracted entities go through the same validator path as inbox_monitor (no PII leaks into observations beyond what was already in the source).

## 11. Why this ranks above multi-runtime dispatch (`ap claude-code`)

Multi-runtime dispatch lets a user who has already adopted the platform run different runtimes through the same context. Quickstart is what gets them adopted in the first place. The order is: stabilize → quickstart → multi-runtime dispatch → vertical demos.

## 12. Success metric

A new user who runs `ap quickstart` should be able to type the question *"what should I work on next?"* within 2 minutes of running the command, and get back a response that references at least one project/person/commitment the platform extracted from their wedge channel. That's the adoption moment.

## 13. Open questions / future work

- **Calendar table.** There is currently no calendar-events table — `_build_anticipatory_context` queries `channel_events` columns that don't exist (root cause of the cascade fixed in PR #399). Pre-loading calendar context for the first chat needs a real table. Either: extend `channel_events` schema with `title/start_time/description` columns, OR add a new `calendar_events` table with proper indexing. Probably the latter.
- **Cross-channel correlation.** When a user trains via local-CLI *and* Gmail, can we collapse the "Simon Aguilera" person entity into one? Needs an alias-resolution pass.
- **Re-training cadence.** Should quickstart re-run automatically every N days, or only when the user explicitly asks? First version: explicit only (`ap quickstart --resync`).
