# AGI Gap 02 — Self-Model, Goal Memory & Identity Persistence

**Date**: 2026-03-24  
**Status**: Design  
**Depends on**: `2026-03-06-memory-system-design.md`, `2026-03-12-agent-fleet-redesign-design.md`, `2026-03-21-dynamic-workflows-design.md`

## 1. Why this exists

The platform can remember user facts and execute tasks, but it does not yet maintain a durable model of:

- what each agent is trying to achieve
- what constraints it is operating under
- what strategies it currently prefers
- what commitments it has already made

Without that, agents feel smart in bursts but not continuously self-consistent.

## 2. Current state

Today we have:

- personas and agent routing
- chat history
- knowledge graph memory
- execution traces
- workflows for durable task execution

Missing pieces:

- persistent goals across sessions
- explicit commitments and open loops
- agent self-description that can evolve safely
- per-tenant operating principles and success criteria
- memory of failed strategies vs. preferred strategies

## 3. Goal

Create a **Self-Model Layer** for both tenant-facing agents and internal supervisors so they can carry forward:

- goals
- commitments
- constraints
- preferences
- strategy priors
- ownership boundaries

## 4. Design

### 4.1 Core objects

Add durable objects for:

- `goal_records`
- `commitment_records`
- `strategy_profiles`
- `agent_identity_profiles`

### 4.2 Goal record schema

Each goal should include:

- `tenant_id`
- `owner_agent_id`
- `title`
- `objective_type`
- `priority`
- `state` (`proposed`, `active`, `blocked`, `completed`, `abandoned`)
- `success_criteria_json`
- `deadline`
- `related_entity_ids`
- `parent_goal_id`
- `progress_summary`
- `last_reviewed_at`

### 4.3 Commitment tracking

When an agent makes an explicit commitment to the user, the system should create a commitment object. Commitments then feed reminders, reviews, and overdue detection.

**Speech-act classification boundary**: Not every mention of future action is a real commitment. The system will encounter hypothetical examples, quoted text, drafted messages, and tool-generated language that was never intended as an actual system obligation. To avoid filling the commitment layer with false reminders:

- **Phase 1**: Only create commitments from explicit tool calls (e.g., `schedule_followup`, `create_calendar_event`) — these are unambiguous system actions.
- **Phase 2**: Add LLM-based speech-act classification to detect genuine commitments in natural language. Must distinguish:
  - Direct commitments: "I'll send that report by Friday" → create commitment
  - Hypothetical: "If you want, I could follow up" → do NOT create commitment
  - Quoted/drafted: "Here's a draft: I'll follow up on..." → do NOT create commitment
- **Phase 3**: User-confirmable commitments — agent proposes, user confirms before it becomes a tracked obligation.

Without this filter, the commitment layer will degrade trust faster than it builds it.

### 4.4 Identity profile

An agent identity profile stores:

- role and mandate
- allowed tool classes
- escalation thresholds
- preferred planning style
- communication style
- learned strengths and weaknesses

This is not freeform personality drift. It is an explicit, auditable operating profile.

## 5. Runtime behavior

At the start of each substantial interaction, the orchestration layer should load:

- active goals
- outstanding commitments
- current constraints
- recent failures and preferred strategies

The agent then responds relative to its ongoing mission, not only the last message.

## 6. Implementation phases

### Phase 1: Goal and commitment storage

- Add goal and commitment models
- Create service layer and API endpoints
- Auto-create commitments from explicit assistant promises

### Phase 2: Identity profile wiring

- Add agent identity profile config
- Expose mandate, risk posture, and domain boundaries to runtime

### Phase 3: Review loop

- Add periodic goal review workflow
- Detect stalled, blocked, or contradictory goals
- Prompt agents to re-plan instead of forgetting

## 7. Success criteria

- Agents keep track of open loops across days and weeks
- Users do not need to repeatedly restate ongoing priorities
- Internal supervisors can distinguish mission drift from legitimate plan changes
- Commitments become auditable system objects, not chat-only text

## 8. Why this matters for AGI

General intelligence requires continuity of purpose. A system that cannot persist goals, constraints, and identity over time will always behave like a reactive assistant rather than a durable agent.
