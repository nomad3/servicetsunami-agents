<h1 align="center">ServiceTsunami</h1>

<p align="center"><strong>The Orchestration Layer for AI Agents</strong></p>

<p align="center">
  <a href="https://agentprovision.com"><img src="https://img.shields.io/badge/live-agentprovision.com-00d2ff?style=flat-square" alt="Production"></a>
  <a href="#"><img src="https://img.shields.io/badge/Phase_1-Claude%20Code%20CLI-blueviolet?style=flat-square" alt="Phase 1"></a>
  <a href="#"><img src="https://img.shields.io/badge/Phase_2-Gemini%20CLI%20%2B%20Codex-grey?style=flat-square" alt="Phase 2"></a>
  <a href="#"><img src="https://img.shields.io/badge/MCP_Tools-77-ff6b6b?style=flat-square" alt="MCP Tools"></a>
  <a href="#"><img src="https://img.shields.io/badge/skills-92%2B%20marketplace-green?style=flat-square" alt="Skill Marketplace"></a>
  <a href="#"><img src="https://img.shields.io/badge/tunnel-Cloudflare-4285F4?style=flat-square" alt="Cloudflare"></a>
</p>

<p align="center">
  Don't build agents — orchestrate them. ServiceTsunami routes tasks to the best existing AI agent platform (Claude Code, Gemini CLI, Codex), serves 77 MCP tools, maintains a shared knowledge graph, and learns which platform performs best via RL. Each tenant uses their own subscription — zero API credits.
</p>

---

## Vision

AI agent platforms (Claude Code, Gemini CLI, Codex) already handle context windows, memory, tool calling, and code execution. Building custom agents on top of LLM APIs is expensive and fragile.

**ServiceTsunami is the layer above.** We handle what the platforms don't: multi-tenancy, integrations (Gmail, Calendar, Jira, GitHub), a persistent knowledge graph, a skill marketplace, RL-driven routing, WhatsApp gateway, and enterprise orchestration.

| | Custom Agents (old) | CLI Orchestration (new) |
|---|---|---|
| LLM calls | API credits ($$$) | Subscription plans ($) |
| Context windows | Manual management | Platform handles it |
| Memory | Custom implementation | Native CLI sessions |
| Tool calling | Custom framework | MCP standard |
| Code execution | Sandboxed, limited | Full dev environment |

## Roadmap

| Phase | Platform | Status |
|-------|----------|--------|
| **Phase 1** | Claude Code CLI (Opus 4.6) | **Live** — 77 MCP tools, Temporal, WhatsApp, dev workflow |
| **Phase 2** | Gemini CLI | Planned — free tier, 1M context, MCP support confirmed |
| **Phase 3** | Codex CLI (OpenAI) | Planned — ChatGPT subscription, MCP support TBD |
| **Phase 4** | RL-driven routing | Planned — learns best platform per task type from feedback |

## Architecture

```
Internet → Cloudflare Tunnel
  ├── servicetsunami.com
  └── agentprovision.com

┌────────────────────────────────────────────────────────────┐
│  Channels: WhatsApp (Neonize) · Web Chat · API             │
└──────────────────────┬─────────────────────────────────────┘
                       │
┌──────────────────────▼─────────────────────────────────────┐
│  FastAPI Backend                                            │
│  ┌──────────────┐  ┌────────────────┐  ┌────────────────┐  │
│  │ Agent Router  │  │ Session Manager│  │ RL Engine      │  │
│  │ (Python, no   │  │ (skill→config, │  │ (learns best   │  │
│  │  LLM cost)    │  │  resume/retry) │  │  platform/task)│  │
│  └──────┬────────┘  └────────────────┘  └────────────────┘  │
└─────────┼───────────────────────────────────────────────────┘
          │
┌─────────▼───────────────────────────────────────────────────┐
│  Temporal Workers                                            │
│                                                              │
│  ┌─────────────────┐  ┌──────────────┐  ┌────────────────┐  │
│  │ Claude Code CLI  │  │ Gemini CLI   │  │ Codex CLI      │  │
│  │ (Phase 1 - LIVE) │  │ (Phase 2)    │  │ (Phase 3)      │  │
│  │ Opus 4.6         │  │ Free/Pro     │  │ ChatGPT plan   │  │
│  │ Subscription     │  │ Subscription │  │ Subscription   │  │
│  └────────┬─────────┘  └──────┬───────┘  └───────┬────────┘  │
│           └──────────────────┼────────────────────┘          │
│                              │                               │
│                    ┌─────────▼─────────┐                     │
│                    │  MCP Tool Server   │                     │
│                    │  77 tools (FastMCP)│                     │
│                    └───────────────────┘                     │
└──────────────────────────────────────────────────────────────┘
```

