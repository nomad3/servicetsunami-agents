<h1 align="center">AgentProvision / AgentProvision</h1>

<p align="center"><strong>The Orchestration Layer for AI Agents</strong></p>

<p align="center">
  <a href="https://agentprovision.com"><img src="https://img.shields.io/badge/live-agentprovision.com-00d2ff?style=flat-square" alt="Production"></a>
  <a href="#"><img src="https://img.shields.io/badge/agents-Claude%20Code%20%7C%20Codex%20%7C%20Gemini%20%7C%20Copilot-blueviolet?style=flat-square" alt="Agents"></a>
  <a href="#"><img src="https://img.shields.io/badge/MCP_Tools-90%2B-ff6b6b?style=flat-square" alt="MCP Tools"></a>
  <a href="#"><img src="https://img.shields.io/badge/skills-92%2B%20marketplace-green?style=flat-square" alt="Skill Marketplace"></a>
  <a href="#"><img src="https://img.shields.io/badge/workflows-26%20native-9b59b6?style=flat-square" alt="Workflows"></a>
  <a href="#"><img src="https://img.shields.io/badge/RL-auto%20scoring-orange?style=flat-square" alt="RL"></a>
  <a href="#"><img src="https://img.shields.io/badge/tunnel-Cloudflare-4285F4?style=flat-square" alt="Cloudflare"></a>
  <a href="#"><img src="https://img.shields.io/badge/Luna_Client-Tauri%202.0-24C8DB?style=flat-square" alt="Luna Client"></a>
</p>

<p align="center">
  Don't build agents — orchestrate them. AgentProvision routes tasks to existing AI agent platforms (Claude Code, Codex, Gemini CLI, GitHub Copilot CLI), serves 90+ MCP tools, maintains a knowledge graph, auto-scores every response with a local LLM, and learns which platform performs best via RL. Enterprise-grade <b>Agent Lifecycle Management</b> with versioning, audit, rollback, and governance. Each tenant uses their own subscription — zero API credits.
</p>

> **Latest:** week of 2026-04-12 → 04-19 shipped the Agent Lifecycle Management Platform, A2A Collaboration, Luna OS Spatial Workstation, native voice PTT, redesigned landing page, and security hardening. See `docs/changelog/2026-04-12-to-2026-04-19.md` for the full digest.

---

## Luna — Native AI Client

Luna is the native presence layer for AgentProvision. A 4.9MB Tauri 2.0 desktop app that lives in your macOS menu bar.

| Feature | Status |
|---------|--------|
| Native macOS ARM64 app (Tauri 2.0 + Rust) | Shipped |
| System tray with show/hide toggle | Shipped |
| Cmd+Shift+Space global shortcut (Raycast-style) | Shipped |
| Cmd+Shift+Space native push-to-talk (Rust cpal audio) | **Shipped 2026-04-19** |
| Cmd+Shift+L Spatial HUD (Three.js knowledge nebula) | **Shipped 2026-04-13** |
| Chat with SSE streaming + markdown rendering | Shipped |
| Emotion-reactive LunaAvatar (SVG, state-driven) | **Shipped 2026-04-19** |
| Shell heartbeat + device handoff protocol | Shipped |
| Native macOS notifications (Tauri plugin) | Shipped |
| Screenshot capture + upload to Luna for analysis | Shipped |
| Memory panel (episodic recall, cross-shell context) | Shipped |
| Trust model + action approval for local actions | Shipped |
| PWA fallback (installable from browser, offline) | Shipped |
| Docker + Cloudflare Tunnel (`luna.agentprovision.com`) | Shipped |
| Device bridge (camera, IoT registry) | **Shipped 2026-04-19** |
| MediaPipe hand tracking (spatial gestures) | **Shipped 2026-04-13** |
| Auto-updater via GitHub Releases | Shipped |
| iOS / Android (Tauri mobile builds) | Planned |
| BLE wearable relay (necklace, glasses) | Planned |
| Wake-word activation | Planned |

```bash
# Build Luna.app (requires Rust toolchain)
cd apps/luna-client
npm install
VITE_API_BASE_URL=http://localhost:8000 cargo tauri build --target aarch64-apple-darwin

# Dev mode (hot reload)
VITE_API_BASE_URL=http://localhost:8000 cargo tauri dev
```

---

## Architecture

