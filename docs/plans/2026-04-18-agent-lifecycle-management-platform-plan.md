# Agent Lifecycle Management Platform — Gap Analysis & Roadmap

**Date:** 2026-04-18  
**Scope:** Enterprise-grade ALM platform — single pane of glass for the full lifecycle of every agent a company runs, hires, or publishes.

---

## What We're Building Toward

Companies need one place to:
- **Register** agents (internal, external, hired, federated)
- **Promote** them safely through environments (draft → staging → production)
- **Own** them (user-level accountability, team assignment)
- **Govern** them (permissions, content policies, data access)
- **Orchestrate** them (A2A communication, task routing, handoffs)
- **Measure** them (SLA, cost, quality per agent over time)
- **Audit** them (compliance-grade logs of every invocation)
- **Retire** them (graceful deprecation with successor routing)

Current platform covers orchestration well. Everything else is partial or missing.

---

## Gap Analysis (confirmed by codebase audit)

| Gap | Current State | What's Missing |
|-----|--------------|----------------|
| Agent lifecycle states | No `status` field on `Agent` model | `draft → staging → production → deprecated` transitions |
| Agent ownership | `tenant_id` only, no `owner_user_id` | Per-user ownership, team assignment |
| RBAC on agents | Tenant-level only | Who can invoke / modify / deploy each agent |
| Comprehensive audit log | `ExecutionTrace` tracks execution; `UserActivity` tracks UI events | Single `AgentAuditLog` record: who invoked what agent, when, with what input/output |
| Per-agent performance | Tenant-level aggregates only | Per-agent: latency p50/p95/p99, error rate, timeout rate, token cost |
| Agent discovery | Static `AgentRelationship` groups | Runtime registry: "find agents with capability X" |
| Cost attribution | Per-task/execution exists | Rolled-up `total_cost_usd` and `total_tokens` per agent, cost per quality point |
| Human-in-the-loop steps | Task-level `requires_approval` only | Workflow-step pause-and-wait with timeout and fallback |
| Multi-org federation | Agents locked to single `tenant_id` | Cross-org agent sharing, agent marketplace between orgs |
| Agent versioning | No version history on Agent | Deploy v2, rollback to v1, version diff |
| Agent import formats | GitHub SKILL.md only | LangChain, CrewAI, AutoGen, OpenAI Assistants |
| Agent testing | None | Test harness, golden dataset eval, shadow mode |
| Agent retirement | Delete only (destructive) | Graceful deprecation with successor routing |
| A2A protocol coverage | Coalition workflow (Temporal, heavyweight) | Lightweight sync RPC between agents, pub/sub events |

---

## Pillar 1 — Agent Identity & Ownership

### What's needed
Every agent needs an owner (a user), an optional team, and a clear accountability chain. Without this, no enterprise can deploy this to production — there's no one to page when an agent fails.

### Tasks

#### 1.1 Add ownership fields to Agent model
**File:** `apps/api/app/models/agent.py`
```python
owner_user_id = Column(UUID, ForeignKey("users.id"), nullable=True)  # primary owner
team_id       = Column(UUID, ForeignKey("agent_groups.id"), nullable=True)  # owning team
status        = Column(String, default="production")  # draft|staging|production|deprecated
version       = Column(Integer, default=1)
```
**Migration:** `093_agent_ownership_and_status.sql`

#### 1.2 Agent status lifecycle transitions
**File:** `apps/api/app/api/v1/agents.py`
```
POST /agents/{id}/promote    → draft → staging → production
POST /agents/{id}/deprecate  → production → deprecated (requires successor_agent_id)
```
- `deprecated` agents: still visible in fleet with gray "Deprecated" badge, no new tasks routed to them.
- All existing tasks complete; new tasks go to `successor_agent_id`.

#### 1.3 Fleet UI — lifecycle states
**File:** `apps/web/src/pages/AgentsPage.js`
- Status badges: `Draft` (gray), `Staging` (amber), `Production` (green), `Deprecated` (red strikethrough).
- Filter bar: filter fleet by status.
- Owners shown as avatar + name on each card.
- "Promote" and "Deprecate" actions in agent detail page.

---

## Pillar 2 — RBAC & Governance

