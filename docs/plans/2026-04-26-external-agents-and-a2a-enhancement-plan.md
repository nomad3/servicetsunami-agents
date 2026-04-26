# External Agents + A2A Enhancement Plan (revised)

**Status:** revised draft — opened 2026-04-26, follow-on work after the skills marketplace redesign (PRs #182–#193).

**Owners:** Simon (architecture, code) — Luna for chat-driven changes once the MCP shim lands.

> **Revision note (2026-04-26 v2):** The first draft proposed a parallel agent-messaging table, a separate blackboard browser UI, and hardcoded coalition-pattern code. After re-reading CLAUDE.md, the dynamic-workflows design docs, and the live code, **most of that duplicates the platform spine**. This revision leans hard on what's already shipped: Dynamic Workflows + Temporal `WorkflowRun` / `WorkflowStepLog` are the audit trail, `CollaborationPanel` is the in-chat A2A view, `AgentPerformanceRollupWorkflow` is the cost rollup, `AgentImporter` is the import path, `CoalitionWorkflow` is the multi-agent orchestration. We add MCP-SSE + reliability + a hire wizard + new workflow templates — that's it.

## Goal

Make hiring, observing, and collaborating with **non-native agents** as friction-free as native ones, and surface **agent-to-agent communication** inline in the chat with the **WorkflowRun / WorkflowStepLog** audit trail behind it. Reuse the Dynamic Workflows runtime as the dispatcher; don't build a parallel mechanism.

## Inventory — what's already shipped (do NOT rebuild)

| Need | Existing component | Where |
|---|---|---|
| Multi-step orchestration with full audit | **Dynamic Workflows** runtime + `WorkflowRun` + `WorkflowStepLog` | `apps/api/app/services/dynamic_workflows.py:150`, `app/workflows/dynamic_executor.py`, `app/workflows/activities/dynamic_step.py:448 _log_step` |
| Agent dispatch as a primitive | `agent` step type in Dynamic Workflows | `dynamic_step.py:196 _call_agent` |
| Multi-agent coalition over a shared blackboard | `CoalitionWorkflow` (Temporal) + `prepare→ChatCliWorkflow→record` cycle | `apps/api/app/workflows/coalition_workflow.py` |
| Live in-chat A2A view | **`CollaborationPanel`** wired into `ChatPage.js` (line 11, 814) | `apps/web/src/components/CollaborationPanel.js` |
| Historical workflow audit / replay UI | **`RunsTab`** (filter by status, drill into step tree) | `apps/web/src/components/workflows/RunsTab.js`, mounted in `WorkflowsPage.js:1519` |
| Import CrewAI / LangChain / AutoGen agent definitions | **`AgentImporter`** | `apps/api/app/services/agent_importer.py` (4 helpers: detect_format, import_crewai, import_langchain, import_autogen, parse_agent_definition) |
| Native-agent capability discovery | `AgentRegistry.find_by_capability` (Redis-cached) + `GET /agents/discover` | `app/services/agent_registry.py:38`, `app/api/v1/agents.py:204` |
| Native-agent cost / latency rollup | **`AgentPerformanceRollupWorkflow`** + `AgentPerformanceSnapshot` (latency p50/p95/p99, total_tokens, total_cost_usd, cost_per_quality_point) | `apps/api/app/workflows/agent_performance_rollup.py:10`, `app/models/agent_performance_snapshot.py` |
| Cross-tenant agent rental | `AgentMarketplaceListing` + `AgentMarketplaceSubscription` (subscribe → approve → optional `external_agent_id` link) | `app/api/v1/agent_marketplace.py`, `app/models/agent_marketplace_listing.py` |
| External agent dispatch (2 of 5 protocols) | `external_agent_adapter.dispatch` for `openai_chat` + `webhook` | `app/services/external_agent_adapter.py:32`, `:59` |
| Credential vault + retrieval | `IntegrationCredential` + `_get_credential` (Fernet-encrypted) | `app/services/external_agent_adapter.py:87`, `app/services/orchestration/credential_vault.py` |
| Internal-key auth for MCP tools | `_verify_internal_key_dep` pattern | `app/api/v1/agents.py:37` |
| MCP tool registration | `apps/mcp-server/src/mcp_tools/__init__.py` (per-module imports) | various |
| Luna's `handoff` presence state | already enumerated in `luna_presence_service.py:20` | service exists |

## Gaps that justify new work

1. **Three external-agent protocols are stubs:** `mcp_sse` (line 21), `a2a` (line 25), `copilot_extension` (line 27) — every Claude Code / Gemini / Cursor skill is MCP-SSE, so this gates "hire any external agent."
2. **External agents are invisible to `AgentRegistry`** — `find_by_capability` only scans the native `Agent` table (line 38). External agents with declared capabilities can't be found by Luna or coalitions.
3. **Single-shot HTTP dispatch in the adapter** — no retry, no circuit breaker, no fallback. One flaky webhook fails an entire coalition step. Temporal's `RetryPolicy` is the platform standard but only the chat-side activities use it; the inline `httpx.post()` in the adapter doesn't.
4. **Hire UX is fragmented across three forms** — `POST /external-agents`, `POST /agent-marketplace/subscribe`, `POST /agents/import`. Tenants don't have a single "find an agent that can do X → hire it" path.
5. **No cost rollup for external agents** — `AgentPerformanceSnapshot.agent_id` FKs to native `agents` only; the rollup workflow has nothing to aggregate over for external agents.
6. **No platform-native primitive for "hand off this task to agent X"** — Luna's persona says "let me delegate that to the data team", but the only mechanism today is the full `CoalitionWorkflow`. We want a 1-step Dynamic Workflow launch + an inline chat message instead of a coalition.

## Design principles for this work

- **Dynamic Workflows is the dispatcher.** Anything that looks like "send a task to an agent" becomes a Dynamic Workflow step. Audit = `WorkflowStepLog`. Chat-side surfacing = a `[handoff]` chat message linking to the run id.
- **No new audit table.** `WorkflowRun` + `WorkflowStepLog` already capture step input/output, tokens, cost, errors, run tree. RunsTab already renders them. Reuse.
- **No new in-chat A2A panel.** `CollaborationPanel` already shows live agent collaboration in the chat. For lightweight 1-step handoffs, surface a single `[handoff]` chat message with the run id; don't open the panel.
- **Patterns become workflow templates, not code.** The 4 new "coalition patterns" land as JSON entries in `workflow_templates.py`, not as new entries in `_PHASE_TO_ENTRY_TYPE`. Tenants install them via the existing `install_workflow_template` MCP tool.
- **External agents speak Dynamic Workflows.** The Dynamic Workflow `agent` step learns to dispatch to either native or external, gated on `kind`. Both honor the same RetryPolicy + reliability shim.

## Phased PR breakdown

### Phase 1 — Plumbing the gaps

**PR-A: MCP-SSE adapter for external agents** *(2–3 day equivalent)*
- Implement `_dispatch_mcp_sse` in `external_agent_adapter.py` (replaces the stub at line 21).
- **Reuse:** the FastMCP SSE client primitives already imported by `apps/mcp-server/`. Bearer auth via existing `_get_credential`.
- Surface tool inventory via the existing `external_agents.capabilities` JSONB; on first connect, populate it from the remote `tools/list` if empty.
- Test: register a remote MCP server, `POST /external-agents/{id}/test-task` → response includes the tool-call trace.

**PR-B: External agents joinable in capability discovery** *(1 day)*
- Extend `AgentRegistry.find_by_capability` to also query `external_agents.capabilities @> [capability]` (Postgres JSONB containment).
- Returns a discriminated union: each result keeps its DB shape but gains `kind: "native" | "external"`.
- `GET /agents/discover` is the natural surface; existing callers (Luna's `find_agent` path) become uniform.
- **Skip:** any heartbeat unification. External agents already have `status` + `last_seen_at`; reuse those columns instead of pushing them into the Redis availability key.

**PR-C: Reliability shim around `external_agent_adapter.dispatch`** *(2 days)*
- Wrap dispatch with a single `external_agent_call(agent, task, context, db)` helper:
  - Per-protocol timeout (default 30s, override in `external_agents.metadata_.timeout`).
  - Retries with exponential backoff (max 3, coefficient 2) — match the Temporal `RetryPolicy(maximum_attempts=3)` semantics already used by `coalition_workflow.py:27`.
  - Redis-backed circuit breaker keyed on `agent:breaker:{external_agent_id}` (open after 5 consecutive failures, half-open after 60s). Redis is already in use by `agent_router.py` and `agent_registry.py` — same client.
  - Optional `metadata_.fallback_agent_id` — if breaker open, dispatch to the fallback (recursively, depth 1) before giving up.
- Surface state in `external_agents.status`: `online | busy | error | breaker_open`.
- The Dynamic Workflow `agent` step (`dynamic_step.py:196`) routes through this helper when the target is external.

### Phase 2 — Hire UX

**PR-D: Unified Hire wizard** *(3 days)*
- New `/hire` route. Steps:
  1. **Capability search** — free-text + chips. Hits `/agents/discover` (now native + external + marketplace listings).
  2. **Source selector** — for each match: native (just open the agent), external owned (open the AgentDetailPage), marketplace listing (subscribe), or import-from-source (paste OpenAI Assistant ID, MCP server URL, webhook URL, or **drop a CrewAI/LangChain/AutoGen JSON** — this branch reuses `AgentImporter.parse_agent_definition`).
  3. **Preview** — capability badges, run a sample task via `/test-task`, show avg latency + success rate from the Phase 3 rollup.
  4. **Hire** — single submit; the wizard picks the right endpoint behind the scenes (`POST /external-agents`, `POST /agents/import`, or `POST /agent-marketplace/subscribe`).
- Replace AgentsPage's existing "Import Agent" modal entry-point with the "Hire Agent" CTA opening this wizard.
- **No new backend** — this PR is wiring on top of endpoints that already exist.

### Phase 3 — Cost rollup parity

**PR-E: External agents into the performance rollup** *(1 day)*
- Make `AgentPerformanceSnapshot.agent_id` nullable; add `external_agent_id UUID` nullable column with FK to `external_agents.id` (CHECK that exactly one of the two is set).
- Extend `AgentPerformanceRollupWorkflow` (`apps/api/app/workflows/agent_performance_rollup.py:10`) to also iterate external agents. Reuse the same window / metric set (success rate, latency p50/p95/p99, total_tokens, total_cost_usd).
- Hook the dispatch path: when the adapter knows the rate (OpenAI tokens × model price; webhook = manual `cost_per_call_usd` in `metadata_`), emit it onto the run's RL experience, which the rollup already aggregates.
- Surface cost on the AgentDetailPage for external agents — reuse the native agent Performance tab template.

### Phase 4 — A2A as Dynamic Workflows

**PR-F: `delegate_to_agent` MCP tool + inline chat handoff message** *(2 days)*
- Replaces the original PR-F (`agent_messages` table). **No new table.**
- New chat-side MCP tool `delegate_to_agent(agent_id, task, async=true|false, reason="")`:
  - Launches a single-step Dynamic Workflow with `step_type=agent` (or `cli_execute` if the target is external + MCP-SSE) targeting the recipient.
  - Returns the `WorkflowRun.id` immediately; if `async=false`, awaits up to 60s via existing `dynamic_workflow_launcher`.
- Side effect: writes a `ChatMessage` row tagged `kind="handoff"` with metadata `{run_id, recipient_agent_id, reason}`. The chat UI surfaces it as `→ Handoff to {Agent Name} (run #abc)` with a click-through to RunsTab filtered to that run.
- Audit trail = WorkflowRun + WorkflowStepLog. Replay = RunsTab. **Zero new audit infrastructure.**
- New companion tool `read_handoff_status(run_id)` — wraps the workflow run lookup so the originating agent can poll for completion.
- Luna's `handoff` presence state (already enumerated) gets used during the dispatch.

**PR-G: New collaboration patterns shipped as workflow templates** *(1 day)*
- Replaces the original PR-H (hardcoded patterns).
- Add four entries to `apps/api/app/services/workflow_templates.py`:
  - `peer_review` — propose → review (parallel) → revise → approve.
  - `sales_handoff` — qualify → enrich → handoff (uses PR-F primitive) → confirm.
  - `escalation_chain` — triage → investigate → human_approval (gated on `agent_policies`) → resolve.
  - `red_team_blue_team` — propose → attack → defend (parallel) → synthesize.
- Each template composes existing step types: `agent`, `condition`, `parallel`, `human_approval`, `mcp_tool`. **No edits to `collaboration_service.py` or `_PHASE_TO_*` mappings.** No edits to `coalition_workflow.py`.
- Templates appear in `WorkflowsPage` → Templates tab; tenants install them via the existing `install_workflow_template` MCP tool.
- The 5 existing `CoalitionWorkflow` patterns remain in service for Levi's MDM-style multi-round investigations; the new patterns are the "shorter-loop" alternatives that cover most peer-collaboration use cases.

**PR-H: `find_agent` MCP tool** *(half day)*
- Wraps `GET /agents/discover` so chat-side agents (Luna, sales-agent, etc.) discover native + external in one call.
- Registered in a new `apps/mcp-server/src/mcp_tools/agent_messaging.py` module (alongside the future `delegate_to_agent` from PR-F).
- Documented in `apps/api/app/agents/_bundled/luna/skill.md` so Luna picks the right primitive (use `find_agent` to resolve, `delegate_to_agent` for 1-step handoff, `start_collaboration` for multi-round investigation).

## What this plan explicitly does NOT do

- **No new `agent_messages` table.** Replaced by Dynamic Workflow runs + `ChatMessage(kind="handoff")` rows. Auditable via WorkflowRun.
- **No blackboard browser / replay UI.** RunsTab already renders the entire workflow run tree (tokens, cost, latency, errors, platform). CollaborationPanel already renders the live blackboard inline in the chat.
- **No edits to `_PHASE_TO_ENTRY_TYPE` / `_PHASE_TO_AUTHOR_ROLE`.** New patterns are workflow templates; no schema rigidity inherited.
- **No parallel registry for external agents.** `AgentRegistry` is the one registry; it learns about JSONB.
- **No new adapter abstraction for `a2a` protocol.** Collapsed into `delegate_to_agent` — external agents that opt into A2A surface as targets of a Dynamic Workflow `agent` step, just like native ones.
- **No new cost table.** `AgentPerformanceSnapshot` becomes the union table.

## Validation strategy

End-to-end smoke per phase, in order:

- **Phase 1 (PR-A → C):** register a remote MCP server, fire `test-task`, force a 500 to verify retry → breaker → fallback. Verify `agent:breaker:{id}` Redis key state. `find_by_capability` on a capability the external agent declared returns it with `kind="external"`.
- **Phase 2 (PR-D):** open `/hire`, search "lead scoring", subscribe to a marketplace listing → run sample task → cost shows up after the next rollup cycle.
- **Phase 3 (PR-E):** force a few openai_chat calls, run `AgentPerformanceRollupWorkflow` manually, inspect the `AgentPerformanceSnapshot` row keyed on `external_agent_id`. Cost strip on AgentDetailPage shows non-zero.
- **Phase 4 (PR-F → H):** from chat, ask Luna to "ask `lead-scoring` to grade this lead". Expect: `[handoff]` chat message, RunsTab shows the 1-step Dynamic Workflow run with full step log + tokens + cost. Reply lands in chat. Then install the `peer_review` template, run it via `POST /workflows/{id}/run`, watch the live SSE in `CollaborationPanel`.

## Open questions (not blockers)

- **`copilot_extension` protocol** — keep stubbed for now; address in a later PR once GitHub publishes the spec we'd target. Out of scope here.
- **Cross-tenant `delegate_to_agent`** — restricted to in-tenant for v1. Cross-tenant rental still goes through the marketplace subscribe flow.
- **Replay storage retention** — `workflow_step_logs` already has rows for every Dynamic Workflow execution. If volume becomes an issue, gate retention on a `tenant_features` flag (default 365 days) — separate cleanup PR.

## Net change vs. the v1 draft

| v1 draft | v2 (this) | Why |
|---|---|---|
| New `agent_messages` table (PR-F) | **Dropped.** Use Dynamic Workflow `agent` step + `ChatMessage(kind="handoff")` | WorkflowRun is the audit trail; no parallel system needed. |
| Blackboard browser UI (PR-G) | **Dropped.** RunsTab + CollaborationPanel already cover it. | Two surfaces already built; rebuilding would duplicate. |
| Hardcoded coalition patterns (PR-H) | **Now workflow templates (PR-G).** | Patterns as data, not code. Tenants can extend without a deploy. |
| Custom retry helper (PR-C) | **Reuse Temporal `RetryPolicy` semantics + Redis breaker.** | Match the platform's existing retry vocabulary. |
| Build hire UX from scratch (PR-D) | **Reuse `AgentImporter` for the import branches.** | crewai/langchain/autogen detection already exists. |
| New cost table (PR-E) | **Extend `AgentPerformanceSnapshot` + existing rollup workflow.** | Same metrics, same cadence, one table. |

Net: 9 PRs → **8 PRs**, but 2 of them are now zero-backend wiring jobs and one is a templates-only PR. Total surface area: ~5 days of focused work where the original v1 was closer to 14.
