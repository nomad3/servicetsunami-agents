<h1 align="center">ServiceTsunami</h1>

<p align="center"><strong>AI Agent Orchestration Platform</strong></p>

<p align="center">
  <a href="https://agentprovision.com"><img src="https://img.shields.io/badge/live-agentprovision.com-00d2ff?style=flat-square" alt="Production"></a>
  <a href="#"><img src="https://img.shields.io/badge/CLI_Agents-Claude%20Code%20Opus-blueviolet?style=flat-square" alt="CLI Agents"></a>
  <a href="#"><img src="https://img.shields.io/badge/MCP_Tools-77-ff6b6b?style=flat-square" alt="MCP Tools"></a>
  <a href="#"><img src="https://img.shields.io/badge/skills-92%2B%20marketplace-green?style=flat-square" alt="Skill Marketplace"></a>
  <a href="#"><img src="https://img.shields.io/badge/embeddings-local%20nomic-orange?style=flat-square" alt="Local Embeddings"></a>
  <a href="#"><img src="https://img.shields.io/badge/tunnel-Cloudflare-4285F4?style=flat-square" alt="Cloudflare"></a>
</p>

<p align="center">
  Orchestration layer on top of Claude Code CLI. Routes tasks via Temporal, serves 77 MCP tools, maintains a knowledge graph, and learns from feedback. WhatsApp-native. Runs from a laptop via Cloudflare Tunnel.
</p>

---

## Architecture

```
Internet → Cloudflare Tunnel → Docker Compose (laptop)
  ├── servicetsunami.com
  └── agentprovision.com

┌──────────────────────────────────────────────────────────┐
│  Channels: WhatsApp · Web Chat · API                      │
└────────────────────────┬─────────────────────────────────┘
                         │
┌────────────────────────▼─────────────────────────────────┐
│  FastAPI (api:8001)                                       │
│  Agent Router → Temporal dispatch → Chat service          │
└──────┬─────────────────┬─────────────────┬───────────────┘
       │                 │                 │
┌──────▼──────┐  ┌───────▼───────┐  ┌─────▼──────┐
│ Code Worker │  │  MCP Tools    │  │  Temporal   │
│ Claude Code │  │  77 tools     │  │  Workflows  │
│ Opus 4.6    │  │  FastMCP      │  │             │
│ git + gh    │  │  port 8087    │  │  port 7233  │
└─────────────┘  └───────────────┘  └────────────┘
```

## How It Works

1. User sends message (WhatsApp/Web)
2. Agent Router selects platform + agent (zero LLM cost)
3. Temporal dispatches to code-worker
4. Claude Code CLI runs with `--model opus` + 77 MCP tools
5. Luna responds using knowledge graph, email, calendar, Jira, code tools
6. Response flows back through chat service
7. RL system logs experience, user feedback trains routing

## Key Features

**CLI Agent Orchestration** — Luna runs as Claude Code CLI (Opus 4.6) with full dev capabilities. Creates branches, commits, pushes, opens PRs. Uses your subscription — zero API credits.

**77 MCP Tools** — Email, calendar, knowledge graph, Jira, GitHub, data analytics, ads (Meta/Google/TikTok), competitor monitoring, sales pipeline, reports, shell, skills.

**Skill & Tool Marketplace** — Three-tier (native/community/custom). Import from GitHub. 92 Google Workspace skills. Agents are skills with `engine: agent`.

**Knowledge Graph** — pgvector semantic search, nomic-embed-text-v1.5 (local, 768-dim). Entities, relations, observations auto-extracted from every interaction.

**RL-Driven Learning** — Every chat logs agent_selection, tool_selection, response_generation. Thumbs up/down feedback. Learning page with metrics.

**WhatsApp Native** — Luna on WhatsApp via Neonize. Persistent typing indicator. Local PDF/document processing. Bulk email scanning in Python.

**Cloudflare Tunnel** — Both domains served from laptop. No port forwarding, automatic SSL.

## Quick Start

```bash
git clone https://github.com/nomad3/servicetsunami-agents.git
cd servicetsunami-agents
DB_PORT=8003 API_PORT=8001 WEB_PORT=8002 docker-compose up --build
# Web: http://localhost:8002 | API: http://localhost:8001
```

## Tech Stack

| Layer | Technology |
|-------|-----------|
| Agent | Claude Code CLI (Opus 4.6) via Temporal |
| Tools | MCP (FastMCP, 77 tools, Streamable HTTP) |
| Embeddings | nomic-embed-text-v1.5 (local, 768-dim) |
| Backend | FastAPI, Python 3.11, SQLAlchemy, pgvector |
| Frontend | React 18, Bootstrap 5, react-markdown |
| Workflows | Temporal (durable, retries, timeouts) |
| Messaging | WhatsApp via Neonize |
| Tunnel | Cloudflare (servicetsunami.com + agentprovision.com) |
| Infrastructure | Docker Compose, Helm, GKE (legacy) |

---

*Built with Claude Code CLI · MCP · Temporal · pgvector · Neonize · Cloudflare · FastAPI · React*