### What's needed
Not everyone should invoke, modify, or deploy every agent. Enterprise teams need role-based controls: a data science team can create agents, only tech leads can promote to production, only admins can deprecate.

### Tasks

#### 2.1 Agent-level permission model
**New table:** `agent_permissions` (migration `094`)
```sql
CREATE TABLE agent_permissions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    agent_id UUID NOT NULL REFERENCES agents(id) ON DELETE CASCADE,
    tenant_id UUID NOT NULL REFERENCES tenants(id),
    principal_type VARCHAR NOT NULL,   -- 'user' | 'team' | 'role'
    principal_id UUID NOT NULL,
    permission VARCHAR NOT NULL,       -- 'invoke' | 'edit' | 'promote' | 'deprecate' | 'admin'
    granted_by UUID REFERENCES users(id),
    created_at TIMESTAMPTZ DEFAULT NOW()
);
```
- `invoke`: can run the agent
- `edit`: can modify config, skills, integrations
- `promote`: can move draft → staging → production
- `deprecate`: can retire the agent
- `admin`: all of the above + delete + permission management

#### 2.2 Permission enforcement in API
**File:** `apps/api/app/api/deps.py`
- New dependency `require_agent_permission(permission)` — checks `agent_permissions` table before any agent operation.
- Fallback: agent owner always has admin. Tenant admin always has admin.

#### 2.3 Content governance policies
**New table:** `agent_policies` (migration `095`)
```sql
CREATE TABLE agent_policies (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    agent_id UUID REFERENCES agents(id),   -- NULL = applies to all tenant agents
    tenant_id UUID NOT NULL REFERENCES tenants(id),
    policy_type VARCHAR NOT NULL,   -- 'output_filter' | 'input_filter' | 'data_access' | 'rate_limit'
    config JSONB NOT NULL,
    enabled BOOLEAN DEFAULT TRUE
);
```
Examples:
- `output_filter`: block responses containing competitor names, PII patterns
- `data_access`: agent cannot query tables matching `regex` pattern
- `rate_limit`: max N invocations per user per hour

**File:** `apps/api/app/services/enhanced_chat.py`
- Apply policies before returning response (output_filter via local Gemma 4 check).
- Apply policies before dispatch (input_filter, data_access).

---

## Pillar 3 — Comprehensive Audit Log

### What's needed
Compliance requires an immutable record: which user invoked which agent, at what time, with what exact input and output, on what data, at what cost. Must be exportable for SOC2, GDPR, and internal SRE reviews.

### Tasks

#### 3.1 AgentAuditLog model
**New file:** `apps/api/app/models/agent_audit_log.py`  
**Migration:** `096_agent_audit_log.sql`
```python
class AgentAuditLog(Base):
    __tablename__ = "agent_audit_logs"
    id              = Column(UUID, primary_key=True, default=uuid.uuid4)
    tenant_id       = Column(UUID, ForeignKey("tenants.id"), nullable=False, index=True)
    agent_id        = Column(UUID, ForeignKey("agents.id"), nullable=True, index=True)
    external_agent_id = Column(UUID, nullable=True)   # for hired agents
    invoked_by_user_id = Column(UUID, ForeignKey("users.id"), nullable=True)
    invoked_by_agent_id = Column(UUID, ForeignKey("agents.id"), nullable=True)  # A2A call
    session_id      = Column(UUID, nullable=True)
    invocation_type = Column(String)   # 'chat' | 'workflow' | 'a2a' | 'api' | 'scheduled'
    input_summary   = Column(Text)     # first 500 chars of input (PII-stripped)
    output_summary  = Column(Text)     # first 500 chars of output
    input_tokens    = Column(Integer)
    output_tokens   = Column(Integer)
    cost_usd        = Column(Float)
    latency_ms      = Column(Integer)
    status          = Column(String)   # 'success' | 'error' | 'timeout' | 'blocked_by_policy'
    error_message   = Column(Text, nullable=True)
    policy_violations = Column(JSONB, nullable=True)   # which policies fired
    quality_score   = Column(Float, nullable=True)     # from RL scorer
    created_at      = Column(DateTime, index=True)
```

