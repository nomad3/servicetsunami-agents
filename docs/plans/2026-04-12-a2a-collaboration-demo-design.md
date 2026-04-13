# Agent-to-Agent Collaboration: Live Demo Design

**Date:** 2026-04-12
**Author:** Simon Aguilera
**Status:** Approved
**Timeline:** 1 week
**Target:** Levi Strauss & Co. Engineering Leadership demo

---

## 1. Objective

Wire the existing Coalition Workflow, Shared Blackboard, and Collaboration Service into a fully functional end-to-end agent-to-agent collaboration system. The system must be demo-ready with:

- **(A) Live demo**: Trigger a multi-agent investigation in the chat UI. Levi's team watches 4 agents collaborate in real-time — blackboard entries appearing, phase transitions visible, final synthesis delivered.
- **(B) Post-execution walkthrough**: Show execution traces, blackboard entries, and agent reasoning in an audit/replay view after completion.

---

## 2. Demo Scenario

**4-agent incident investigation** on a master data discrepancy. The scenario is parameterized in seed data (not hardcoded in workflow code) so it can be refined to match Levi's actual MDM reality before demo day.

**Placeholder scenario**: Pricing discrepancy detected on 1,200+ SKUs — ERP and e-commerce catalog out of sync across regions due to a schema migration that introduced a breaking constraint without backfilling existing records.

**Agents**:
1. **Triage Agent** — classifies severity, identifies affected systems from knowledge graph, scopes blast radius
2. **Data Investigator** — pulls observations, correlates timeline, identifies the breaking change
3. **Root Cause Analyst** — confirms root cause, calculates cascade impact, validates the hypothesis
4. **Incident Commander** — synthesizes action plan (immediate remediation + preventive measures)

**Trigger message**: User types a natural language incident description in chat. The agent router detects a complex task and dispatches the CoalitionWorkflow. A manual trigger endpoint also exists for demo reliability.

---

## 3. Architecture

### 3.1. End-to-End Flow

```
User: "Investigate: master data pricing discrepancy on 1,200+ SKUs"
  │
  ├─ Chat SSE → emits user_saved
  │
  ├─ Agent Router → detects complex task → dispatch_coalition()
  │
  └─ Temporal: CoalitionWorkflow (agentprovision-orchestration queue)
       │
       ├─ 1. select_coalition_template()
       │     → pattern: incident_investigation
       │     → roles: {triage: agent-A, investigator: agent-B,
       │               analyst: agent-C, commander: agent-D}
       │
       ├─ 2. initialize_collaboration()
       │     → creates Blackboard (Postgres)
       │     → creates CollaborationSession
       │     → publishes collaboration_started → Redis channel: session:{chat_session_id}
       │     → Session events stream (GET /sessions/{id}/events) picks it up
       │     → Frontend opens collaboration panel + subscribes to collaboration/{id}/stream
       │
       ├─ 3. execute_collaboration_step() × 4 phases
       │     For each phase:
       │       → read blackboard entries (shared context)
       │       → build phase-specific prompt
       │       → dispatch CodeTaskWorkflow (agentprovision-code queue)
       │       → code-worker picks CLI via RL routing / tenant config
       │       → CLI response → advance_phase() → write to blackboard
       │       → publish events → Redis pub/sub
       │       → async: consensus_reviewer scores contribution → RL
       │       → frontend panel updates in real-time
       │
       ├─ 4. finalize_collaboration()
       │     → Commander synthesizes final report via CLI
       │     → publishes collaboration_completed → Redis
       │     → writes final message to chat session
       │     → triggers provider_council review (async)
       │     → logs collaboration_outcome RL experience
       │     → updates CoalitionTemplate stats
       │
       └─ Frontend:
            ├─ Chat: agent messages appear as labeled turns
            └─ Panel: phase timeline + blackboard feed + status bar
```

### 3.2. CLI Dispatch (Platform-Agnostic)

The collaboration workflow never references a specific CLI. Each collaboration phase dispatches a child `ChatCliWorkflow` on the `agentprovision-code` queue. `ChatCliWorkflow` is designed for conversational agent sessions — it takes a message and returns `response_text`, which is exactly what a collaboration phase produces (textual analysis, not code commits or PRs).

