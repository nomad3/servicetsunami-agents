<h1 align="center">AgentProvision / AgentProvision</h1>

<p align="center"><strong>The Orchestration Layer for AI Agents</strong></p>

```
    +---------------------------------------------------------------+
    |  Live           agentprovision.com                            |
    |  Orchestrates   Claude Code | Codex CLI | Gemini CLI |        |
    |                 GitHub Copilot CLI | OpenCode                 |
    |  Surfaces       Luna desktop (Tauri 2.0)                      |
    |                 alpha CLI   (terminal, see docs/cli/)         |
    |                 web SPA     WhatsApp     Microsoft Teams      |
    |  Capabilities   90+ MCP tools                                 |
    |                 92+ marketplace skills                        |
    |                 26 dynamic workflow templates                 |
    |                 RL auto-scoring on every reply                |
    |                 Cloudflare tunnel                             |
    +---------------------------------------------------------------+
```

<p align="center">
  Don't build agents — orchestrate them. AgentProvision routes tasks to existing AI agent platforms (Claude Code, Codex, Gemini CLI, GitHub Copilot CLI), serves 90+ MCP tools, maintains a knowledge graph, auto-scores every response with a local LLM, and learns which platform performs best via RL. Enterprise-grade <b>Agent Lifecycle Management</b> with versioning, audit, rollback, and governance. Each tenant uses their own subscription — zero API credits.
</p>

