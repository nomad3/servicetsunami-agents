<h1 align="center">ServiceTsunami / Wolfpoint.ai</h1>

<p align="center"><strong>The Orchestration Layer for AI Agents</strong></p>

<p align="center">
  <a href="https://agentprovision.com"><img src="https://img.shields.io/badge/live-agentprovision.com-00d2ff?style=flat-square" alt="Production"></a>
  <a href="#"><img src="https://img.shields.io/badge/agents-Claude%20Code%20%7C%20Codex%20%7C%20Gemini-blueviolet?style=flat-square" alt="Agents"></a>
  <a href="#"><img src="https://img.shields.io/badge/MCP_Tools-81-ff6b6b?style=flat-square" alt="MCP Tools"></a>
  <a href="#"><img src="https://img.shields.io/badge/skills-92%2B%20marketplace-green?style=flat-square" alt="Skill Marketplace"></a>
  <a href="#"><img src="https://img.shields.io/badge/RL-auto%20scoring-orange?style=flat-square" alt="RL"></a>
  <a href="#"><img src="https://img.shields.io/badge/tunnel-Cloudflare-4285F4?style=flat-square" alt="Cloudflare"></a>
</p>

<p align="center">
  Don't build agents — orchestrate them. ServiceTsunami routes tasks to existing AI agent platforms (Claude Code, Codex, Gemini CLI), serves 81 MCP tools, maintains a knowledge graph, auto-scores every response with a local LLM, and learns which platform performs best via RL. Each tenant uses their own subscription — zero API credits.
</p>

---

## Architecture

```
Internet → Cloudflare Tunnel
  ├── servicetsunami.com
  └── agentprovision.com

┌─────────────────────────────────────────────────────────────┐
│  Channels: WhatsApp (Neonize) · Web Chat · API              │
└──────────────────────┬──────────────────────────────────────┘
                       │
┌──────────────────────▼──────────────────────────────────────┐
│  FastAPI Backend                                             │
│  ┌──────────────┐  ┌────────────────┐  ┌─────────────────┐  │
│  │ Agent Router  │  │ Session Manager│  │ Auto Quality    │  │
│  │ (Python, zero │  │ (skill→config, │  │ Scorer (local   │  │
│  │  LLM cost)    │  │  resume/retry) │  │ Qwen, 6-dim RL)│  │
│  └──────┬────────┘  └────────────────┘  └─────────────────┘  │
└─────────┼────────────────────────────────────────────────────┘
          │
┌─────────▼────────────────────────────────────────────────────┐
│  Temporal Workers                                             │
│                                                               │
│  ┌─────────────────┐  ┌──────────────┐  ┌─────────────────┐  │
│  │ Claude Code CLI  │  │ Codex CLI    │  │ Gemini CLI      │  │
│  │ Opus 4.6 ✅      │  │ OpenAI ✅    │  │ Google ⏳       │  │
│  │ Subscription     │  │ Subscription │  │ Subscription    │  │
│  └────────┬─────────┘  └──────┬───────┘  └───────┬─────────┘  │
│           └──────────────────┼────────────────────┘           │
│                              │                                │
│                    ┌─────────▼──────────┐                     │
│                    │  MCP Tool Server    │                     │
│                    │  81 tools (FastMCP) │                     │
│                    └────────────────────┘                     │
└───────────────────────────────────────────────────────────────┘
          │
┌─────────▼────────────────────────────────────────────────────┐
│  Local ML (Ollama — zero cloud cost)                          │
│  ┌─────────────────┐  ┌──────────────┐  ┌─────────────────┐  │
│  │ Auto Scoring     │  │ Knowledge    │  │ Fallback Chat   │  │
│  │ qwen2.5-coder    │  │ Extraction   │  │ (no sub needed) │  │
│  │ 6-dim rubric→RL  │  │ + Triage     │  │ Luna persona    │  │
│  └──────────────────┘  └──────────────┘  └─────────────────┘  │
└───────────────────────────────────────────────────────────────┘
```

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
| Claude Code | OAuth token → vault | **Live** |
| Codex (OpenAI) | auth.json → vault | **Live** |
| Gemini CLI | Google OAuth | Integrated, untested |
| GitHub | OAuth via agentprovision.com | **Live** |
| Gmail/Calendar | Google OAuth with auto-refresh | **Live** |
| Microsoft/Outlook | Microsoft OAuth | Wired |
| Jira | Basic Auth | **Live** |

## AGI Roadmap — Brain Architecture

The platform is evolving from a reactive assistant into a durable agent system through six capability gaps:

```
┌─────────────────────────────────────────────────────────────┐
│                     AGENT BRAIN                             │
│                                                             │
│  Safety Layer (Gap 05)      ████████████████████ COMPLETE   │
│  111 governed actions, trust scores, autonomy tiers         │
│  Evidence packs, tenant overrides, enforcement gates        │
│                                                             │
│  Self-Model (Gap 02)        ████████████████████ COMPLETE   │
│  Goals, commitments, identity profiles                      │
│  6h review workflow, runtime injection into CLI sessions    │
│                                                             │
│  World Model (Gap 01)       █████░░░░░░░░░░░░░░ Phase 1    │
│  Assertions with confidence, TTL, corroboration             │
│  Auto-projected snapshots per entity                        │
│                                                             │
│  Planning (Gap 03)          ░░░░░░░░░░░░░░░░░░░ Planned    │
│  Society of Agents (Gap 06) ░░░░░░░░░░░░░░░░░░░ Planned    │
│  Self-Improvement (Gap 04)  ░░░░░░░░░░░░░░░░░░░ Planned    │
└─────────────────────────────────────────────────────────────┘

RL Feedback Loop:
  Response → Auto Score (Qwen 6-dim) → Provider Council (20%)
  → RL Experience → Trust Recompute → Routing Optimization
  → Exploration: 70% Codex / 30% Claude Code
```

See `docs/plans/2026-03-24-agi-roadmap-summary.md` for full diagrams and implementation details.

## Quick Start

```bash
git clone https://github.com/nomad3/servicetsunami-agents.git
cd servicetsunami-agents
DB_PORT=8003 API_PORT=8001 WEB_PORT=8002 docker-compose up --build

# Web:         http://localhost:8002
# API:         http://localhost:8001
# MCP Tools:   http://localhost:8087
# Temporal UI: http://localhost:8233
# Demo login:  test@example.com / password
```

### Connect Your Agent
1. **Integrations** → Claude Code → run `claude setup-token` → paste token
2. Chat via web or WhatsApp — Luna responds via your subscription
3. Every response auto-scored and logged for RL improvement

## Stack

FastAPI · React 18 · PostgreSQL + pgvector · Temporal · FastMCP · Ollama (Qwen) · Neonize (WhatsApp) · Cloudflare Tunnel · Docker Compose · nomic-embed-text-v1.5

## Documentation

See `CLAUDE.md` for full architecture, API structure, development commands, and patterns.

---

*Built with Claude Code CLI · Codex CLI · Gemini CLI · MCP · Temporal · Ollama · pgvector · Neonize · Cloudflare · FastAPI · React*