The `platform` field in `ChatCliInput` is determined by the `prepare_collaboration_step` activity:
1. Reads `tenant_features.default_cli_platform` for the tenant's configured default
2. Checks RL routing recommendation via `rl_routing.get_best_platform()`
3. Passes the chosen platform to `ChatCliInput(platform=..., message=phase_prompt, ...)`
4. The code-worker dispatches to Gemini CLI, Claude Code CLI, or Codex CLI based on that value

Returns `ChatCliResult.response_text` — the agent's contribution text — which goes directly to the blackboard via `advance_phase()`.

`CodeTaskWorkflow` is NOT used for collaboration phases. It is designed for code execution tasks (creates branches, commits, opens PRs). Collaboration phases are analysis and reasoning tasks, making `ChatCliWorkflow` the correct contract.

### 3.3. Context Inheritance (Shared Blackboard)

Each agent inherits the full blackboard state from all prior agents:

| Phase | Agent | Sees on Blackboard |
|-------|-------|--------------------|
| Triage | Triage Agent | Incident description only |
| Investigate | Data Investigator | Incident + triage output |
| Analyze | Root Cause Analyst | Incident + triage + investigation |
| Command | Incident Commander | Everything — full chain of reasoning |

The prompt for each phase includes:
- The original incident description
- All blackboard entries (ordered by `board_version`)
- Phase-specific instructions ("You are the Root Cause Analyst. Your job is to...")
- Relevant knowledge graph entities from `memory.recall()`

---

## 4. Real-Time SSE via Redis Pub/Sub

### 4.1. Event Flow

```
Coalition Activity (Temporal worker)
    → writes blackboard entry (Postgres)
    → publishes event to Redis channel: collaboration:{id}
        ↓
SSE endpoint: GET /api/v1/collaborations/{id}/stream
    → subscribes to Redis channel: collaboration:{id}
    → streams events to frontend as SSE
```

### 4.2. Redis Channel Pattern

- Channel name: `collaboration:{collaboration_id}`
- Scoped per collaboration session
- Auto-expires when `collaboration_completed` is published — frontend closes the EventSource

### 4.3. SSE Event Types

```json
collaboration_started: {
  "collaboration_id": "uuid",
  "pattern": "incident_investigation",
  "agents": [
    {"slug": "triage-agent", "role": "triage_agent", "phase": "triage"},
    {"slug": "data-investigator", "role": "investigator", "phase": "investigate"},
    {"slug": "root-cause-analyst", "role": "analyst", "phase": "analyze"},
    {"slug": "incident-commander", "role": "commander", "phase": "command"}
  ],
  "blackboard_id": "uuid"
}

phase_started: {
  "phase": "investigate",
  "agent_slug": "data-investigator",
  "agent_role": "investigator",
  "round": 1
}

blackboard_entry: {
  "entry_id": "uuid",
  "entry_type": "evidence",
  "author_slug": "data-investigator",
  "author_role": "researcher",
  "content_preview": "First 200 chars...",
  "content_full": "Full contribution text",
  "confidence": 0.85,
  "board_version": 3
}

phase_completed: {
  "phase": "investigate",
  "agent_slug": "data-investigator",
  "entry_id": "uuid",
  "board_version": 3,
  "confidence": 0.85
}

collaboration_completed: {
  "collaboration_id": "uuid",
  "consensus": "yes",
  "rounds": 1,
  "duration_seconds": 47,
  "final_report": "Full synthesized report text"
}
```

### 4.4. Publisher Service

New file: `apps/api/app/services/collaboration_events.py`

```python
def publish_event(collaboration_id: str, event_type: str, payload: dict) -> None:
    """Publish a collaboration event to Redis pub/sub."""
    # Uses the existing Redis connection from K8s
    # Channel: collaboration:{collaboration_id}
    # Message: JSON with event_type + payload + timestamp

def subscribe_stream(collaboration_id: str):
    """Generator that yields SSE events from Redis subscription."""
    # Includes 15s heartbeat to keep connection alive
```

### 4.5. Missed Event Catch-Up

If the frontend connects after the collaboration started (page refresh, late navigation):

1. SSE endpoint first queries Postgres for all existing blackboard entries
2. Emits them as a `catch_up` batch (ordered by `board_version`)
3. Then switches to the live Redis stream
4. No events lost — Postgres is the source of truth, Redis is the real-time transport

