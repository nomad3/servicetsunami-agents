# External Agents + A2A Enhancement Plan

**Status:** draft — opened 2026-04-26, follow-on work after the skills marketplace redesign (PRs #182–#193).

**Owners:** Simon (architecture, code) — Luna for chat-driven changes once the MCP shim lands.

## Goal

Make hiring, observing, and collaborating with **non-native agents** as friction-free as native ones, and make **agent-to-agent communication** observable, durable, and replayable. Today the building blocks exist (`external_agents` table, marketplace listings, `CoalitionWorkflow`, blackboard SSE) but the UX is incomplete and three of the five external-agent protocols aren't implemented.

## Inventory — what's already shipped

| Layer | Component | Status |
|---|---|---|
| Data | `external_agents` (protocol, endpoint, capabilities, health, stats) | ✅ migration 094 |
| Data | `agent_marketplace_listings` + `agent_marketplace_subscriptions` | ✅ migration 104 |
| Data | `collaboration_sessions` + `blackboards` + `blackboard_entries` | ✅ |
| API | `GET/POST/PUT/DELETE /external-agents` + `/{id}/health-check` + `/test-task` + `/callback/{id}` | ✅ |
| API | `GET /agent-marketplace/listings` + subscribe/approve/revoke | ✅ |
| API | `GET/POST /collaborations` + `/{id}/advance` + `/stream` (SSE) + `/trigger` | ✅ |
| Service | `external_agent_adapter` — `openai_chat`, `webhook` dispatch | ✅ partial (3 of 5 protocols missing) |
| Service | `collaboration_service` — 5 patterns: propose_critique_revise, plan_verify, research_synthesize, debate_resolve, incident_investigation | ✅ |
| Workflow | `CoalitionWorkflow` (orchestration queue) | ✅ |
| UI | AgentsPage "External Agents" section + Import modal | ✅ basic |
| UI | `CollaborationPanel` live phase timeline | ✅ |

## Gaps

1. **Hire UX is fragmented.** Users can either (a) `POST /external-agents` directly, (b) import a CrewAI/LangChain config, or (c) `POST /agent-marketplace/subscribe`. No unified "hire an agent" flow with capability search → listing → preview → one-click hire.
2. **Two protocols missing in the adapter:** `mcp_sse` (most important — every Claude Code / Gemini skill is one of these) and `a2a` (peer-to-peer). `copilot_extension` is partial.
3. **External agents aren't in the capability registry.** `GET /agents/discover?capability=x` returns native agents only; external agents with `capabilities: ["code", "search"]` are invisible to coalitions.
4. **No direct agent-to-agent DM.** Coalition is the only way for two agents to talk — heavy for a quick "hand off this lead to sales-agent". Want a lightweight `agent_message` channel.
5. **Blackboard is write-once-read-stream.** Live SSE works; historical search/replay is API-only — no UI to browse who said what across past collaborations.
6. **No reliability layer for external calls.** Single attempt, no retry policy, no circuit breaker, no fallback-to-native on failure. One flaky webhook can fail a whole coalition.
7. **Cost tracking gap.** `external_agents.task_count` increments but no $ rollup; tenant admins can't see "you spent $42 on the OpenAI Assistant `lead-researcher` this week".
8. **Coalition pattern catalog is small (5).** Common workflows missing: `peer_review`, `sales_handoff`, `escalation_chain`, `red_team_blue_team`.

## Phased PR breakdown

### Phase 1 — Foundations (unblock everything else)

**PR-A: MCP-SSE adapter** *(2–3 day equivalent)*
- Implement `_dispatch_mcp_sse` in `external_agent_adapter.py`. SSE handshake, tool-list fetch, tool-call dispatch via `mcp` SDK already in `apps/mcp-server/`.
- Reuse `IntegrationCredential` for bearer auth where the remote MCP requires it.
- Test: register a remote MCP server (e.g. an external read-only Jira MCP), `POST /external-agents/{id}/test-task` → tool-call traces in the response.

**PR-B: External agents in the capability registry** *(1 day)*
- Extend `apps/api/app/services/agent_registry.py` so `find_by_capability` searches `external_agents.capabilities` JSONB alongside native agents.
- Returns a uniform `{kind: "native"|"external", id, name, capabilities, health}` envelope so callers don't branch.
- `CoalitionWorkflow` already accepts an agent set — verify external agents flow through `external_agent_adapter` instead of `code-worker`.

**PR-C: Reliability shim** *(2 days)*
- New `external_agent_call` helper wraps `adapter.dispatch` with: timeout (per-protocol default), exponential-backoff retry (max 3, configurable in `metadata_`), circuit breaker (open after 5 consecutive failures, half-open after 60s), optional `fallback_agent_id`.
- Persist breaker state in Redis; surface in `external_agents.status` (`online | busy | error | breaker_open`).
- Wire into the adapter's main entrypoint so every dispatch path benefits.

### Phase 2 — Hire UX

**PR-D: Unified Hire wizard** *(3 days)*
- New `/hire` route + page. Steps:
  1. **Capability search** — free-text + chips. Hits `/agents/discover` (native + external + marketplace listings).
  2. **Source picker** — for each match: native (link), external owned (link), marketplace listing (subscribe), import-from-URL (OpenAI Assistant ID, MCP server URL, webhook URL, CrewAI/LangChain JSON).
  3. **Preview** — capability badges, sample task (`/test-task`), avg latency, success rate, cost-per-call estimate, owner (for marketplace).
  4. **Hire** — one POST: creates `external_agents` row OR `agent_marketplace_subscriptions` row OR native `agents` import.
- Replace the current AgentsPage "Import Agent" modal entry-point with a "Hire Agent" CTA that opens this wizard.

**PR-E: Cost rollup + transparency** *(1 day)*
- Add `external_agents.cost_usd_total` (decimal) and `cost_per_call_usd` (decimal) — populated when the adapter knows the rate (OpenAI tokens × model price; webhook = manual config).
- New endpoint `GET /external-agents/{id}/usage?from=&to=` returns task count + token usage + estimated cost over a window.
- Surface a small cost strip on the AgentDetailPage for external agents.

### Phase 3 — Agent-to-agent communication

**PR-F: Direct agent-to-agent message channel** *(2 days)*
- New table `agent_messages(id, tenant_id, sender_agent_id, recipient_agent_id, kind, content, in_reply_to_id, created_at)` — persistent message log.
- New MCP tool `send_agent_message(recipient_agent_id, content, kind="task"|"reply"|"notify")` — chat-side primitive any agent can call.
- New MCP tool `read_agent_messages(since?, kind?)` — recipient's inbox.
- `AgentRouter` learns to deliver: native → fan into the recipient agent's chat session; external → `external_agent_adapter.dispatch(..., context={inbox: [msgs]})`.
- Outcome: a sales-agent can hand off a qualified lead to a billing-agent without spinning up a coalition.

**PR-G: Blackboard browser + replay UI** *(2 days)*
- New `WorkflowsPage` tab "Collaborations" or extend the existing collaboration panel.
- Lists past `collaboration_sessions` filtered by date/pattern/participating agent.
- Click → blackboard timeline (existing SSE renderer in replay mode reading `blackboard_entries` instead of Redis stream).
- Fragility / agreement scores already computed by the auto-quality scorer — surface them inline.

### Phase 4 — More patterns + ergonomics

**PR-H: New coalition patterns** *(1 day)*
- Add 4 patterns to `PATTERN_PHASES` with their phase→role bindings:
  - `peer_review` — propose → review → revise → approve
  - `sales_handoff` — qualify → enrich → handoff → confirm
  - `escalation_chain` — triage → investigate → escalate (gated on `agent_policies`) → resolve
  - `red_team_blue_team` — propose → attack → defend → synthesize (validation pattern for risky changes)
- Update `_PHASE_TO_ENTRY_TYPE` and `_PHASE_TO_AUTHOR_ROLE` tables in `collaboration_service.py`.
- Add 2-line example seed entries each so users can see them in the Hire/Coalitions UI.

**PR-I: Agent-side discovery from chat** *(1 day)*
- New MCP tool `find_agent(capability, status?)` — wraps `/agents/discover` for both native + external. Lets Luna say "I'll grab the `lead-scoring` agent for this".
- New MCP tool `delegate_to_agent(agent_id, task, async=true|false)` — dispatches a single task without a full coalition. Returns task id; `read_agent_messages` polls for the response.
- Documented in luna's `skill.md` so the chat path uses them.

## Validation strategy

End-to-end smoke per phase:

- **Phase 1**: register a remote MCP server, fire `test-task`, verify tool-trace + reliability (force a 500, watch retries → breaker → fallback).
- **Phase 2**: open `/hire`, search "lead scoring", subscribe to a marketplace listing, run a sample task, see cost rollup tick.
- **Phase 3**: from chat, ask Luna to hand off a deal to `sales-agent`; verify `agent_messages` row + recipient inbox + reply round-trip. Then open the Blackboard browser and replay a past coalition.
- **Phase 4**: trigger each new pattern via `POST /collaborations` and watch the live SSE.

## Out of scope (intentionally)

- **Tenant-to-tenant routing of agent_messages.** Marketplace already covers cross-tenant rental; a global agent-DM mesh is a separate trust problem.
- **Voice / video between agents.** All comms stay text/structured; the existing transcription + Luna voice path covers human ↔ agent.
- **Auto-discovery of remote MCP servers.** Users still register endpoints explicitly; mDNS-style auto-discovery has security implications outside this plan.

## Open questions

- **Adapter for `a2a` protocol (`external_agents.protocol = "a2a"`)** — what does this actually mean operationally? It overlaps with PR-F (`agent_messages`). Decision: collapse `a2a` protocol into the new agent-message channel so external agents speak the same primitive native ones do, instead of a separate transport.
- **Marketplace approval flow** — current code allows tenant admins to approve cross-tenant subscriptions. Do we want a per-listing autopay rate-limit or a per-call signed receipt? Punt to a later billing PR.
- **Replay storage** — blackboard entries already persist; how long do we keep them? Default: tenant-configurable retention, default 365 days. Track in tenant_features.

## What this plan does NOT change

- Native agent runtime (`code-worker` / `ChatCliWorkflow`) — untouched.
- The skills marketplace surface shipped in PR1–PR8 — additive only; new MCP tools land alongside the existing set.
- Existing collaboration patterns and their seed data — additive.