```
Internet ─▶ Cloudflare Tunnel
  ├─▶ agentprovision.com                (web + API)
  └─▶ luna.agentprovision.com           (Luna PWA / Tauri client)

┌─────────────────────────────────────────────────────────────────────┐
│  Channels: WhatsApp (Neonize) · Web Chat · Luna Desktop · API       │
└───────────────────────────────┬─────────────────────────────────────┘
                                │
┌───────────────────────────────▼─────────────────────────────────────┐
│  FastAPI Backend                                                     │
│  ┌─────────────┐  ┌────────────────┐  ┌────────────────────────┐    │
│  │ Agent Router│  │ Session Manager│  │ Auto Quality Scorer    │    │
│  │ (zero LLM)  │  │ (CLAUDE.md gen)│  │ (Gemma 4, 6-dim → RL)  │    │
│  └─────────────┘  └────────────────┘  └────────────────────────┘    │
│  ┌─────────────────────────────────────────────────────────────┐    │
│  │ Agent Lifecycle Management (ALM)                            │    │
│  │   · Versioning + rollback    · Audit log (compliance)       │    │
│  │   · Performance snapshots    · Governance policies          │    │
│  │   · Redis registry           · External agent adapters      │    │
│  └─────────────────────────────────────────────────────────────┘    │
│  ┌─────────────────────────────────────────────────────────────┐    │
│  │ A2A Collaboration: Blackboard · CoalitionWorkflow · SSE     │    │
│  └─────────────────────────────────────────────────────────────┘    │
└───────────────────────────────┬─────────────────────────────────────┘
                                │
┌───────────────────────────────▼─────────────────────────────────────┐
│  Temporal Workers                                                    │
│                                                                      │
│  ┌─────────────┐  ┌─────────────┐  ┌───────────┐  ┌──────────────┐  │
│  │ Claude Code │  │ Codex CLI   │  │ Gemini CLI│  │ Copilot CLI  │  │
│  │ (Anthropic) │  │ (OpenAI)    │  │ (Google)  │  │ (GitHub)     │  │
│  └─────────────┘  └─────────────┘  └───────────┘  └──────────────┘  │
│      All use tenant subscriptions via OAuth vault — zero API credits│
│                                 │                                    │
│                       ┌─────────▼──────────┐                         │
│                       │  MCP Tool Server   │  FastMCP, 90+ tools     │
│                       │  (Drive, Email,    │  X-Internal-Key auth    │
│                       │   Jira, Ads, etc.) │                         │
│                       └────────────────────┘                         │
│                                                                      │
│  Dynamic Workflows (26 native templates, JSON-defined, ReactFlow UI) │
└───────────────────────────────┬─────────────────────────────────────┘
                                │
┌───────────────────────────────▼─────────────────────────────────────┐
│  Data + Local ML                                                     │
│  ┌──────────────────┐  ┌──────────────┐  ┌─────────────────────┐    │
│  │ PostgreSQL +     │  │ Redis        │  │ Ollama (Gemma 4)    │    │
│  │ pgvector (768d)  │  │ (pub/sub,    │  │ - Auto scoring      │    │
│  │ - agents (ALM)   │  │  agent reg)  │  │ - Knowledge extract │    │
│  │ - blackboards    │  │              │  │ - Email triage      │    │
│  │ - workflows      │  │              │  │ - Fallback chat     │    │
│  │ - knowledge graph│  │              │  │                     │    │
│  └──────────────────┘  └──────────────┘  └─────────────────────┘    │
│  ┌──────────────────────────┐  ┌────────────────────────────────┐    │
│  │ Rust embedding-service   │  │ Rust memory-core               │    │
│  │ (fastembed, gRPC :50051) │  │ (gRPC :50052, Recall/Record)   │    │
│  └──────────────────────────┘  └────────────────────────────────┘    │
└──────────────────────────────────────────────────────────────────────┘
```

## Luna Memory System (v2)

Luna remembers like a person, not a database. Seven memory modules work together.