---

## 5. Frontend

### 5.1. Chat Integration

When the chat SSE stream emits `collaboration_started`, the chat UI:

1. Shows a **collaboration card** inline — compact banner: "Incident Investigation started — 4 agents collaborating" with an expand button
2. Each agent's final phase contribution appears as a **labeled chat message**: agent avatar, name, role badge (e.g., "Triage Agent — triage_agent"), and the contribution text
3. The final report from the Commander appears as the assistant's response message

### 5.2. Collaboration Panel

New component: `apps/web/src/components/CollaborationPanel.js`

A slide-out right panel (split view) with two modes: **live** and **replay**.

**Phase Timeline** (top):
- Horizontal stepper: `Triage → Investigate → Analyze → Command`
- Active phase pulses with animation
- Completed phases show green check + duration
- Each step shows the agent avatar assigned to it

**Blackboard Feed** (middle, scrollable):
- Live append-only feed of blackboard entries as they arrive via SSE
- Each entry is a card:
  - Agent name + role badge
  - Entry type label (proposal, evidence, critique, synthesis)
  - Content (full text, expandable for long contributions)
  - Confidence score bar
  - Board version + timestamp

**Status Bar** (bottom):
- Round counter, elapsed time, consensus status
- When complete: "Consensus reached in 1 round, 47s"

### 5.3. Replay/Playback Mode

When viewing a completed collaboration (from "Collaborations" tab in chat session detail):

- **Replay button**: Plays back all blackboard entries in sequence with their original timestamps, animating the phase timeline progression
- **Speed controls**: 1x (real-time), 2x, 5x, skip-to-end
- **Step-through mode**: Arrow keys or click to step through entries one by one. Each step highlights the active phase, the contributing agent, and scrolls the blackboard feed to that entry
- **Data source**: Pure Postgres via `GET /api/v1/collaborations/{id}` — no Redis needed for replay. Frontend sorts by `board_version` and animates client-side

This doubles as the Part B walkthrough tool. During the Levi's demo, after the live run, switch to replay mode and step through each agent's reasoning at your own pace.

### 5.4. Mode Toggle

The `CollaborationPanel` handles both modes:
- **Live mode**: Active when collaboration is in progress. SSE via Redis pub/sub. Entries appear in real-time.
- **Replay mode**: Active when viewing a completed collaboration. Postgres snapshot. Playback controls visible.

Toggle is automatic based on collaboration status, with manual override.

---

## 6. New Collaboration Pattern

Add `incident_investigation` to `PATTERN_PHASES` and `PHASE_REQUIRED_ROLES`:

```python
PATTERN_PHASES = {
    # ... existing patterns ...
    "incident_investigation": ["triage", "investigate", "analyze", "command"],
}

PHASE_REQUIRED_ROLES = {
    # ... existing phases ...
    "triage": ["triage_agent"],
    "investigate": ["investigator"],
    "analyze": ["analyst"],
    "command": ["commander"],
}
```

Phase-to-entry-type mapping:
- `triage` → `EntryType.EVIDENCE` (classification + blast radius)
- `investigate` → `EntryType.EVIDENCE` (data correlation + findings)
- `analyze` → `EntryType.CRITIQUE` (root cause confirmation + impact calculation)
- `command` → `EntryType.SYNTHESIS` (action plan)

Phase-to-author-role mapping:
- `triage` → `AuthorRole.RESEARCHER`
- `investigate` → `AuthorRole.RESEARCHER`
- `analyze` → `AuthorRole.CRITIC`
- `command` → `AuthorRole.SYNTHESIZER`

---

## 7. Alignment with Council & Federation

### 7.1. Consensus Reviewer (3-Agent Local Council)

Each agent's contribution during collaboration is scored by the consensus reviewer — same as any regular chat response. The 3 reviewers (accuracy, helpfulness, persona) run locally via Gemma 4, async, never blocking the collaboration flow.

Scores feed into:
- Per-step RL experience
- Coalition template `avg_quality_score` (learns which agent-role pairings work best)

### 7.2. Provider Review Council (Multi-CLI Review)