#### 3.2 Write audit log on every agent invocation
**Files:** `apps/api/app/services/enhanced_chat.py`, `apps/api/app/workflows/activities/coalition_activities.py`, `apps/api/app/api/v1/external_agents.py`
- After every agent dispatch (internal or external), write one `AgentAuditLog` row.
- Fire-and-forget (non-blocking): write via background thread or Temporal activity.

#### 3.3 Audit API & UI
**Endpoint:** `GET /agents/{id}/audit-log?from=&to=&status=&invoked_by=`  
**Endpoint:** `GET /audit/agents?tenant_id=&from=&to=` (admin only)  
**Endpoint:** `GET /audit/agents/export?format=csv&from=&to=` (compliance export)

**File:** `apps/web/src/pages/AgentDetailPage.js`
- "Audit" tab: timeline of invocations with user, time, latency, cost, status, policy violations.
- Filter by date range, status, invoked_by.
- Export to CSV button.

---

## Pillar 4 — Per-Agent Performance Dashboard

### What's needed
Fleet managers need to see which agents are fast vs slow, expensive vs cheap, reliable vs flaky — per agent, over time, with trend lines. Currently metrics only exist at tenant level.

### Tasks

#### 4.1 Agent performance materialized view (or scheduled rollup)
**Migration:** `097_agent_performance_rollup.sql`
```sql
CREATE TABLE agent_performance_snapshots (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    agent_id UUID NOT NULL REFERENCES agents(id),
    tenant_id UUID NOT NULL REFERENCES tenants(id),
    window_start TIMESTAMPTZ NOT NULL,
    window_hours INTEGER NOT NULL DEFAULT 24,
    invocation_count INTEGER DEFAULT 0,
    success_count INTEGER DEFAULT 0,
    error_count INTEGER DEFAULT 0,
    timeout_count INTEGER DEFAULT 0,
    latency_p50_ms INTEGER,
    latency_p95_ms INTEGER,
    latency_p99_ms INTEGER,
    avg_quality_score FLOAT,
    total_tokens INTEGER DEFAULT 0,
    total_cost_usd FLOAT DEFAULT 0,
    cost_per_quality_point FLOAT,
    created_at TIMESTAMPTZ DEFAULT NOW()
);
```
Snapshots written by a new Temporal scheduled activity every hour: `compute_agent_performance_snapshot`.

#### 4.2 Performance API
**File:** `apps/api/app/api/v1/agents.py`
```
GET /agents/{id}/performance?window=24h|7d|30d
```
Returns: invocation count, success rate, latency percentiles, quality trend, cost trend.

#### 4.3 Fleet performance overview
**File:** `apps/web/src/pages/AgentsPage.js`
- Each agent card shows: last 24h invocations, success rate bar, avg latency, cost badge.
- Sortable columns: by quality score, by cost, by error rate.

#### 4.4 Agent performance detail charts
**File:** `apps/web/src/pages/AgentDetailPage.js`
- "Performance" tab: 7-day sparklines for quality score, latency p50/p95, cost.
- Alert threshold config: "notify me if error rate > 10% or latency p95 > 5s".
- Comparison mode: compare two versions of the same agent.

---

## Pillar 5 — Agent Discovery & Runtime Registry

### What's needed
Agents need to find each other at runtime without hardcoded IDs. An orchestrator should be able to ask "give me the best available agent with capability `data_analysis` that has error rate < 5% in the last 24h" — and get a live answer.

### Tasks

#### 5.1 Agent Registry service
**New file:** `apps/api/app/services/agent_registry.py`
```python
class AgentRegistry:
    def find_by_capability(self, capability: str, tenant_id: UUID, 
                           max_error_rate: float = 0.1) -> list[Agent]:
        """Return agents matching capability, sorted by quality score desc."""

    def find_available(self, tenant_id: UUID) -> list[Agent]:
        """Return agents not at capacity (current tasks < max_concurrent)."""

    def advertise(self, agent_id: UUID, capabilities: list[str]) -> None:
        """Agent announces its capabilities (called on startup/health check)."""
```
- Backed by Redis for real-time availability (TTL 60s, refreshed by health checks).
- Fallback to DB query if Redis unavailable.