```
User Message: "Tell me about the desk robot project"
    |
    v
+-------------------+     +--------------------------+
| Embed (768-dim)   |---->| pgvector cosine search   |
| nomic-embed-text  |     +--------------------------+
+-------------------+              |
                     +-------------+-------------+-------------+
                     |             |             |             |
               +-----v-----+ +----v------+ +----v-----+ +----v---------+
               | Entities  | | Memories  | | Episodes | | Contradicts  |
               | top 10    | | top 5     | | top 3    | | (disputed)   |
               | +keyword  | | +decay    | | +mood    | |              |
               | +session  | | +scorer   | | +source  | |              |
               | boost     | | confidence| |          | |              |
               +-----------+ +-----------+ +----------+ +--------------+
                     |             |             |             |
               +-----v-------------v-------------v-------------v------+
               |                  Memory Context                       |
               |  entities: Phoebe [excited] (from chat Mar 27)        |
               |  episodes: "Yesterday discussed desk robots..."       |
               |  conflicts: "Phoebe was 'contact', now 'product'"     |
               |  time: "Good morning! It's Monday"                    |
               |  calendar: "Sprint Planning at 10:30 AM"              |
               |  preferences: "response_length: short (72%)"          |
               |  dream patterns: "codex works well for routing"       |
               +-------------------------+----------------------------+
                                         |
                                         v
                                   CLAUDE.md injection
                                   (Luna's instructions)
```

```
Nightly Dream Cycle (Temporal workflow)
    |
    +---> Scan RL experiences (last 24h)
    |         |
    |         v
    +---> Extract decision patterns (avg reward per action)
    |         |
    |         v
    +---> Generate dream insights + synthesize memories
    |         |
    |         v
    +---> Consolidate RL policy weights
    |         |
    |         v
    +---> Prune stale entities (health < 0.1, age > 30d)
    |         |
    |         v
    +---> Merge duplicate entities (same name+type)
    |         |
    |         v
    +---> Learn user preferences (response patterns)
    |         |
    |         v
    +---> Morning report via WhatsApp
```

| Module | What Luna does | Example |
|--------|---------------|---------|
| **Source Attribution** | Remembers WHERE she learned things | `"from chat Mar 27"` |
| **Contradiction Detection** | Flags conflicting facts for user | `"was 'contact' but now 'product'"` |
| **Episodic Memory** | Remembers conversation stories | `"Yesterday discussed desk robots, you were excited"` |
| **Emotional Memory** | Tags sentiment on knowledge | `[excited]`, `[frustrated]`, `[curious]` |
| **Anticipatory Context** | Time/calendar-aware proactive context | `"Good morning! Sprint Planning at 10:30"` |
| **Active Forgetting** | Prunes noise, merges duplicates nightly | Health scoring + archival + dedup |
| **User Preferences** | Learns communication style from RL | `"prefers short responses (72% confidence)"` |

## Luna Presence System

Luna's face reacts to what she's doing. Tamagotchi-style chat header with ambient glow.

```
+------------------------------------------+
|                                          |
|         ◜   ◝    ? ...                   |  <-- 200px avatar
|            ·        (thinking emote)     |      with state glow
|         ╰────╯                           |
|                                          |
|         Session Title                    |
|         Luna General Assistant           |
+------------------------------------------+
|  User: tell me about Phoebe             |  <-- chat scrolls
|                                          |      independently
|  Luna: Phoebe is the desk robot...      |
|                                          |
|  [input box]                    [Send]   |
+------------------------------------------+
```

```
State Machine:

idle ──> listening ──> thinking ──> responding ──> idle
  |                      |              |
  +──> sleep (30m)       +──> focused   +──> happy (score>=85)
  |                      |              +──> empathetic (failed)
  +──> private (muted)   +──> error     +──> playful (casual)
                         +──> alert (high-priority notification)
                         +──> handoff (device switch)
```

| State | Trigger | Emote |
|-------|---------|-------|
| idle | Response delivered | `~` |
| listening | WhatsApp inbound | `((·))` |
| thinking | CLI dispatch | `? ...` |
| responding | Response received | `> _ <` |
| happy | Score >= 85 or thumbs up | hearts + stars |
| focused | Code task | `</>` |
| alert | High-priority notification | `!! triangle !!` |
| error | CLI failure | `#!@%&` |
| sleep | 30 min idle | `z Z z` |

## Agent Lifecycle Management Platform (ALM)