The Incident Commander's final synthesis — the deliverable the user sees — triggers `_maybe_trigger_provider_council()`. This is the high-stakes output that warrants full multi-provider review (Claude, Codex, Gemma 4).

Individual phase contributions do NOT trigger the provider council (too slow, 4x cost). Only the final report.

Trigger condition: collaboration outputs are flagged with `collaboration_output=True`, which qualifies as "side-effect" tier in the provider council decision gate.

### 7.3. Federation (Future-Compatible)

Federation (STP Protocol, Phase 4) is out of scope for this week. But the design is compatible:

- Blackboard entries have `author_agent_slug` — in federation this becomes `node_id:agent_slug`
- `blackboard_entries` gets an optional `source_node_id` column (nullable, defaults to local)
- Collaboration SSE events use a stable schema that a remote node could publish to
- `CodeTaskWorkflow` dispatch is Temporal-based — Temporal supports multi-cluster replication natively

---

## 8. RL Integration

### 8.1. Per-Step RL (`decision_point: collaboration_step`)

- **State**: phase name + blackboard context (truncated) + agent slug
- **Action**: the agent's contribution (what it wrote to the blackboard)
- **Reward**: consensus reviewer score (async, non-blocking)
- **Stored in**: `rl_experience` with `reward_components` including 6-dimension breakdown

Teaches: which agents perform best at which collaboration roles.

### 8.2. Per-Collaboration RL (`decision_point: collaboration_outcome`)

- **State**: task description + pattern + role assignments
- **Action**: the coalition template selected
- **Reward**: derived from:
  - Final report quality (provider council score if triggered, else consensus reviewer)
  - Consensus speed (fewer rounds = higher reward)
  - User feedback (thumbs up/down on the final chat message)
- **Updates**: `CoalitionTemplate.avg_quality_score`, `avg_rounds_to_consensus`, `avg_cost_usd`

Teaches: which patterns work best for which task types.

### 8.3. Platform Routing RL (`decision_point: chat_response`)

Each CLI call within the collaboration is a normal code-worker execution. The existing RL platform routing already captures which CLI performed well. No new code needed.

### 8.4. Cost Tracking

Each collaboration step logs tokens + cost to the coalition outcome. The RL system learns cost-efficiency — if two templates produce similar quality but one costs 3x more, the cheaper one gets preferred.

---

## 9. API Endpoints

All new endpoints are additive. **Existing endpoints at `/api/v1/collaborations` are unchanged**: `POST /collaborations` (create session on existing blackboard), `GET /collaborations/{id}` (returns `CollaborationSessionInDB`), `GET /collaborations/{id}/advance` — all remain as-is.

### 9.1. Session-Level Event Stream (NEW)

```
GET /api/v1/chat/sessions/{session_id}/events
```

- **Long-lived SSE stream** — the frontend opens this once per chat session and keeps it open throughout
- Subscribes to Redis channel: `session:{chat_session_id}`
- Coalition activities publish `collaboration_started` to this channel when the workflow begins
- Frontend receives `collaboration_started` and knows to render the collaboration panel
- Also receives session-level events: `collaboration_completed` (signals the panel to show the final result)
- Heartbeat: 15s keep-alive
- **This is the solution to the request-scoped SSE problem**: the message stream (`POST .../messages/stream`) is per-message and closes after `done`. The session events stream is separate, stays open, and carries async workflow notifications.

### 9.2. Collaboration Detail Stream (NEW sub-path)

```
GET /api/v1/collaborations/{collaboration_id}/stream
```

- Detailed SSE for a specific collaboration — blackboard entries, phase transitions
- Subscribes to Redis channel: `collaboration:{collaboration_id}`
- Catch-up: replays missed events from Postgres on connect (ordered by `board_version`), then live Redis
- Frontend opens this after receiving `collaboration_started` on the session stream
- Heartbeat: 15s keep-alive
- Auto-closes on `collaboration_completed` event

### 9.3. Collaboration Full Detail (NEW sub-path)

```
GET /api/v1/collaborations/{collaboration_id}/detail
```

- Returns: collaboration session + all blackboard entries (ordered by `board_version`) + phase history
- Distinct from existing `GET /collaborations/{id}` which returns only `CollaborationSessionInDB`
- Powers: replay/playback UI and post-demo walkthrough