#### 5.2 Agent self-registration on startup
**File:** `apps/api/app/api/v1/agents.py`
- `POST /agents/{id}/heartbeat` — agent (or its worker) calls this every 30s with current load.
- Updates Redis key `agent:available:{id}` with TTL.
- External agents call this via their configured endpoint health check.

#### 5.3 Intelligent routing in coalition workflow
**File:** `apps/api/app/workflows/activities/coalition_activities.py`
- Replace static `agent_id` in coalition role assignments with `AgentRegistry.find_by_capability(role)`.
- Coalition can dynamically pick the best available agent for each phase at runtime.

#### 5.4 Luna can discover and hire agents via chat
**File:** `apps/api/app/services/enhanced_chat.py` (MCP tool)
- New MCP tool: `find_agent(capability, max_latency_ms, max_cost_per_call)` — Luna calls this to discover and route to the right agent.
- Returns agent name, description, current availability.

---

## Pillar 6 — Agent Versioning & Promotion

### What's needed
When you update an agent's config, skills, or prompt, you need a safe path: test the new version in staging, compare performance against the old version, then promote to production. Without versioning, every config change is a risky live edit.

### Tasks

#### 6.1 Agent version history table
**New file:** `apps/api/app/models/agent_version.py`  
**Migration:** `098_agent_versions.sql`
```python
class AgentVersion(Base):
    __tablename__ = "agent_versions"
    id         = Column(UUID, primary_key=True)
    agent_id   = Column(UUID, ForeignKey("agents.id"))
    tenant_id  = Column(UUID)
    version    = Column(Integer)           # monotonic, increments on each save
    config_snapshot = Column(JSONB)        # full Agent config at this version
    promoted_by = Column(UUID, ForeignKey("users.id"))
    promoted_at = Column(DateTime)
    status      = Column(String)           # draft | staging | production | rolled_back
    notes       = Column(Text)             # changelog / reason for change
    performance_snapshot = Column(JSONB)   # p95 latency, quality score at time of promotion
```

#### 6.2 Version API
**File:** `apps/api/app/api/v1/agents.py`
```
GET  /agents/{id}/versions              → list all versions
POST /agents/{id}/versions/{v}/promote  → promote version v to production
POST /agents/{id}/versions/{v}/rollback → revert to version v
GET  /agents/{id}/versions/{v}/diff     → config diff between v and current
```

#### 6.3 Version comparison in UI
**File:** `apps/web/src/pages/AgentDetailPage.js`
- "Versions" tab: timeline of versions with status badges and who promoted each.
- Side-by-side diff view for config changes between versions.
- "Rollback" button with confirmation.

---

## Pillar 7 — Human-in-the-Loop Workflow Steps

### What's needed
The current `requires_approval` on `AgentTask` is binary (approve/reject the whole task). Enterprise workflows need granular checkpoints: "run steps 1–3 autonomously, pause at step 4 for a human to review the draft, then continue."

This is already partially scaffolded in `DynamicWorkflow` (step type `human_approval` exists in CLAUDE.md) but not fully wired.

### Tasks

#### 7.1 Wire the `human_approval` step type in DynamicWorkflowExecutor
**File:** `apps/api/app/workflows/activities/dynamic_workflow_activities.py`
- When executor reaches a `human_approval` step: emit `approval_requested` event to chat session SSE.
- Pause workflow (Temporal signal wait) — timeout configurable (`timeout_hours`, default 24).
- On timeout: execute `on_timeout` branch (`approve` | `reject` | `skip`).
- Frontend: approval card appears in chat with "Approve" / "Reject" / "Request Changes" buttons.

#### 7.2 Approval notification
**File:** `apps/api/app/models/notification.py`
- Create high-priority notification when approval is requested.
- If agent has `owner_user_id`, notify that user first. Fallback to all tenant admins.

#### 7.3 Approval API
**File:** `apps/api/app/api/v1/agents.py`
```
POST /agent-tasks/{task_id}/approve   → { decision: 'approved' | 'rejected', comment: str }
```
- Sends Temporal signal to resume the workflow.

---

## Pillar 8 — Agent Import Formats

### What's needed
Teams already have agents defined in LangChain, CrewAI, or AutoGen. They should be able to import them into the platform rather than rebuild from scratch.