> **Latest (2026-05-15 → 2026-05-16):** **Alpha Control Center** — `/dashboard` is now a VSCode/Cursor-style IDE shell that replaces the prior separate `/chat` page. Three-pane resizable layout (sessions/files · chat groups · agent activity), horizontal resize handle, docked live terminal, inline CLI picker, ⌘K palette, ⚡ A2A trigger, editor-group splits (up to 4), single shared SSE via `SessionEventsContext`, workspace file tree (tenant + platform scopes) with file viewer, full CLI subprocess output streaming, gated end-of-deploy emergency disk cleanup (PRs #495–#518). The **Alpha CLI is the kernel** — every feature flows through it. See [`docs/architecture/dashboard.md`](docs/architecture/dashboard.md) and [`docs/architecture/alpha_cli_kernel.md`](docs/architecture/alpha_cli_kernel.md).
>
> **Previously (2026-04-19 → 2026-05-03):** Skills Marketplace v2 (`_bundled/` + `_tenant/<uuid>/` layout, Claude-Code-format SKILL.md, library_revisions audit), External Agents + A2A v2 (Microsoft Copilot Studio + Azure AI Foundry import, Workflows-as-spine, no `agent_messages` table), Microsoft Teams channel via Graph + `TeamsMonitorWorkflow`, **GitHub Copilot CLI** runtime, autodetect CLI + quota fallback chain, greeting fast-path latency win (~130×–397× on warm path), internal endpoints blocked from public internet (#207), routing transparency footer in chat. See [`docs/changelog/2026-04-19-to-2026-05-03.md`](docs/changelog/2026-04-19-to-2026-05-03.md) for the full digest.
>
> **Earlier (2026-04-12 → 2026-04-19):** Agent Lifecycle Management Platform, A2A Collaboration, Luna OS Spatial Workstation, redesigned landing page, security hardening. See [`docs/changelog/2026-04-12-to-2026-04-19.md`](docs/changelog/2026-04-12-to-2026-04-19.md). (Native cpal PTT was designed in #154 but is not currently in the tree.)

---

## Alpha Control Center — `/dashboard`

The user-facing surface for the platform. Mounted at `/dashboard` (`apps/web/src/pages/DashboardControlCenter.js`) and replaces the prior separate `/chat` page. VSCode/Cursor-style IDE shell — conversation-first, but laid out like an editor.

```
┌──────────────────────────────────────────────────────────────────────┐
│ TitleBar · session title · ⚡ A2A · ⌘K · Pro/Simple · user ▾         │
├────────────┬─────────────────────────────────────┬───────────────────┤
│ Left card  │ EditorArea (1..4 chat groups)       │ AgentActivityPanel│
│ Chats │    │ side-by-side splits; focused group  │ live v2 SSE feed  │
│ Files      │ takes new sessions from left rail   │ (Pro mode only)   │
├────────────┴─────────────────────────────────────┴───────────────────┤
│  ⇕  horizontal resize handle                                         │
├──────────────────────────────────────────────────────────────────────┤
│  TerminalCard — auto-opens on first cli_subprocess_stream            │
│  Tabs per CLI: claude_code · codex · gemini_cli · copilot · …         │
└──────────────────────────────────────────────────────────────────────┘
```

| Surface | What |
|---|---|
| **Left card modes** | `Chats` (sessions) ↔ `Files` (workspace tree, tenant + platform scopes). Toggle persists in `apControl.leftMode`. |
| **File tree** | Lazy-loaded. Tenant scope → `/var/agentprovision/workspaces/<tenant_id>/`. Platform scope → `/opt/agentprovision/platform-docs/` (superusers). Backend: `GET /api/v1/workspace/{tree,file}`. |
| **Editor groups** | Up to 4 chat panes side-by-side, each with its own active session. Focus = 2 px inset brand-primary border. Sidebar click → focused group. |
| **Inline CLI picker** | Pill widget in the chat thread header writes `tenant_features.default_cli_platform` via `brandingService` — tenant-wide effect. Replaces the old "Open in full chat" link. |
| **Pro / Simple toggle** | Simple hides AgentActivityPanel + TerminalCard. |
| **⚡ A2A trigger** | Dispatch Plan/Verify, Propose/Critique/Revise, incident_investigation, etc. patterns. |
| **⌘K palette** | Unified search across sessions, agents, static nav. |
| **Live agent activity** | SSE-streamed `cli_subprocess_*`, `cli_routing_decision`, `auto_quality_consensus`, `plan_step_changed`, `subagent_dispatched/response`, `tool_call_*`, `chat_message`. Single shared subscription via `SessionEventsContext` — no per-pane fan-out. |
| **Live terminal** | Auto-opens on first `cli_subprocess_stream`; tabs per CLI platform; renders the **full transcript** (reasoning, tool calls, edits) — not just start/end. |
| **Workspace volume** | Named `workspaces` volume in docker-compose; PVC template in `helm/charts/microservice/` guarded by `workspaces.enabled=true` in `helm/values/agentprovision-api.yaml`. 10 GiB default (PR #515). |

Full architecture: [`docs/architecture/dashboard.md`](docs/architecture/dashboard.md). Layout / pane composition / terminal-stream design docs live under [`docs/plans/2026-05-1{5,6}-*.md`](docs/plans/).

### Workspace persistence

Every tenant gets a durable filesystem subtree mounted into both the `api` and `code-worker` services. Files Luna writes (memory, plans, project notes) survive container restarts, image rebuilds, and deploys — and the dashboard Files tab + `alpha workspace …` verbs share the same view of disk.

- Named volume `agentprovision-agents_workspaces` mounted at `/var/agentprovision/workspaces` on **both** `api` and (post PR #517) `code-worker`. Per-tenant subdirectory `/<tenant_id>/` is auto-seeded on first `GET /api/v1/workspace/tree` with `docs/plans/`, `memory/`, `projects/`, and a starter `README.md`.
- Persists across `docker compose restart`, image rebuilds, deploys, and node reboots. Only `docker volume rm` (or `kubectl delete pvc`) wipes it — **never** `docker volume prune`. Production-path Helm PVC at `helm/charts/microservice/templates/workspaces-pvc.yaml`, 10 GiB default, gated on `workspaces.enabled=true`.
- Three kernel verbs: `alpha workspace tree`, `alpha workspace read`, `alpha workspace clone` (new — 2026-05-16, parallel impl). Each maps 1:1 to a thin `/api/v1/workspace/…` route; path-segment guards reject `.git/`, `__pycache__/`, etc. even on direct access; platform scope is superuser-gated; 256 KiB read cap.
- `alpha workspace clone owner/repo` runs `git clone` inside `code-worker` and emits a `workspace_repo_cloned` SSE event the dashboard tree picks up. Memory + workstation ↔ cloud sync is tracked as task #256.

Full doc: [`docs/architecture/workspace.md`](docs/architecture/workspace.md).

### Alpha CLI is the kernel

Every feature flows through `alpha`. Frontend → CLI (kernel) → internal API → MCP tools / memory / RL. The web `/dashboard`, Tauri, WhatsApp, and the `alpha` binary are **viewports**, not implementations. If a new feature can't be expressed as `alpha <verb>`, the design is wrong.

See [`docs/architecture/alpha_cli_kernel.md`](docs/architecture/alpha_cli_kernel.md).

---

## Luna — Native AI Client

Luna is the native presence layer for AgentProvision. A 4.9MB Tauri 2.0 desktop app that lives in your macOS menu bar.

| Feature | Status |
|---------|--------|
| Native macOS ARM64 app (Tauri 2.0 + Rust) | Shipped |
| System tray with show/hide toggle | Shipped |
| Cmd+Shift+Space global shortcut (Raycast-style) | Shipped |
| Cmd+Shift+Space command palette (`tauri-plugin-global-shortcut`) | Shipped |
| Voice input via browser MediaRecorder → `/api/v1/media/transcribe` | Shipped (native cpal PTT designed in #154 not currently in tree) |
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

## `alpha` — Terminal AI Client

`alpha` is the terminal-native counterpart to Luna. Same FastAPI backend, same agents, same skills — but scriptable (`--json`), CI-friendly (`--no-stream`), with OS-keychain token storage and a 30-minute JWT. Cross-platform (macOS arm64/x86_64, Linux x86_64, Windows x86_64). Auto-updates via `alpha upgrade`. Source under [`apps/agentprovision-cli`](apps/agentprovision-cli/) and [`apps/agentprovision-core`](apps/agentprovision-core/).

```bash
# Install (macOS / Linux)
curl -fsSL https://agentprovision.com/install.sh | sh
# Install (Windows PowerShell)
iwr https://agentprovision.com/install.ps1 | iex

alpha login                                          # password flow, token in keychain
alpha status --runtimes                              # auth + preflight all local CLI runtimes
alpha chat send "what shipped this week?"            # streaming reply, like Claude Code
alpha workflow run incident_investigation --json     # dispatch a dynamic workflow
```

| Command surface | Status |
|------|--------|
| `alpha login` / `logout` / `status` (`--runtimes`) | Shipped |
| `alpha chat send` / `repl` (streaming + REPL) | Shipped |
| `alpha agent` (list, get, create, promote, rollback) | Shipped |
| `alpha workflow` (list templates, run, status) | Shipped |
| `alpha session` / `sessions` (list, show, resume) | Shipped |
| `alpha memory` (search, recall, record) | Shipped |
| `alpha skill` (list, run, install) | Shipped |
| `alpha integration` (list, connect, status) | Shipped |
| `alpha upgrade` / `completions` / `quickstart` | Shipped |
| `alpha run` / `watch` / `cancel` — durable runs, terminal-close-safe (Phase 1 wedge, PRs #434/#436/#438) | Shipped |
| `alpha run --providers ...` / `--fanout` / `--merge` — multi-provider parallel + consensus | Shipped (backend `/api/v1/tasks-fanout/` flagged prototype) |
| `alpha recall` / `remember` — explicit memory ingest + recall (Phase 2) | Shipped |
| `alpha policy show` — per-agent governance read-out, policy enforcement in `run` (Phase 2) | Shipped |
| `alpha coalition list` / `run` / `watch` — A2A coalitions from the terminal (Phase 3) | Shipped |
| `alpha recipes list` / `describe` / `run` / `uninstall` — goal-recipe runtime (Phase 3) | Shipped |
| `alpha usage` / `costs` — per-provider tokens, per-day rollup, `--by team` (Phase 4) | Shipped |
| `alpha recipes publish` — community recipe contribution (Phase 5) | Planned |

`alpha` is not competing with `claude` / `codex` / `gemini` / `gh copilot` — it orchestrates them. The differentiation roadmap ([`docs/plans/2026-05-13-ap-cli-differentiation-roadmap.md`](docs/plans/2026-05-13-ap-cli-differentiation-roadmap.md)) covers eight CLI surfaces no leaf CLI offers: durable runs, fanout/consensus, cost attribution, team RBAC, A2A coalitions, memory-aware sessions, governance policies, RL-routed model selection. Phases 1–4 are in code on main; Phase 5 (`recipes publish`) remains future.

Full reference: [`docs/cli/README.md`](docs/cli/README.md).

---

## Architecture

```
Internet ─▶ Cloudflare Tunnel
  ├─▶ agentprovision.com                (web + API)
  └─▶ luna.agentprovision.com           (Luna PWA / Tauri client)

┌─────────────────────────────────────────────────────────────────────┐
│  Channels: WhatsApp · Web · Luna Desktop · API · Microsoft Teams    │
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
| **Governance** | Distributed (not a single table — `agent_policies` was removed in P0b 2026-05-23 as dead infra): tool permissions via `agent.tool_groups` + MCP scope check; content gating via `platform_safety_io` safety floor; rate limits via `core.rate_limit.limiter`; declared values via AgentValueSet; cross-source arbitration via Value Arbitration. See `docs/plans/2026-05-23-p0b-agent-policy-decision.md`. |
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

## Skills Marketplace v2

File-based skills laid out across two folders on a shared volume. Shipped 2026-04-26 (PRs #182–#193).

```
skills/
├── _bundled/              # read-only, ships with the container
│   └── <skill-slug>/
│       ├── SKILL.md       # Claude-Code-format frontmatter + instructions
│       ├── script.py      # optional, engine: python
│       ├── script.sh      # optional, engine: shell
│       └── prompt.md      # optional, engine: markdown
└── _tenant/
    └── <tenant-uuid>/     # custom + community-imported skills
        └── <skill-slug>/...
```

- **Audit log** — every change writes a `library_revisions` row (migration 110).
- **Code-worker access** — via the `read_library_skill` MCP tool. The library is intentionally **not** mounted into the worker pod.
- **MCP tools** — `update_skill`, `update_agent`, `read_library_skill` for full CRUD from any CLI runtime.
- **Auto-trigger** — pgvector embeddings power semantic match-on-context.

---

## External Agents + A2A v2

Shipped 2026-04-26 (PRs #194–#205). Workflows are the audit + dispatch spine — no separate `agent_messages` table.

| Capability | How |
|------------|-----|
| OpenAI Assistants, MCP servers, webhooks | `external_agents` row + `external_agent_adapter.py` |
| Microsoft Copilot Studio | `GET /agents/microsoft/discover` enumerates Copilot Studio + AI Foundry → `POST /agents/import` adopts on Copilot CLI runtime |
| CrewAI / LangChain / AutoGen import | `agent_importer.py` config translator |
| Reliability shim | Mirrors Temporal RetryPolicy across native + external calls |
| Handoffs | `ChatMessage(context.kind="handoff")` + `WorkflowRun` (no new table) |
| Patterns | Ship as `workflow_templates` JSON (incident_investigation, deal_brief, cardiology_case_review) |
| Resolution | `_call_agent` resolves UUIDs to native or external dispatch |

**Hire wizard** (#205) unifies native + external + marketplace + import in a single onboarding flow.

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
| GitHub Copilot CLI | OAuth token via vault, JSON output mode | **Live runtime (#244, 2026-04-26)** |
| GitHub | OAuth via agentprovision.com (multi-account aware) | **Live** |
| Gmail / Calendar / Drive | Google OAuth with auto-refresh | **Live** |
| Microsoft / Outlook | Microsoft OAuth | **Live** |
| Microsoft Teams | Microsoft Graph (reuses microsoft OAuth) + `TeamsMonitorWorkflow` | **Live (#241/#250, 2026-04-26)** |
| Jira | Basic Auth | **Live** |
| Copilot Studio (DirectLine) | Per-request token passthrough | **Live** |
| Microsoft Copilot Studio + Azure AI Foundry agents | `GET /agents/microsoft/discover` → adopt on Copilot CLI runtime | **Live (#243/#251, 2026-04-26)** |

## Quick Start

```bash
git clone https://github.com/nomad3/agentprovision-agents.git
cd agentprovision-agents

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
1. **Terminal (`alpha`)**: `curl -fsSL https://agentprovision.com/install.sh | sh && alpha login && alpha quickstart`
2. **Claude Code**: Integrations -> Claude Code -> run `claude setup-token` -> paste token
3. **Gemini CLI**: Integrations -> Connect Gemini CLI -> follow link -> paste code
4. Chat via web, WhatsApp, Luna desktop, or `alpha chat repl` — every channel hits the same agents
5. Every response auto-scored and logged for RL improvement

## Luna OS Roadmap

Luna is evolving from a chat client into an AI-first native operating system.

| Phase | Goal | Status |
|-------|------|--------|
| **Phase 0** | Consolidate the brain (AgentProvision as system of record) | **Done** |
| **Phase 1** | Desktop presence (menu bar, shortcuts, notifications, screenshot) | **Done** |
| **Phase 2** | Memory-led native (episodic recall, cross-device continuity) | **Done** |
| **Phase 3** | Spatial workstation (transparent Tauri window, knowledge nebula, A2A visuals, hand tracking) | **Done 2026-04-13** |
| **Phase 4** | Voice-first interaction (browser MediaRecorder today; native cpal PTT designed in #154 not currently in tree) | Browser path live; native deferred |
| **Phase 5** | Device bridge (camera, IoT registry, desk sensors) | **Done 2026-04-19** |
| **Phase 6** | Mobile companion (iOS/Android, BLE wearable relay) | Planned |
| **Phase 7** | Local actions (automations, file ops, system commands with trust gates) | Planned |
| **Phase 8** | Embodied devices (desk robot, ambient capture) | Planned |

See `docs/plans/2026-03-29-luna-native-operating-system-plan.md` and `docs/plans/2026-04-12-spatial-knowledge-exploration-design.md`.

## Stack

FastAPI · React 18 · Tauri 2.0 (Rust) · Three.js + Framer Motion · PostgreSQL + pgvector · Temporal · Redis · FastMCP · Ollama (Gemma 4) · Neonize (WhatsApp) · Cloudflare Tunnel · Docker Compose (local) / Helm on Rancher Desktop (prod-path) · nomic-embed-text-v1.5

## Documentation

| Where | What |
|-------|------|
| [`CLAUDE.md`](CLAUDE.md) | Full architecture, API structure, models, services, dev commands, patterns. Source of truth. |
| [`docs/architecture/dashboard.md`](docs/architecture/dashboard.md) | Alpha Control Center — `/dashboard` IDE shell, panes, height chain, localStorage map |
| [`docs/architecture/alpha_cli_kernel.md`](docs/architecture/alpha_cli_kernel.md) | "Every feature through Alpha CLI" — design principle, examples, anti-patterns |
| [`docs/changelog/`](docs/changelog/) | Weekly digests of shipped features |
| [`docs/plans/`](docs/plans/) | Design docs and implementation plans (per feature, dated) |
| [`docs/report/`](docs/report/) | Security audits, pentest verifications, system health reports |
| [`docs/KUBERNETES_DEPLOYMENT.md`](docs/KUBERNETES_DEPLOYMENT.md) | Full K8s deployment runbook |
| [`docs/cli/README.md`](docs/cli/README.md) | `alpha` CLI reference — login, chat, workflow, memory, skill, integration |

**Recent highlights:**
- [`docs/architecture/dashboard.md`](docs/architecture/dashboard.md) — Alpha Control Center (`/dashboard`) IDE shell — VSCode-style layout, workspace file tree, inline CLI picker, editor-group splits, single-SSE pattern (2026-05-15 → 2026-05-16, PRs #495–#518)
- [`docs/plans/2026-05-15-alpha-control-center-ide-shell-design.md`](docs/plans/2026-05-15-alpha-control-center-ide-shell-design.md) — IDE shell design (canonical UI; supersedes /den)
- [`docs/plans/2026-05-16-dashboard-split-pane-spec-doc-viewer.md`](docs/plans/2026-05-16-dashboard-split-pane-spec-doc-viewer.md) — pane composition + doc viewer
- [`docs/plans/2026-05-16-terminal-full-cli-output.md`](docs/plans/2026-05-16-terminal-full-cli-output.md) — full CLI transcript in TerminalCard
- [`docs/plans/2026-05-16-codex-mcp-tool-access-fix.md`](docs/plans/2026-05-16-codex-mcp-tool-access-fix.md) — Codex MCP-over-SSE enablement
- [`docs/changelog/2026-04-19-to-2026-05-03.md`](docs/changelog/2026-04-19-to-2026-05-03.md) — prior fortnight (Skills v2, External Agents v2, Teams, Copilot CLI runtime, latency)
- [`docs/changelog/2026-04-12-to-2026-04-19.md`](docs/changelog/2026-04-12-to-2026-04-19.md) — prior week (ALM, A2A, Spatial HUD)
- [`docs/plans/2026-04-26-external-agents-and-a2a-enhancement-plan.md`](docs/plans/2026-04-26-external-agents-and-a2a-enhancement-plan.md) — External Agents + A2A v2 design
- [`docs/plans/2026-04-26-skills-fleet-alignment-plan.md`](docs/plans/2026-04-26-skills-fleet-alignment-plan.md) — Skills Marketplace v2
- [`docs/plans/2026-04-23-luna-latency-reduction-plan.md`](docs/plans/2026-04-23-luna-latency-reduction-plan.md) — latency campaign (greeting fast-path, prompt trim, KV cache probe)
- [`docs/plans/2026-04-25-luna-hallucination-reduction-plan.md`](docs/plans/2026-04-25-luna-hallucination-reduction-plan.md) — hallucination reduction
- [`docs/plans/2026-04-18-agent-lifecycle-management-platform-plan.md`](docs/plans/2026-04-18-agent-lifecycle-management-platform-plan.md) — ALM design
- [`docs/plans/2026-04-12-a2a-collaboration-demo-design.md`](docs/plans/2026-04-12-a2a-collaboration-demo-design.md) — A2A coalitions
- [`docs/report/2026-04-18-pentest-verification.md`](docs/report/2026-04-18-pentest-verification.md) — black-hat verification of the security hardening
- [`docs/plans/2026-05-13-ap-cli-differentiation-roadmap.md`](docs/plans/2026-05-13-ap-cli-differentiation-roadmap.md) — eight CLI differentiators planned for `alpha`
- [`docs/plans/2026-05-11-ap-cli-multi-runtime-dispatch-plan.md`](docs/plans/2026-05-11-ap-cli-multi-runtime-dispatch-plan.md) — multi-runtime dispatch for `alpha`

---

*Built with Claude Code CLI . Codex CLI . Gemini CLI . MCP . Temporal . Ollama . pgvector . Neonize . Cloudflare . FastAPI . React . Tauri*