### 9.4. Collaboration List by Chat Session (NEW)

```
GET /api/v1/chat/sessions/{session_id}/collaborations
```

- Lists all collaborations linked to a chat session via `blackboards.chat_session_id`
- Powers: "Collaborations" tab in the chat session detail view

### 9.5. Manual Trigger (NEW sub-path)

```
POST /api/v1/collaborations/trigger
Body: {
  "chat_session_id": "uuid",
  "task_description": "string",
  "pattern": "incident_investigation",  // optional, auto-detected if omitted
  "role_overrides": {}                  // optional, uses template defaults if omitted
}
```

- Distinct from existing `POST /collaborations` (which creates a session on an existing blackboard)
- Bypasses intent detection, directly starts CoalitionWorkflow via Temporal
- Demo reliability fallback: if the automatic trigger from agent_router doesn't fire, use this

---

## 10. Database Changes

### 10.1. New Column

```sql
ALTER TABLE blackboard_entries ADD COLUMN source_node_id VARCHAR(100) DEFAULT NULL;
```

Nullable, defaults to local. Future-proofing for federation without adding complexity now.

### 10.2. New Collaboration Pattern Data

The `incident_investigation` pattern is defined in code (`schemas/collaboration.py`), not in a migration. The pattern's phases and role mappings are Python constants.

### 10.3. Seed Script

`apps/api/scripts/seed_incident_demo.py` — idempotent script that creates:

- 4 agent records (Triage Agent, Data Investigator, Root Cause Analyst, Incident Commander)
- Agent relationships (delegation chain)
- Knowledge graph entities (master data systems, pipelines, databases)
- Knowledge graph observations (discrepancy data, timeline, symptoms)
- Knowledge graph relations (system dependencies)
- Coalition template for `incident_investigation` pattern

Parameterized: scenario details are in a config dict at the top of the script, easily swappable for Levi's-specific data.

---

## 11. Pre-Existing Bugs to Fix

These issues exist in the current codebase and must be fixed as part of this work, since the coalition flow has never run end-to-end.

### 11.1. BlackboardCreate Missing chat_session_id

`coalition_activities.py:84` passes `chat_session_id=UUID(chat_session_id)` to `BlackboardCreate`, but neither the schema (`schemas/blackboard.py`) nor the model (`models/blackboard.py`) has a `chat_session_id` field. This would crash at runtime.

**Fix**:
- Add `chat_session_id` column (UUID, nullable, FK to `chat_sessions.id`) to the `Blackboard` model
- Add `chat_session_id` field to `BlackboardCreate` schema
- Migration: `ALTER TABLE blackboards ADD COLUMN chat_session_id UUID REFERENCES chat_sessions(id)`
- This also enables the `GET /api/v1/chat/sessions/{session_id}/collaborations` endpoint — query collaborations via `blackboards.chat_session_id → collaboration_sessions.blackboard_id`

### 11.2. Pattern Name Hyphen/Underscore Mismatch

`select_coalition_template()` in `coalition_activities.py:32-40` returns patterns with hyphens (`"propose-critique-revise"`), but `CollaborationPattern` enum and `PATTERN_PHASES` dict use underscores (`"propose_critique_revise"`). This means `create_session()` would fail Pydantic validation.

**Fix**: Change `select_coalition_template()` to return underscored pattern names matching the enum. This is a 3-line fix in the activity.

### 11.3. No Redis Client in API

The API has no Redis dependency. Redis is deployed in K8s but the Python API has never used it.

**Fix**:
- Add `redis[hiredis]` to `apps/api/requirements.txt`
- Add `REDIS_URL: str = "redis://redis:6379/0"` to `apps/api/app/core/config.py` Settings
- Create a Redis client singleton in `collaboration_events.py` (or a shared `app/core/redis.py`)
- Add `REDIS_URL` to Helm values for the API pod (env var from configmap)

### 11.4. CLI Dispatch via ChatCliWorkflow Child Workflow

`CoalitionWorkflow` runs on `agentprovision-orchestration`. `ChatCliWorkflow` runs on `agentprovision-code` (same worker as `CodeTaskWorkflow`). Cross-queue child workflows are supported by Temporal natively.