## 77 MCP Tools

All tools served via Anthropic's MCP protocol (FastMCP, Streamable HTTP). Any MCP-compatible CLI agent connects instantly.

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
| **Connectors** | query data sources | 1 |

## Skill & Agent Marketplace

Agents and tools share the same three-tier marketplace:

```bash
# Import 92 Google Workspace skills
POST /api/v1/skills/library/import-github
{"repo_url": "https://github.com/googleworkspace/cli/tree/main/skills"}

# Import MCP tools from community
POST /api/v1/tools/import-github
{"repo_url": "https://github.com/modelcontextprotocol/servers/tree/main/src/slack"}
```

**Native** (bundled) | **Community** (GitHub imports) | **Custom** (per-tenant, versioned)

Agents are skills with `engine: agent` and `platform_affinity: claude_code | gemini_cli | codex_cli`.

## Development Workflow

Luna (the default agent) has full dev capabilities:

```
User: "Fix the login button color"
Luna:
  1. cd /workspace && git checkout -b fix/login-button-color
  2. Edits the file using Claude Code's Edit tool
  3. git commit -m "fix: update login button color"
  4. git push origin fix/login-button-color
  5. gh pr create --title "fix: update login button color"
  6. Returns PR URL to user
```

GitHub token fetched from OAuth vault per-session. No hardcoded credentials.

## One-Click Platform Auth

Each CLI platform uses subscription-based OAuth — zero API credits:

| Platform | Auth | Status |
|----------|------|--------|
| Claude Code | `claude setup-token` → vault | **Live** |
| GitHub | OAuth via agentprovision.com | **Live** |
| Gmail/Calendar | Google OAuth with auto-refresh | **Live** |
| Gemini CLI | Google OAuth (extends existing) | Phase 2 |
| Codex | ChatGPT OAuth | Phase 3 |

## Quick Start

```bash
git clone https://github.com/nomad3/servicetsunami-agents.git
cd servicetsunami-agents
DB_PORT=8003 API_PORT=8001 WEB_PORT=8002 docker-compose up --build

# Web:         http://localhost:8002
# API:         http://localhost:8001
# MCP Tools:   http://localhost:8087
# Temporal UI: http://localhost:8233
```

### Connect Your Agent
1. **Integrations** → Claude Code → run `claude setup-token` → paste token
2. **Settings** → enable CLI Orchestrator
3. Chat via web or WhatsApp — Luna responds via your subscription

## Design Documents

| Document | Description |
|----------|-------------|
| `docs/plans/2026-03-15-cli-orchestration-pivot-design.md` | Full architecture spec |
| `docs/plans/2026-03-15-cli-orchestration-pivot-plan.md` | Phase 1 implementation (10 tasks) |
| `docs/plans/2026-03-16-adk-removal-plan.md` | ADK deprecation steps |
| `docs/plans/2026-03-16-cloudflare-tunnel-design.md` | Cloudflare Tunnel setup |

---

*Built with Claude Code CLI · Gemini CLI (soon) · Codex (soon) · MCP · Temporal · pgvector · Neonize · Cloudflare · FastAPI · React*
