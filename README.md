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
|  | (Python, zero |  | (skill->config,|  | (local Qwen, 6-dim  |  |
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
|  | qwen2.5-coder    |  | Extraction    |  | (no sub needed)  |        |
|  | 6-dim rubric->RL |  | + Triage      |  | Luna persona     |        |
|  +------------------+  +---------------+  +------------------+        |
+-----------------------------------------------------------------------+
```

## Luna Memory System (v2)

Luna remembers like a person, not a database. Seven memory modules work together across a hybrid semantic + keyword recall engine with pgvector.

| Module | What Luna does | How |
|--------|---------------|-----|
| **Hybrid Recall** | Finds relevant knowledge for every message | pgvector cosine search + keyword boost + session boost |
| **Source Attribution** | Remembers WHERE she learned things | `"Phoebe desk robot (from chat Mar 27)"` |
| **Contradiction Detection** | Flags conflicting facts | `"Phoebe was 'contact' but new info says 'product'"` |
| **Episodic Memory** | Remembers conversation stories, not just entities | `"Yesterday we discussed desk robots, you liked Phoebe"` |
| **Emotional Memory** | Remembers how you FEEL about things | Sentiment tags: `[excited]`, `[frustrated]`, `[curious]` |
| **Anticipatory Context** | Knows time of day and upcoming events | `"Good morning! You have Sprint Planning at 10:30 AM"` |
| **Active Forgetting** | Prunes noise, merges duplicates nightly | Health scoring + 30-day archival + duplicate merge |
| **User Preferences** | Learns your communication style | Response length, tone, format from RL feedback patterns |

**Memory recall pipeline** (every message):
1. Embed user message (nomic-embed-text-v1.5, 768-dim)
2. Semantic search: top 10 entities + top 5 memories + top 3 episodes
3. Keyword boost (+0.3 for name matches) + session boost (+0.2 for recent turns)
4. Memory decay (time-weighted, SQL-side during top-K selection)
5. Fetch observations with source attribution + sentiment per entity
6. Check for contradictions (disputed world state assertions)
7. Inject time context + upcoming calendar events
8. Inject user preferences (learned from RL feedback)
9. All injected into CLAUDE.md as structured sections

**Dream cycle** (nightly via Temporal):
- Consolidate RL patterns into policy weights
- Generate dream insights + synthesize agent memories
- Prune stale entities (health < 0.1, age > 30d)
- Merge duplicate entities (same name + type, different case)
- Learn user preferences from response quality patterns

## Luna Presence System

Luna has a visual identity across the platform. Her face reacts to what she's doing in real-time.

| State | Trigger | Visual |
|-------|---------|--------|
| idle | Response delivered | `~` faint glow |
| listening | WhatsApp inbound | `((·))` blue pulse |
| thinking | CLI dispatch | `? ...` amber shimmer |
| responding | Response received | `> _ <` green glow |
| happy | Score >= 85 or thumbs up | Hearts + stars, golden |
| focused | Code task | `</>` cool blue |
| alert | High-priority notification | `!! triangle !!` amber flash |
| error | CLI failure | `#!@%&` red glitch |
| sleep | 30 min idle | `z Z z` dim |
| empathetic | Failed response | Heart, warm pink |
| playful | Casual message | `~ stars ^_^ stars ~` purple |

Tamagotchi-style chat header: 200px animated avatar with ambient glow, session-scoped presence state, adaptive polling (3s active / 10s idle).

## Auto Quality Scoring & RL

Every agent response is automatically scored by a local Qwen model across 6 dimensions:

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
| Response quality scoring | qwen2.5-coder:1.5b | Manual review |
| Conversation summarization | qwen2.5-coder:0.5b | Anthropic API calls |
| Knowledge extraction | qwen2.5-coder:1.5b | Anthropic API calls |
| Email/calendar triage | qwen2.5-coder:1.5b | Anthropic API calls |
| Competitor analysis | qwen2.5-coder:1.5b | Anthropic API calls |
| Message intent classification | qwen2.5-coder:0.5b | LLM-based routing |
| Free-tier fallback (Luna) | qwen2.5-coder:1.5b | Error message |
| MCP tool calling (planned) | qwen3:4b | None — new capability |

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

### Memory v2 Milestones (all shipped)

| Phase | Modules | PRs |
|-------|---------|-----|
| Phase 1 | Source Attribution + Contradiction Detection | #80 |
| Phase 2 | Episodic Memory (conversation stories) | #81, #85 |
| Phase 3 | Active Forgetting + User Preferences | #87 |
| Phase 4 | Emotional Memory + Anticipatory Context | #90 |

### Presence System (shipped)

| Component | PRs |
|-----------|-----|
| Presence API + LunaAvatar + state integration | #70 |
| Tamagotchi chat header (200px) | #73, #74 |
| All 13 states wired + bigger emotes | #75 |
| Luna on landing page hero | #76 |

See `docs/plans/` for full design documents and implementation plans.

## Stack

FastAPI . React 18 . Tauri 2.0 (Rust) . PostgreSQL + pgvector . Temporal . FastMCP . Ollama (Qwen) . Neonize (WhatsApp) . Cloudflare Tunnel . Docker Compose . nomic-embed-text-v1.5

## Documentation

See `CLAUDE.md` for full architecture, API structure, development commands, and patterns.

---

*Built with Claude Code CLI . Codex CLI . Gemini CLI . MCP . Temporal . Ollama . pgvector . Neonize . Cloudflare . FastAPI . React . Tauri*