**Decision**: Use `workflow.execute_child_workflow(ChatCliWorkflow.run, ..., task_queue="agentprovision-code")` from within `CoalitionWorkflow`. `ChatCliWorkflow` is the correct contract — it takes `ChatCliInput` and returns `ChatCliResult.response_text`, which is the agent's textual contribution. `CodeTaskWorkflow` is NOT used — it creates PRs and commits, which is wrong for analysis phases.

The three-step refactoring of `CoalitionWorkflow.run()`:

1. **Activity**: `prepare_collaboration_step()` — reads blackboard, builds phase prompt, resolves CLI platform via RL routing, returns `ChatCliInput`
2. **Child workflow**: `ChatCliWorkflow` on `agentprovision-code` queue — executes the CLI call
3. **Activity**: `record_collaboration_step()` — takes `ChatCliResult.response_text`, calls `advance_phase()`, writes to blackboard, publishes Redis events

```python
for i in range(session_info["max_rounds"]):
    # 1. Prepare: read blackboard, build ChatCliInput
    chat_input = await workflow.execute_activity(prepare_collaboration_step, ...)
    # chat_input is ChatCliInput(platform="gemini", message=phase_prompt,
    #                            tenant_id=..., instruction_md_content=agent_persona)

    # 2. Execute: dispatch ChatCliWorkflow to code-worker queue
    cli_result = await workflow.execute_child_workflow(
        ChatCliWorkflow.run,
        chat_input,
        task_queue="agentprovision-code",
        execution_timeout=timedelta(minutes=5),
    )
    # cli_result is ChatCliResult(response_text="...", success=True)

    # 3. Record: advance_phase() + blackboard write + Redis publish
    step_result = await workflow.execute_activity(
        record_collaboration_step,
        args=[session_info["collaboration_id"], cli_result.response_text, ...],
    )

    if step_result.get("consensus_reached"):
        break
```

---

## 12. Schema/Enum Additions

Beyond the new pattern data, these enum and mapping additions are required:

### 12.1. CollaborationPattern Enum

Add to `schemas/collaboration.py`:
```python
class CollaborationPattern(str, Enum):
    # ... existing ...
    INCIDENT_INVESTIGATION = "incident_investigation"
```

### 12.2. CollaborationPhase Enum

Add to `schemas/collaboration.py`:
```python
class CollaborationPhase(str, Enum):
    # ... existing ...
    TRIAGE = "triage"
    INVESTIGATE = "investigate"
    ANALYZE = "analyze"
    COMMAND = "command"
```

### 12.3. Phase Mappings in advance_phase()

Add to `collaboration_service.py` `phase_to_entry_type`:
```python
"triage": EntryType.EVIDENCE,
"investigate": EntryType.EVIDENCE,
"analyze": EntryType.CRITIQUE,
"command": EntryType.SYNTHESIS,
```

Add to `phase_to_role`:
```python
"triage": AuthorRole.RESEARCHER,
"investigate": AuthorRole.RESEARCHER,
"analyze": AuthorRole.CRITIC,
"command": AuthorRole.SYNTHESIZER,
```

Add a guard for unknown phases — raise ValueError instead of silently defaulting to proposal/contributor.

### 12.4. Terminal Phase Loop-Back

`advance_phase()` loops back to `"critique"` on disagreement, falling back to index 1 if no critique phase exists. For `incident_investigation`, this means looping back to `"investigate"` (index 1). This is semantically correct — re-investigate on disagreement. No code change needed, but implementation should verify this behavior in tests.

### 12.5. Template Selector Keywords

Add incident/investigation keywords to `select_coalition_template()`:
```python
if any(k in task_lower for k in ["incident", "investigate", "outage", "degraded", "crash", "alert"]):
    pattern = "incident_investigation"
    required_roles = ["triage_agent", "investigator", "analyst", "commander"]
```

---

## 13. Files Changed / Created

### New Files

| File | Purpose |
|------|---------|
| `apps/api/app/services/collaboration_events.py` | Redis pub/sub publisher + client singleton for collaboration events |
| `apps/web/src/components/CollaborationPanel.js` | Phase timeline + blackboard feed + status bar + replay |
| `apps/web/src/components/CollaborationPanel.css` | Ocean theme styling for the panel |
| `apps/api/scripts/seed_incident_demo.py` | Parameterized demo seed data |
| `apps/api/migrations/091_blackboard_chat_session_and_source_node.sql` | Add `chat_session_id` to blackboards + `source_node_id` to blackboard_entries |

