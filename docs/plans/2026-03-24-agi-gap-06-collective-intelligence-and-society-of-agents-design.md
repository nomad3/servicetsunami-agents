# AGI Gap 06 — Collective Intelligence & Society-of-Agents Runtime

**Date**: 2026-03-24  
**Status**: Design  
**Depends on**: `2026-03-19-distributed-agent-protocol-design.md`, `2026-03-23-consensus-resilience-design.md`, `2026-03-23-multi-provider-review-council-design.md`

## 1. Why this exists

The platform is already moving toward a distributed agent network, but the current model is still mostly "one task, one agent, one response path". A more general system likely emerges from specialized components that coordinate, critique, and share state rather than from a single monolithic super-agent.

## 2. Current state

We already have:

- hierarchical teams
- multi-provider execution paths
- consensus/review work
- distributed protocol design
- routing and orchestration primitives

Missing:

- stable roles for internal specialist agents
- shared blackboard/state substrate
- explicit debate/synthesis protocols
- dynamic coalition formation for novel tasks
- agent reputation and specialization markets

## 3. Goal

Build a **Society-of-Agents Runtime** where multiple agents collaborate through shared state and formal coordination patterns rather than ad hoc prompt chaining.

## 4. Design

### 4.1 Shared blackboard

Create a shared working memory for a task containing:

- current goal
- subproblems
- proposed hypotheses
- evidence snippets
- open disagreements
- final synthesized answer or plan

**Concurrency and authority model**: Since the execution model is distributed and Temporal-driven, concurrent writes from planner/researcher/verifier are the normal case, not an edge case. The blackboard MUST implement:

- **Append-only semantics**: Agents add entries, never overwrite. Each entry tagged with author agent, timestamp, and confidence.
- **Versioning**: Every blackboard state change creates a version. Diffs between versions are inspectable.
- **Ownership**: Each subproblem/hypothesis has an owner agent. Only the owner or a higher-authority role (synthesizer, auditor) can mark it resolved.
- **Conflict resolution**: When agents disagree on a fact, both positions are stored as competing entries. Resolution requires either consensus voting or synthesizer adjudication — not silent overwrite.
- **Replayability**: The full append log enables replaying the collaboration for debugging and RL training.

### 4.2 Stable coordination roles

Define reusable internal roles such as:

- planner
- researcher
- executor
- critic
- verifier
- synthesizer
- auditor

These roles can map to different providers or skills depending on cost and capability.

### 4.3 Coalition formation

For complex tasks, the router should choose not just one agent but a team shape:

- single-agent fast path
- planner + executor
- planner + researcher + verifier
- code agent + reviewer + risk gate

### 4.4 Reputation system

Track reputation for both agents and team shapes:

- task-type win rate
- cost/quality ratio
- failure modes
- disagreement usefulness

This lets the system learn which coalitions outperform others.

## 5. Implementation phases

### Phase 1: Shared task blackboard

- Add task-scoped collaboration state
- Let multiple agents write proposals and evidence into it

### Phase 2: Formal collaboration patterns

- Add planner/critic/verifier workflows
- Store disagreements and resolution reasons

### Phase 3: Learned coalition routing

- Route tasks to team shapes based on historical outcomes
- Combine with distributed node selection from STP

## 6. Success criteria

- Complex tasks no longer rely on a single opaque reasoning pass
- Multi-agent collaboration is inspectable and replayable
- The system learns which team structures work best for which problems
- Specialized agents become composable cognitive building blocks

## 7. Why this matters for AGI

One plausible path to general intelligence is not a single giant mind but a coordinated society of specialized minds with shared state, verification, and adaptation. The platform already has the beginnings of that. This document turns it into an explicit architecture.
