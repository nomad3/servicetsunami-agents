# Technical Debt Reduction Plan

**Date**: 2026-03-27
**Scope**: Full platform audit — design docs vs implementation

---

## Current State

The platform has strong foundations (chat, WhatsApp, knowledge graph, RL, Temporal workflows, safety governance) but significant gaps in enterprise features, marketplace infrastructure, and frontend completeness.

**63% average implementation** across 19 design documents.

---

## Priority 1: Quick Wins (1-2 days each)

### P1.1 — Memory recall injection into chat
- **Gap**: Knowledge graph entities/observations are never recalled and injected into the CLI prompt before agent execution
- **Impact**: Luna has no memory of past conversations or entities unless explicitly searched
- **Fix**: In `cli_session_manager.py`, before building the CLAUDE.md, query `embedding_service.recall()` for the user's message and inject top-5 relevant memories into the system prompt
- **Files**: `services/cli_session_manager.py`, `services/embedding_service.py`
- **Effort**: 2-3 hours

### P1.2 — Lead scoring endpoint
- **Gap**: `score` + `scored_at` columns exist on knowledge_entities but no endpoint to trigger scoring
- **Impact**: Can't score leads programmatically or from UI
- **Fix**: Add `POST /api/v1/knowledge/entities/{id}/score` using existing `LeadScoringTool`
- **Files**: `api/v1/knowledge.py`
- **Effort**: 1 hour

### P1.3 — ExecutionTrace population
- **Gap**: Model exists, rarely populated. Workflows run but don't log step-by-step traces
- **Impact**: No audit trail for task execution
- **Fix**: Add trace logging to `TaskExecutionWorkflow` activities
- **Files**: `workflows/activities/`, `services/execution_trace.py`
- **Effort**: 3-4 hours

### P1.4 — Brand assets: JPEG → SVG
- **Gap**: All "PNG" logo files are actually JPEGs with white backgrounds, no transparency
- **Impact**: Logos look broken on dark backgrounds, no proper favicon
- **Fix**: Generate proper SVG/PNG with transparency, update favicon.ico, og-image
- **Effort**: 2 hours (design tool + file replacement)

---

## Priority 2: Core Feature Completions (3-5 days each)

### P2.1 — Dynamic Workflows visual builder
- **Gap**: Model + executor exist but no UI for creating workflows
- **Impact**: Only developers can create workflows via API
- **Fix**: Build drag-and-drop workflow builder in React (WorkflowBuilderPage.js)
- **Files**: `pages/WorkflowBuilderPage.js`, `components/workflow/`
- **Effort**: 5 days

### P2.2 — Memory page completion
- **Gap**: Memories tab incomplete, Activity tab missing filters, no health overview
- **Impact**: Users can't see what Luna remembers or audit memory activity
- **Fix**: Complete MemoryPage tabs: Memories (grouped by type), Activity (with event type filters), Overview (health bar + stats)
- **Files**: `pages/MemoryPage.js`, `components/memory/`
- **Effort**: 3 days

### P2.3 — Connectors page
- **Gap**: ConnectorsPage is a stub — no connector types, no test connection, no CRUD
- **Impact**: Can't connect external data sources (Snowflake, PostgreSQL, S3)
- **Fix**: Build connector type registry, credential management, test connection flow
- **Files**: `api/v1/connectors.py`, `services/connector_service.py`, `pages/ConnectorsPage.js`
- **Effort**: 5 days

### P2.4 — Gemini CLI integration
- **Gap**: Design complete, ~10% implemented. No Dockerfile install, no auth endpoint
- **Impact**: Can't use Gemini as a CLI provider
- **Fix**: Add gemini CLI to code-worker Dockerfile, wire auth endpoint, add to fallback chain
- **Files**: `code-worker/Dockerfile`, `code-worker/workflows.py`, `api/v1/oauth.py`
- **Effort**: 3 days

