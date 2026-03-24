# AGI Gap 03 — Long-Horizon Planning, Execution & Recovery

**Date**: 2026-03-24  
**Status**: Design  
**Depends on**: `2026-03-21-dynamic-workflows-design.md`, `2026-03-15-cli-orchestration-pivot-design.md`, `2026-03-23-consensus-resilience-design.md`

## 1. Why this exists

ServiceTsunami can execute workflows and multi-step tasks, but most intelligence still lives inside short-lived request cycles. AGI-like systems need to sustain work over longer horizons, recover from interruption, and adapt plans when reality changes.

## 2. Current state

The platform already has:

- Temporal workflows
- code execution workers
- inbox/competitor monitors
- execution traces
- dynamic workflows

Missing:

- explicit hierarchical plans as first-class state
- plan repair when a step fails
- bounded autonomy budgets
- interruption/resume semantics above the workflow level
- "why did the plan change?" provenance

## 3. Goal

Build a **Plan Runtime** that lets agents create, monitor, revise, and resume long-running plans safely.

## 4. Design

### 4.1 Plan object

Introduce a plan model with:

- `goal_id`
- `plan_version`
- `current_step_id`
- `status`
- `replan_count`
- `budget_json` (time, cost, risk)

**Steps, assumptions, and metrics as first-class records** (not JSON blobs):

Steps, assumptions, and success metrics should be separate tables (`plan_steps`, `plan_assumptions`, `plan_metrics`), not opaque JSON on the parent plan row. This is critical because:

- Individual steps need their own execution/replan history and provenance
- Plan diffs between versions require per-step comparison
- "Resume from last confirmed step" requires addressable step records
- Assumptions need independent status tracking (valid, invalidated, unverified)

The existing `dynamic_workflows` system already suffers from JSON-blob step definitions making inspection and finalization harder — this design should not repeat that pattern.

**Relationship to PR #25's Architect agent**: The code-worker already has a planning phase (Architect agent creates `.claude/plan.md`, plan review council validates, implementation follows). The Plan Runtime should formalize and extend that pattern into durable DB-backed plans, not ignore or duplicate it.

### 4.2 Execution semantics

Each plan step should declare:

- expected inputs
- expected outputs
- owner agent
- required tools
- side-effect level
- retry policy
- fallback path

### 4.3 Replanning engine

When execution diverges, the runtime should classify the failure:

- transient execution failure
- missing information
- invalid assumption
- blocked by approval
- world-state change

Then it decides:

- retry
- gather more information
- branch to fallback
- escalate for approval
- rewrite the remaining plan

### 4.4 Human-visible autonomy budgets

Every long-horizon plan should operate under explicit budgets:

- maximum external actions
- maximum spend
- maximum concurrent branches
- maximum unattended runtime

This prevents "agentic drift" while still allowing durable execution.

## 5. Implementation phases

### Phase 1: Plan state and APIs

- Add `plans` and `plan_events`
- Store step state transitions with provenance
- Expose plan inspection in API/UI

### Phase 2: Replanning and resume

- Add failure classifier
- Add plan repair policies
- Support resume from last confirmed step

### Phase 3: Budget-aware execution

- Enforce risk/time/cost budgets
- Require human approval when budgets would be exceeded

## 6. Success criteria

- Agents can work across hours or days without losing context
- Failed plans do not silently die or restart from scratch
- Users can inspect why the system replanned
- Long-running execution becomes reliable enough for real operations

## 7. Why this matters for AGI

Long-horizon reasoning is one of the main missing properties in current systems. Durable planning with recovery is the bridge from "clever replies" to "ongoing competent action".