### Tasks

#### 8.1 Import adapter for CrewAI
**File:** `apps/api/app/services/agent_importer.py`
- Parse `crew.yaml` or `crew.py`: extract agent name, role, goal, backstory, tools list.
- Map to `Agent` create payload: `name`, `description`, `persona_prompt` (backstory), `capabilities` (tools).
- Register as a draft agent with `source = "crewai"` in metadata.

#### 8.2 Import adapter for LangChain
- Parse LangChain `AgentExecutor` JSON export or LCEL chain config.
- Extract: agent type, tools, prompt template, memory type.
- Map tools to matching MCP tools in the platform's skill registry.

#### 8.3 Import adapter for AutoGen
- Parse `AssistantAgent` / `UserProxyAgent` config dicts.
- Extract: system message (→ `persona_prompt`), `code_execution_config` (→ code agent flag), `function_map` (→ skills).

#### 8.4 Import UI
**File:** `apps/web/src/pages/AgentsPage.js`
- Dropdown on "+ Agent Wizard": "Import from file" option.
- Upload `.yaml` / `.json` / `.py` file.
- Auto-detect format (CrewAI / LangChain / AutoGen / OpenAI Assistant).
- Preview parsed agent config, allow edits, then save as draft.

---

## Pillar 9 — Multi-Org Agent Federation

### What's needed
Org A should be able to publish an agent to a marketplace. Org B discovers it, subscribes, and gets a "hired" instance of it. The agent runs in Org A's infrastructure but is managed from Org B's fleet — output flows back to Org B. This is the B2B agent economy.

### Tasks

#### 9.1 Agent marketplace (cross-org)
**New table:** `agent_marketplace_listings` (migration `099`)
```sql
CREATE TABLE agent_marketplace_listings (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    agent_id UUID NOT NULL REFERENCES agents(id),
    publisher_tenant_id UUID NOT NULL REFERENCES tenants(id),
    name VARCHAR NOT NULL,
    description TEXT,
    capabilities JSONB,
    protocol VARCHAR NOT NULL,   -- how subscribers invoke it: openai_chat | mcp_sse | webhook
    endpoint_url VARCHAR,        -- publisher's publicly accessible endpoint
    pricing_model VARCHAR,       -- 'free' | 'per_call' | 'subscription'
    price_per_call_usd FLOAT,
    install_count INTEGER DEFAULT 0,
    avg_rating FLOAT,
    public BOOLEAN DEFAULT FALSE,
    created_at TIMESTAMPTZ DEFAULT NOW()
);
```

#### 9.2 Subscribe flow
- `POST /marketplace/agents/{listing_id}/subscribe` — creates an `ExternalAgent` record in the subscribing tenant pointing to the publisher's endpoint.
- Publisher receives a subscription event and can approve/deny.
- Approved: subscribing tenant gets an API key for the published agent's endpoint.

#### 9.3 Publisher controls
- Publisher sets per-subscriber rate limits, allowed scopes, and revenue splits.
- Publisher sees subscriber list, usage stats, and revenue in their fleet dashboard.

#### 9.4 Marketplace UI
- New tab in Agent Fleet: "Marketplace" — discover and subscribe to agents published by other orgs.
- Each listing: name, publisher, capabilities, pricing, rating, install count.
- "Subscribe" button → fires subscribe API, creates ExternalAgent in fleet immediately.

---

## Pillar 10 — Agent Testing & Evaluation

### What's needed
Before promoting an agent from staging to production, teams need to validate it doesn't regress. This requires a test harness: run the agent against a golden dataset of expected inputs/outputs, compare quality scores, and block promotion if scores drop.

### Tasks

#### 10.1 Agent test suite model
**New file:** `apps/api/app/models/agent_test_suite.py`  
**Migration:** `100_agent_test_suites.sql`
```python
class AgentTestCase(Base):
    agent_id    = Column(UUID, ForeignKey("agents.id"))
    input       = Column(Text)           # test input message
    expected_output_contains = Column(JSONB)   # list of strings that must appear
    expected_output_excludes = Column(JSONB)   # list of strings that must NOT appear
    min_quality_score = Column(Float, default=0.6)
    max_latency_ms    = Column(Integer, default=10000)
    tags              = Column(JSONB)    # ["regression", "smoke", "perf"]
```

