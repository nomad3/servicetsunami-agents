<h1 align="center">ServiceTsunami / AgentProvision</h1>

<p align="center"><strong>The Orchestration Layer for AI Agents</strong></p>

<p align="center">
  <a href="https://agentprovision.com"><img src="https://img.shields.io/badge/live-agentprovision.com-00d2ff?style=flat-square" alt="Production"></a>
  <a href="#"><img src="https://img.shields.io/badge/agents-Claude%20Code%20%7C%20Codex%20%7C%20Gemini-blueviolet?style=flat-square" alt="Agents"></a>
  <a href="#"><img src="https://img.shields.io/badge/MCP_Tools-81-ff6b6b?style=flat-square" alt="MCP Tools"></a>
  <a href="#"><img src="https://img.shields.io/badge/skills-92%2B%20marketplace-green?style=flat-square" alt="Skill Marketplace"></a>
  <a href="#"><img src="https://img.shields.io/badge/RL-auto%20scoring-orange?style=flat-square" alt="RL"></a>
  <a href="#"><img src="https://img.shields.io/badge/tunnel-Cloudflare-4285F4?style=flat-square" alt="Cloudflare"></a>
  <a href="#"><img src="https://img.shields.io/badge/Luna_Client-Tauri%202.0-24C8DB?style=flat-square" alt="Luna Client"></a>
</p>

<p align="center">
  Don't build agents — orchestrate them. ServiceTsunami routes tasks to existing AI agent platforms (Claude Code, Codex, Gemini CLI), serves 81 MCP tools, maintains a knowledge graph, auto-scores every response with a local LLM, and learns which platform performs best via RL. Each tenant uses their own subscription — zero API credits.
</p>

---

## Luna — Native AI Client

Luna is the native presence layer for AgentProvision. A 4.9MB Tauri 2.0 desktop app that lives in your macOS menu bar.

| Feature | Status |
|---------|--------|
| Native macOS ARM64 app (Tauri 2.0 + Rust) | Shipped |
| System tray with show/hide toggle | Shipped |
| Cmd+Shift+Space global shortcut (Raycast-style) | Shipped |
| Chat with SSE streaming + markdown rendering | Shipped |
| Emotion-reactive LunaAvatar (keyword heuristics) | Shipped |
| Shell heartbeat + device handoff protocol | Shipped |
| Native macOS notifications (Tauri plugin) | Shipped |
| Screenshot capture + upload to Luna for analysis | Shipped |
| Memory panel (episodic recall, cross-shell context) | Shipped |
| Trust model + action approval for local actions | Shipped |
| PWA fallback (installable from browser, offline) | Shipped |
| Docker + Cloudflare Tunnel (`luna.servicetsunami.com`) | Shipped |
| iOS / Android (Tauri mobile builds) | Planned |
| BLE wearable relay (necklace, glasses) | Planned |
| Device bridge (camera, desk robot, IoT) | Planned |
| Voice input (push-to-talk, wake word) | Planned |

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
Internet -> Cloudflare Tunnel
  +--> servicetsunami.com
  +--> agentprovision.com
  +--> luna.servicetsunami.com  (Luna PWA)

+------------------------------------------------------------------+
|  Channels: WhatsApp (Neonize) . Web Chat . Luna Desktop . API     |
+------------------------------+-----------------------------------+
                               |
+------------------------------v-----------------------------------+
|  FastAPI Backend                                                  |
|  +---------------+  +----------------+  +----------------------+  |
|  | Agent Router  |  | Session Manager|  | Auto Quality Scorer  |  |
|  | (Python, zero |  | (skill->config,|  | (local Gemma 4,     |  |
|  |  LLM cost)    |  |  resume/retry) |  |  RL scoring)         |  |
|  +-------+-------+  +----------------+  +----------------------+  |
+---------+------------------------------------------------------------+
          |
+---------v------------------------------------------------------------+
|  Temporal Workers                                                     |
|                                                                       |
|  +------------------+  +---------------+  +------------------+        |
|  | Claude Code CLI  |  | Codex CLI     |  | Gemini CLI       |        |
|  | Opus 4.6         |  | OpenAI        |  | Google           |        |
|  | Subscription     |  | Subscription  |  | Subscription     |        |
|  +--------+---------+  +-------+-------+  +--------+---------+        |
|           +---------------------+---------------------+               |
|                                 |                                     |
|                       +---------v-----------+                         |
|                       |  MCP Tool Server    |                         |
|                       |  81 tools (FastMCP) |                         |
|                       +---------------------+                         |
+-----------------------------------------------------------------------+
          |