### Modified Files

| File | Change |
|------|--------|
| `apps/api/app/workflows/coalition_workflow.py` | Refactor step loop: prepare activity → child ChatCliWorkflow (agentprovision-code) → record activity |
| `apps/api/app/workflows/activities/coalition_activities.py` | Replace 2 stubs with `prepare_collaboration_step` (returns `ChatCliInput`) + `record_collaboration_step` (takes `ChatCliResult.response_text`). Fix pattern name hyphen→underscore mismatch. Add incident keywords to template selector |
| `apps/api/app/schemas/collaboration.py` | Add `INCIDENT_INVESTIGATION` pattern enum, `TRIAGE/INVESTIGATE/ANALYZE/COMMAND` phase enums, `PATTERN_PHASES` + `PHASE_REQUIRED_ROLES` entries |
| `apps/api/app/services/collaboration_service.py` | Add phase-to-entry-type and phase-to-role mappings for new phases. Add unknown-phase guard |
| `apps/api/app/services/blackboard_service.py` | Pass `chat_session_id` through in `create_blackboard()` |
| `apps/api/app/models/blackboard.py` | Add `chat_session_id` column to `Blackboard` model |
| `apps/api/app/schemas/blackboard.py` | Add `chat_session_id` field to `BlackboardCreate` and `BlackboardInDB` |
| `apps/api/app/api/v1/collaborations.py` | Add `/stream`, `/detail`, `/trigger` endpoints (existing CRUD endpoints unchanged) |
| `apps/api/app/api/v1/chat.py` | Add `GET /sessions/{id}/events` — long-lived session-level SSE stream (Redis `session:{id}` channel) |
| `apps/api/app/core/config.py` | Add `REDIS_URL` setting |
| `apps/api/requirements.txt` | Add `redis[hiredis]` dependency |
| `apps/web/src/pages/ChatPage.js` | Wire collaboration panel + SSE event handling |
| `helm/values/agentprovision-api-local.yaml` | Add `REDIS_URL` env var |

---

## 14. Out of Scope

- Federation daemon / multi-node mesh (Phase 4)
- Custom collaboration pattern builder UI
- Collaboration analytics dashboard
- Non-incident patterns (research-synthesize already works in stub form — can be wired later using the same approach)

### Future Work: Dynamic Pattern Registry

`PATTERN_PHASES` and `PHASE_REQUIRED_ROLES` are currently Python constants in `schemas/collaboration.py`. This is consistent with existing code but creates a deployment dependency for new collaboration types.

Post-demo, these should migrate to a DB-backed registry (a `collaboration_patterns` table with `name`, `phases` JSONB, `role_phase_map` JSONB) — similar to how `dynamic_workflows` already handles JSON-defined workflow step types. New patterns (e.g., `security_review`, `data_quality_audit`) could then be added via API or UI without a code deploy. The implementation contract (`advance_phase()`, `create_session()`) does not need to change — only the source of the phase/role data.

### Redis Connection Management

The SSE generator holds a Redis subscription for the duration of the collaboration (typically 30-120 seconds). The `collaboration_events.py` Redis client must use connection pooling (`redis.ConnectionPool`) and implement reconnection logic in the generator — if the subscription drops, attempt reconnect up to 3 times before closing the SSE stream with an error event. This is especially important in the Rancher Desktop K8s environment where Redis pod restarts can occur under memory pressure.

---

## 15. Success Criteria

1. User sends an incident message in chat → 4 agents collaborate via CoalitionWorkflow
2. Chat shows labeled agent messages in real-time as each phase completes
3. Collaboration panel shows phase timeline progressing, blackboard entries appearing live
4. Final synthesis appears as the assistant's response
5. Replay mode allows stepping through the completed collaboration
6. Each agent's contribution is scored by consensus reviewer (visible in RL experiences)
7. Final report triggers provider council review (visible in RL experiences)
8. Coalition template stats update after completion
9. Works with any CLI (Gemini, Claude Code, Codex) — platform selection via RL routing / tenant config
10. Manual trigger endpoint works as demo fallback
