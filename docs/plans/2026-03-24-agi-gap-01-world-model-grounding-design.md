# AGI Gap 01 — World Model & Grounded State Layer

**Date**: 2026-03-24  
**Status**: Design  
**Depends on**: `2026-03-06-memory-system-design.md`, `2026-03-19-distributed-agent-protocol-design.md`, `2026-03-23-learned-routing-modularity-design.md`

## 1. Why this exists

ServiceTsunami already has persistent memory, semantic recall, tool access, and a growing distributed execution model. What it does **not** have is a durable, queryable model of the world that agents can treat as the current state of reality rather than a bag of loosely related memories.

That gap is one of the main reasons the platform is still "strong narrow intelligence" instead of anything AGI-adjacent. The system can retrieve facts, but it cannot reliably maintain:

- a stable view of the environment
- causal relationships between changes
- uncertainty and contradiction
- time-aware state transitions

## 2. Current state

Today the platform has:

- `knowledge_entities`, `knowledge_relations`, `knowledge_observations`, and embeddings
- recall and extraction flows
- inbox and competitor monitors that generate observations
- RL traces that record decisions after the fact

What is missing:

- a canonical "current world state" per tenant
- freshness/confidence decay on state assertions
- conflict resolution between competing facts
- event-to-state projection pipelines
- causal linking across observations, workflows, and outcomes

## 3. Goal

Add a **World State Layer** that turns raw observations into a structured, time-aware operating picture. Agents should be able to ask:

- what is true right now?
- how confident are we?
- what changed recently?
- what probably caused that change?
- what assumptions are unstable and need verification?

## 4. Design

### 4.1 New conceptual layers

1. **Observations**  
Raw facts from chat, email, workflows, monitors, and external tools.

2. **Assertions**  
Normalized claims derived from observations, e.g. `company.stage = proposal`, `campaign.status = paused`.

3. **World State Projections**  
The current best-known state per entity or system object, with provenance and confidence.

4. **Causal Edges**  
Links from events to outcomes, e.g. `email_followup_sent -> meeting_booked`.

### 4.2 Proposed data model

Add:

- `world_state_snapshots`
- `world_state_assertions`
- `world_state_projection_runs`
- `causal_edges`

Each assertion should include:

- `tenant_id`
- `subject_entity_id`
- `attribute_path`
- `value_json`
- `valid_from`
- `valid_to`
- `confidence`
- `source_observation_id`
- `source_type`
- `freshness_ttl_hours`
- `status` (`active`, `superseded`, `disputed`, `expired`)

### 4.3 Projection engine

Build a projection service that:

- consumes new observations
- normalizes them into assertions
- resolves conflicts using recency, source trust, and corroboration
- updates the latest state projection for each entity
- emits "state changed" activities for downstream workflows

### 4.4 Agent integration

Before a high-value response or workflow step, agents receive:

- top relevant entities
- current projected state
- recent changes
- unstable assumptions requiring confirmation

This replaces "memory as context stuffing" with "memory as state estimation".

## 5. Implementation phases

### Phase 1: Assertion model

- Add assertion and snapshot tables
- Project observations into assertions for a small set of domains:
  - leads
  - tasks
  - meetings
  - competitor entities

### Phase 2: Conflict and freshness handling

- Add assertion decay and expiry
- Mark contradictory claims as disputed
- Surface confidence and freshness in recall payloads

### Phase 3: Causal graph

- Link actions to downstream results
- Store causal hypotheses with confidence
- Feed successful causal patterns into routing and planning

### Phase 4: State-first agent prompts

- Inject world state summaries instead of raw memory dumps
- Add "verify unstable assumption" behavior to agent orchestration

## 6. Success criteria

- Agents can answer "what changed?" from projected state, not just semantic search
- Contradictory facts are visible instead of silently coexisting
- Time-sensitive decisions carry freshness/confidence metadata
- Routing and planning improve because they use structured state, not only embeddings

## 7. Why this matters for AGI

AGI-like behavior needs more than retrieval. It needs a persistent model of reality that updates over time and distinguishes memory, belief, and uncertainty. This document is the first step toward that.