#### 10.2 Test runner
**File:** `apps/api/app/api/v1/agents.py`
```
POST /agents/{id}/test          → run all test cases, return results
POST /agents/{id}/test/shadow   → run new version in shadow (don't affect output), compare to current
```
- Shadow mode: dispatch the same input to both old and new agent version in parallel, compare quality scores, return diff.

#### 10.3 Promotion gate
**File:** `apps/api/app/api/v1/agents.py`
- `POST /agents/{id}/promote` checks test suite results first if test cases exist.
- Block promotion if any test case fails. Return failing cases in the error response.

#### 10.4 Test UI
**File:** `apps/web/src/pages/AgentDetailPage.js`
- "Tests" tab: list test cases with last result, status badge, run button.
- "Run all tests" button with live progress.
- "Shadow mode" toggle: run staging vs production side-by-side.

---

## Priority Order

| Priority | Pillar | Status |
|----------|--------|--------|
| P0 | Pillar 1 — Identity & Ownership | ✅ Shipped (migrations 093–095) |
| P0 | Pillar 3 — Audit Log | ✅ Shipped (migration 098, `audit_log.py`) |
| P1 | Pillar 2 — RBAC & Governance | ✅ Shipped (migrations 096–097, `require_agent_permission`) |
| P1 | Pillar 4 — Performance Dashboard | ✅ Shipped (migration 099, hourly snapshots) |
| P1 | Pillar 5 — Agent Discovery | ✅ Shipped (`agent_registry.py`, Redis-backed) |
| P2 | Pillar 6 — Versioning | ✅ Shipped (migration 100, promote/rollback) |
| P2 | Pillar 7 — Human-in-the-Loop | ✅ Shipped (`human_approval` step + `/agent-tasks/{id}/workflow-approve`) |
| P2 | Pillar 8 — Import Formats | ✅ Shipped (`agent_importer.py`, `/agents/import`, Import modal) |
| P3 | Pillar 9 — Federation / Marketplace | ✅ Core shipped (migration 104, `/marketplace/*`, Marketplace section). **Deferred:** per-subscriber rate limits, allowed scopes, revenue splits. |
| P3 | Pillar 10 — Testing & Evaluation | ✅ Core shipped (migration 105, `/agents/{id}/test`, Tests tab, promotion gate). **Deferred:** `/test/shadow` mode (run new vs. current in parallel). |

---

## Migration Sequence (actual)

Actual numbering diverged from the plan by +2 (091/092 were already consumed by blackboard + password reset work):

```
093 — agent_integration_configs        (Part B from fleet plan)
094 — external_agents                  (Part C from fleet plan)
095 — agent_ownership_and_status       (Pillar 1)
096 — agent_permissions                (Pillar 2)
097 — agent_policies                   (Pillar 2)
098 — agent_audit_log                  (Pillar 3)
099 — agent_performance_rollup         (Pillar 4)
100 — agent_versions                   (Pillar 6)
104 — agent_marketplace_listings       (Pillar 9)  ← 101–103 consumed by chat_sessions.agent_id, agent name unique, workflow counter defaults
105 — agent_test_suites                (Pillar 10)
```

## Files Summary

**New models:** `external_agent.py`, `agent_integration_config.py`, `agent_audit_log.py`, `agent_version.py`, `agent_permission.py`, `agent_policy.py`, `agent_performance_snapshot.py`, `agent_test_suite.py`, `agent_marketplace_listing.py`

**New services:** `external_agent_adapter.py`, `agent_registry.py`, `agent_importer.py`

**New API routers:** `external_agents.py`, `agent_marketplace.py`

**Modified:** `agents.py` (routes), `agent.py` (model), `deps.py` (RBAC), `enhanced_chat.py` (policy enforcement, audit), `coalition_activities.py` (dynamic routing, audit), `dynamic_workflow_activities.py` (human-in-loop), `orchestration_worker.py` (performance snapshots, health checks)

**Frontend:** `AgentsPage.js` (fleet restructure, hire wizard, marketplace tab, status filters), `AgentDetailPage.js` (performance, audit, versions, tests, integrations tabs)