### P2.5 — Marketing ads tools
- **Gap**: No Meta/Google/TikTok ad platform tools, no marketing_analyst agent
- **Impact**: Can't manage ad campaigns or monitor competitor ads
- **Fix**: Build ads_tools.py (15 tools per design doc), create marketing_analyst skill
- **Files**: `mcp-tools/src/mcp_tools/ads.py`, `skills/native/marketing_analyst/`
- **Effort**: 5 days

---

## Priority 3: Platform Infrastructure (1-2 weeks each)

### P3.1 — WhatsApp Partner API
- **Gap**: External apps can't integrate with the platform via WhatsApp
- **Impact**: ai-marketing-platform, PharmApp use cases broken
- **Models needed**: IntegrationPartner, WhatsappConnection, MessageLog
- **Routes needed**: `/api/v1/partners/*`, `/api/v1/webhooks/whatsapp`
- **Workflows needed**: WhatsAppMessageWorkflow, WhatsAppCampaignWorkflow
- **Effort**: 2 weeks

### P3.2 — Multi-LLM callback registration
- **Gap**: `before_model_callback` not on all 25 agent files per design
- **Impact**: Tenants can't seamlessly switch LLM providers
- **Fix**: Systematic audit and add callback to all agent definitions
- **Files**: 25+ agent/skill files
- **Effort**: 1 week

### P3.3 — Local Ollama performance
- **Gap**: 47-80s per call, cold start >300s
- **Impact**: Free-tier users get unusable response times
- **Fix**: Switch to qwen3:0.6b for tool calling, pre-warm model on startup, add response streaming
- **Files**: `services/local_tool_agent.py`, `services/local_inference.py`
- **Effort**: 3 days

---

## Priority 4: Monetization Prerequisites (2-4 weeks each)

### P4.1 — Distributed Node Network (STP Phase 2)
- **Gap**: Zero implementation — no NetworkNode, AgentPackage models
- **Impact**: Can't scale beyond single laptop, can't onboard external operators
- **Models needed**: NetworkNode, AgentPackage, NodeHeartbeat
- **Routes needed**: `/api/v1/nodes/*`, `/api/v1/agent-packages/*`
- **UI needed**: Node directory, agent marketplace
- **Effort**: 4 weeks

### P4.2 — Credit System (STP Phase 3)
- **Gap**: Zero implementation — no CreditAccount, no Stripe integration
- **Impact**: Can't charge users, can't split revenue with operators
- **Models needed**: CreditAccount, CreditTransaction, UsageRecord
- **Routes needed**: `/api/v1/credits/*`, `/api/v1/billing/*`
- **Integrations needed**: Stripe Connect
- **Effort**: 3 weeks

### P4.3 — Agent Marketplace
- **Gap**: Skills exist but no publishing/discovery/install flow
- **Impact**: Can't distribute or monetize agent packages
- **Fix**: Build marketplace listing, review, install pipeline
- **UI needed**: MarketplacePage, SkillDetailPage, PublishFlow
- **Effort**: 3 weeks

---

## Dead Code to Remove

| Item | Location | Action |
|------|----------|--------|
| ADK references | Various (already removed per memory) | Verify clean |
| Signal entity creation | knowledge_extraction.py | Remove signal→parent pattern |
| Unused SkillConfig imports | Various services | Clean up |
| Old VM deploy script | scripts/deploy.sh | Delete |
| GKE workflow files (.yaml.disabled) | .github/workflows/ | Delete |

---

## Summary Timeline

| Phase | Duration | Focus |
|-------|----------|-------|
| Week 1 | P1.1-P1.4 | Quick wins: memory recall, lead scoring, traces, brand |
| Weeks 2-3 | P2.1-P2.5 | Core features: workflow builder, memory UI, connectors, Gemini, ads |
| Weeks 4-5 | P3.1-P3.3 | Infrastructure: partner API, multi-LLM, Ollama perf |
| Weeks 6-9 | P4.1-P4.3 | Monetization: node network, credits, marketplace |