+---------v------------------------------------------------------------+
|  Local ML (Ollama -- zero cloud cost)                                 |
|  +------------------+  +---------------+  +------------------+        |
|  | Auto Scoring     |  | Knowledge     |  | Fallback Chat    |        |
|  | gemma4           |  | Extraction    |  | (no sub needed)  |        |
|  | 6-dim rubric->RL |  | + Triage      |  | Luna persona     |        |
|  +------------------+  +---------------+  +------------------+        |
+-----------------------------------------------------------------------+
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

## 81 MCP Tools

All tools served via Anthropic's MCP protocol (FastMCP, Streamable HTTP).

| Category | Tools | Count |
|----------|-------|-------|
| **Knowledge Graph** | create/find/update entity, relations, observations, search, timeline | 11 |
| **Email** | search, read, send, download attachment, deep scan, list accounts | 6 |
| **Calendar** | list events, create event | 2 |
| **Jira** | search/get/create/update issues, list projects | 5 |
| **GitHub** | repos, issues, PRs, file read, code search | 8 |
| **Ads** | Meta/Google/TikTok campaigns, insights, ad libraries | 12 |
| **Data** | SQL queries, datasets, schema, insights | 4 |
| **Sales** | qualify leads, outreach, pipeline, proposals, follow-ups | 6 |
| **Competitor** | add/remove/list, reports, compare campaigns | 5 |
| **Monitor** | inbox start/stop/status, competitor start/stop/status | 6 |
| **Reports** | document extraction, Excel generation | 2 |
| **Analytics** | calculate, compare periods, forecast | 3 |
| **Skills** | list, run, match context, recall memory | 4 |
| **Shell** | execute commands, deploy changes | 2 |
| **Drive** | search, read, list files | 3 |
| **Connectors** | query data sources | 1 |

## Platform Auth

Each CLI platform uses subscription-based OAuth — zero API credits:

| Platform | Auth | Status |
|----------|------|--------|
| Claude Code | OAuth token via vault | **Live** |
| Codex (OpenAI) | auth.json via vault | **Live** |
| Gemini CLI | Google OAuth | Integrated, untested |
| GitHub | OAuth via agentprovision.com | **Live** |
| Gmail/Calendar | Google OAuth with auto-refresh | **Live** |
| Microsoft/Outlook | Microsoft OAuth | Wired |
| Jira | Basic Auth | **Live** |

## Quick Start

```bash
git clone https://github.com/nomad3/servicetsunami-agents.git
cd servicetsunami-agents
DB_PORT=8003 API_PORT=8001 WEB_PORT=8002 docker-compose up --build

# Web:           http://localhost:8002
# API:           http://localhost:8001
# Luna Client:   http://localhost:8009
# MCP Tools:     http://localhost:8087
# Temporal UI:   http://localhost:8233
# Demo login:    test@example.com / password
```

### Connect Your Agent
1. **Integrations** -> Claude Code -> run `claude setup-token` -> paste token
2. Chat via web, WhatsApp, or Luna desktop — Luna responds via your subscription
3. Every response auto-scored and logged for RL improvement

## Luna OS Roadmap

Luna is evolving from a chat client into an AI-first native operating system.

| Phase | Goal | Status |
|-------|------|--------|
| **Phase 0** | Consolidate the brain (AgentProvision as system of record) | Done |
| **Phase 1** | Desktop presence (menu bar, shortcuts, notifications, screenshot) | Done |
| **Phase 2** | Memory-led native (episodic recall, cross-device continuity) | Done |
| **Phase 3** | Mobile companion (iOS/Android, BLE wearable relay) | Planned |
| **Phase 4** | Local actions (automations, file ops, system commands with trust gates) | Planned |
| **Phase 5** | Embodied devices (camera, desk robot, ambient capture) | Planned |

See `docs/plans/2026-03-29-luna-native-operating-system-plan.md` for the full master plan.

## Stack

FastAPI . React 18 . Tauri 2.0 (Rust) . PostgreSQL + pgvector . Temporal . FastMCP . Ollama (Gemma 4) . Neonize (WhatsApp) . Cloudflare Tunnel . Docker Compose . nomic-embed-text-v1.5

## Documentation

See `CLAUDE.md` for full architecture, API structure, development commands, and patterns.

---

*Built with Claude Code CLI . Codex CLI . Gemini CLI . MCP . Temporal . Ollama . pgvector . Neonize . Cloudflare . FastAPI . React . Tauri*