Enterprise governance layer for agents in production. Shipped 2026-04-18 (PR #153).

```
  Draft ──► Staging ──► Production ──► Deprecated
              │             │               │
              └──promote────┴───rollback────┘
                   (writes agent_versions snapshot)
```

| Feature | Description |
|---------|-------------|
| **Versioning** | Every promote creates a config snapshot (`agent_versions`). `POST /agents/{id}/rollback/{version}` restores. |
| **Audit log** | `agent_audit_log` captures create/update/promote/deprecate/rollback with actor, before/after, reason. `GET /agents/{id}/audit-log`, `GET /audit/agents`. |
| **Performance snapshots** | Hourly Temporal rollup: success rate, p95 latency, tokens, cost, quality score. `GET /agents/{id}/performance`. |
| **Governance policies** | `agent_policies` — rate limits, approval gates, allowed tool allowlist, blocked actions. |
| **RBAC** | `agent_permissions` — per-user/team role (owner / editor / viewer). |
| **Registry discovery** | Redis-backed capability index. `GET /agents/discover?capability=<x>` returns matching active agents. |
| **External agents** | `external_agents` table + adapters for OpenAI Assistants, webhook endpoints, MCP protocol. |
| **Framework import** | `POST /agents/import` — CrewAI / LangChain / AutoGen configs → Agent. |
| **Human-in-the-loop** | `POST /agent-tasks/{id}/workflow-approve` — gates workflow steps on admin signoff. |
| **Per-agent integration binding** | `agent_integration_configs` pivot — one agent can use a different Gmail than another. |

**UI:** `AgentsPage` fleet view with status badges + Import Agent modal. `AgentDetailPage` with Overview / Performance / Audit / Versions / Integrations tabs.

---

## A2A Collaboration System

Multi-agent coalitions that solve problems together through phased workflows on a shared blackboard. Shipped 2026-04-12.

```
  User message: "INCIDENT: master data catalog is down"
       │
       ▼
  +-- CoalitionWorkflow (pattern: incident_investigation) ------+
  │                                                             │
  │   Phase 1: gather_facts                                     │
  │      ├─ SRE agent  ──► ChatCliWorkflow child                │
  │      ├─ Data agent ──► ChatCliWorkflow child                │
  │      └─ DevOps     ──► ChatCliWorkflow child                │
  │          ↓ each writes to Blackboard (source_node_id)       │
  │                                                             │
  │   Phase 2: hypothesize (agents read blackboard, propose)    │
  │                                                             │
  │   Phase 3: prescribe (consensus action plan)                │
  │                                                             │
  │   Redis pub/sub ──► SSE stream ──► CollaborationPanel UI    │
  +-------------------------------------------------------------+
```

| Component | Description |
|-----------|-------------|
| `CoalitionWorkflow` | Top-level workflow on `agentprovision-orchestration` queue |
| `ChatCliWorkflow` | Child workflow per agent turn (runs on `agentprovision-code`) |
| `Blackboard` model | Shared context substrate. `chat_session_id` + `source_node_id` per entry (migration 091). |
| `collaboration_events.py` | Redis pub/sub client |
| `GET /chat/sessions/{id}/events/stream` | Unified SSE stream (chat + collaboration) |
| `CollaborationPanel` React | Live mode + phase timeline + blackboard feed + replay |

**Patterns defined:** `incident_investigation`, `deal_brief`, `cardiology_case_review`.

**Design principle:** A2A dispatches are **CLI-agnostic** — they route through RL policy, never hardcode a specific CLI.

---

## Luna OS Spatial Workstation

Game-inspired transparent Tauri window (`Cmd+Shift+L`) for A2A visualization and knowledge exploration. Shipped 2026-04-13.

- **Knowledge Nebula** — 3D scatter of memory entities with instanced rendering + bloom. WASD flight controls.
- **A2A Strategic Combat visuals** (Phase 6) — Agent avatars, comms beams between active collaborators, inventory panel.
- **MediaPipe hand tracking** (Phase 7) — Native webcam pipeline + hand-pose detection for spatial gestures.
- **RAID status overlay** — Real-time Temporal workflow status.
- Rust-side `project_embeddings` command does a cheap 3-PC projection (full UMAP pending).

---

## Dynamic Workflows — 26 Native Templates

JSON-defined workflows interpreted at runtime by a single `DynamicWorkflowExecutor` Temporal workflow. Visual ReactFlow builder at `/workflows/builder/:id`.

**Step types:** `mcp_tool`, `agent`, `condition`, `for_each`, `parallel`, `wait`, `transform`, `human_approval`, `webhook_trigger`, `workflow`, `continue_as_new`, `cli_execute`, `internal_api`.

**Triggers:** `cron`, `interval`, `webhook`, `event`, `manual`, `agent`.

Templates include the HealthPets **Cardiac Report Generator** (2026-04-19): email → PDF extraction → DACVIM cardiac evaluation → Google Doc.

---

## Auto Quality Scoring & RL

Every agent response is automatically scored by a local Gemma 4 model across 6 dimensions:

| Dimension | Max | What it measures |
|-----------|-----|-----------------|
| Accuracy | 25 | Factual correctness, no hallucinations |
| Helpfulness | 20 | Addresses actual user need, actionable |
| Tool Usage | 20 | Appropriate MCP tool selection |
| Memory Usage | 15 | Knowledge graph recall, context building |
| Efficiency | 10 | Concise, fast response |
| Context Awareness | 10 | Conversation continuity |

Scores are logged as **RL experiences** with cost tracking (tokens/cost per quality point) and platform recommendations. The system learns which agent platform performs best per task type. Zero cloud cost — fully local inference.

## Local ML Pipeline

All lightweight ML tasks run locally via Ollama (zero cloud cost):

| Task | Model | Replaces |
|------|-------|----------|
| Response quality scoring | gemma4 | Manual review |
| Conversation summarization | gemma4 | Anthropic API calls |
| Knowledge extraction | gemma4 | Anthropic API calls |
| Email/calendar triage | gemma4 | Anthropic API calls |
| Competitor analysis | gemma4 | Anthropic API calls |
| Message intent classification | gemma4 | LLM-based routing |
| Free-tier fallback (Luna) | gemma4 | Error message |
| MCP tool calling | gemma4 | None — new capability |

## 90+ MCP Tools

All tools served via Anthropic's MCP protocol (FastMCP, Streamable HTTP).

| Category | Tools | Count |
|----------|-------|-------|
| **Knowledge Graph** | create/find/update entity, relations, observations, search, timeline | 11 |
| **Email** | search, read, send, download attachment, deep scan, list accounts | 6 |
| **Calendar** | list events, create event | 2 |
| **Drive** | search, read, create file (Google Docs), list folders | 4 |
| **Jira** | search/get/create/update issues, list projects | 5 |
| **GitHub** | repos, issues, PRs, file read, code search | 8 |
| **Copilot Studio** | DirectLine agent proxy (new 2026-04-18) | 1 |
| **Ads** | Meta/Google/TikTok campaigns, insights, ad libraries | 12 |
| **Data** | SQL queries, datasets, schema, insights | 4 |
| **Sales** | qualify leads, outreach, pipeline, proposals, follow-ups | 6 |
| **Competitor** | add/remove/list, reports, compare campaigns | 5 |
| **Monitor** | inbox start/stop/status, competitor start/stop/status | 6 |
| **Reports** | document extraction, Excel generation | 2 |
| **Analytics** | calculate, compare periods, forecast | 3 |
| **Dynamic Workflows** | Luna CRUD for workflows (create, list, update, delete, run, status, activate, install_template) | 8 |
| **Skills** | list, run, match context, recall memory | 4 |
| **Devices** (new) | device registry, camera integration | 2 |
| **Shell** | execute commands, deploy changes | 2 |
| **Connectors** | query data sources | 1 |
| **Supermarket / Aremko / Remedia** | tenant-specific business operations | ~8 |

## Platform Auth

Each CLI platform uses subscription-based OAuth — zero API credits:

| Platform | Auth | Status |
|----------|------|--------|
| Claude Code | OAuth token via vault | **Live** |
| Codex (OpenAI) | auth.json via vault | **Live** |
| Gemini CLI | Manual OAuth (via Web UI) | **Live** |
| GitHub Copilot CLI | OAuth token via vault | **Live (new 2026-04-18)** |
| GitHub | OAuth via agentprovision.com | **Live** |
| Gmail / Calendar / Drive | Google OAuth with auto-refresh | **Live** |
| Microsoft / Outlook | Microsoft OAuth | **Live** |
| Jira | Basic Auth | **Live** |
| Copilot Studio (DirectLine) | Per-request token passthrough | **Live (new 2026-04-18)** |

## Quick Start

```bash
git clone https://github.com/nomad3/servicetsunami-agents.git
cd servicetsunami-agents

# 1. Configure secrets (all three are required — no defaults)
cp apps/api/.env.example apps/api/.env
# Edit apps/api/.env to set SECRET_KEY, API_INTERNAL_KEY, MCP_API_KEY
# Generate with: python -c "import secrets; print(secrets.token_hex(32))"

# 2. Start the full stack (docker compose — primary local runtime)
docker compose up -d

# Apply DB migrations
PG=$(docker ps --format '{{.Names}}' | grep db-1)
for f in apps/api/migrations/*.sql; do
  docker exec -i $PG psql -U postgres agentprovision < "$f"
done

# Web:           https://agentprovision.com (Cloudflare tunnel) or http://localhost:8002
# API:           https://agentprovision.com/api/v1/  or http://localhost:8000
# Luna Client:   https://luna.agentprovision.com       or http://localhost:8009
# Demo login:    test@example.com / password
```

For production-style K8s deployment (Rancher Desktop + Helm), see `./scripts/deploy_k8s_local.sh` and `docs/KUBERNETES_DEPLOYMENT.md`.

**After rotating any secret, recreate services** — `environment:` in `docker-compose.yml` overrides `env_file`, so `docker compose restart` alone is insufficient:

```bash
docker compose up -d --force-recreate api code-worker orchestration-worker mcp-tools
```

### Connect Your Agent
1. **Claude Code**: Integrations -> Claude Code -> run `claude setup-token` -> paste token
2. **Gemini CLI**: Integrations -> Connect Gemini CLI -> follow link -> paste code
3. Chat via web, WhatsApp, or Luna desktop — Luna responds via your subscription
4. Every response auto-scored and logged for RL improvement

## Luna OS Roadmap

Luna is evolving from a chat client into an AI-first native operating system.

| Phase | Goal | Status |
|-------|------|--------|
| **Phase 0** | Consolidate the brain (AgentProvision as system of record) | **Done** |
| **Phase 1** | Desktop presence (menu bar, shortcuts, notifications, screenshot) | **Done** |
| **Phase 2** | Memory-led native (episodic recall, cross-device continuity) | **Done** |
| **Phase 3** | Spatial workstation (transparent Tauri window, knowledge nebula, A2A visuals, hand tracking) | **Done 2026-04-13** |
| **Phase 4** | Voice-first interaction (native cpal PTT, WAV encoding, voice context) | **Done 2026-04-19** |
| **Phase 5** | Device bridge (camera, IoT registry, desk sensors) | **Done 2026-04-19** |
| **Phase 6** | Mobile companion (iOS/Android, BLE wearable relay) | Planned |
| **Phase 7** | Local actions (automations, file ops, system commands with trust gates) | Planned |
| **Phase 8** | Embodied devices (desk robot, ambient capture) | Planned |

See `docs/plans/2026-03-29-luna-native-operating-system-plan.md` and `docs/plans/2026-04-12-spatial-knowledge-exploration-design.md`.

## Stack

FastAPI · React 18 · Tauri 2.0 (Rust + cpal) · Three.js + Framer Motion · PostgreSQL + pgvector · Temporal · Redis · FastMCP · Ollama (Gemma 4) · Neonize (WhatsApp) · Cloudflare Tunnel · Docker Compose (local) / Helm on Rancher Desktop (prod-path) · nomic-embed-text-v1.5

## Documentation

| Where | What |
|-------|------|
| [`CLAUDE.md`](CLAUDE.md) | Full architecture, API structure, models, services, dev commands, patterns. Source of truth. |
| [`docs/changelog/`](docs/changelog/) | Weekly digests of shipped features |
| [`docs/plans/`](docs/plans/) | Design docs and implementation plans (per feature, dated) |
| [`docs/report/`](docs/report/) | Security audits, pentest verifications, system health reports |
| [`docs/KUBERNETES_DEPLOYMENT.md`](docs/KUBERNETES_DEPLOYMENT.md) | Full K8s deployment runbook |

**Recent highlights:**
- [`docs/changelog/2026-04-12-to-2026-04-19.md`](docs/changelog/2026-04-12-to-2026-04-19.md) — most recent week
- [`docs/plans/2026-04-18-agent-lifecycle-management-platform-plan.md`](docs/plans/2026-04-18-agent-lifecycle-management-platform-plan.md) — ALM design
- [`docs/plans/2026-04-12-a2a-collaboration-demo-design.md`](docs/plans/2026-04-12-a2a-collaboration-demo-design.md) — A2A coalitions
- [`docs/report/2026-04-18-full-security-audit.md`](docs/report/2026-04-18-full-security-audit.md) — security findings
- [`docs/report/2026-04-18-pentest-verification.md`](docs/report/2026-04-18-pentest-verification.md) — black-hat verification

---

*Built with Claude Code CLI . Codex CLI . Gemini CLI . MCP . Temporal . Ollama . pgvector . Neonize . Cloudflare . FastAPI . React . Tauri*
