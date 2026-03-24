# AGI Gap 05 — Safety, Governance & Trust Boundaries

**Date**: 2026-03-24  
**Status**: Design  
**Depends on**: `2026-03-23-consensus-resilience-design.md`, `2026-03-23-multi-provider-review-council-design.md`, `2026-03-21-dynamic-workflows-design.md`

## 1. Why this exists

More autonomy without stronger governance is a liability. As the platform becomes better at planning, self-improving, and coordinating across nodes, the main constraint shifts from capability to trustworthiness.

## 2. Current state

The platform already has:

- tool access boundaries
- async review patterns
- workflow durability
- tenant isolation
- execution traces

Missing:

- unified action-risk model
- formal approval policies across channels and tools
- evidence requirements for high-impact decisions
- sandbox tiers for learning vs. production autonomy
- trust scores that affect allowed autonomy

## 3. Goal

Create a **Trust Architecture** where every meaningful action is governed by:

- risk classification
- evidence sufficiency
- policy enforcement
- reviewability
- rollbackability

## 4. Design

### 4.1 Action policy engine

Every action should be evaluated against:

- side-effect level
- reversibility
- tenant policy
- agent trust level
- evidence confidence
- human approval requirements

Output:

- `allow`
- `allow_with_logging`
- `require_confirmation`
- `require_review`
- `block`

### 4.2 Evidence packs

Before high-risk actions, require a structured evidence pack:

- relevant world-state facts
- recent observations
- assumptions
- uncertainty notes
- proposed action
- expected downside

This prevents autonomous action from being based on thin context.

### 4.3 Trust scores

Add trust scoring at three levels:

- agent profile
- policy artifact
- tool/action class

Trust increases only when actions remain accurate and reversible under real usage.

### 4.4 Sandboxes

Define four execution tiers:

1. observe-only
2. recommend-only
3. supervised execution
4. bounded autonomous execution

Promotion between tiers requires evidence, not optimism.

## 5. Implementation phases

### Phase 1: Unified risk taxonomy

- Create risk classes for all MCP tools and workflow actions
- Add approval mapping by tenant and channel

### Phase 2: Policy enforcement layer

- Centralize allow/confirm/block checks
- Attach evidence packs to sensitive actions

### Phase 3: Trust-aware autonomy

- Add trust score inputs to routing and execution
- Restrict low-trust agents to lower autonomy tiers

## 6. Success criteria

- High-risk actions are governed consistently across the stack
- Users can inspect why an action was allowed or blocked
- Autonomy expands only when trust metrics justify it
- Learning systems cannot silently bypass operational policy

## 7. Why this matters for AGI

A system that becomes more powerful without becoming more governable is not progress. Trust architecture is what makes greater autonomy usable in the real world.
