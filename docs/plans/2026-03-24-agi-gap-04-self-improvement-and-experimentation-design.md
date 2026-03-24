# AGI Gap 04 — Self-Improvement, Experiment Loops & Policy Evolution

**Date**: 2026-03-24  
**Status**: Design  
**Depends on**: `2026-03-12-reinforcement-learning-framework-design.md`, `2026-03-23-learned-routing-modularity-design.md`, `2026-02-26-self-modifying-agents-design.md`

## 1. Why this exists

The platform already records experiences and quality scores, which is ahead of most agent systems. But the current loop is still mostly passive scoring. To move toward AGI-like capability, the system needs to improve through controlled experimentation, not just retrospective grading.

## 2. Current state

We already have:

- RL experiences and reward scoring
- routing modularity work
- local quality scoring
- provider review concepts
- self-modifying agent design work

Missing:

- hypothesis-driven experiments
- automatic policy proposals from experience clusters
- safe A/B evaluation of new strategies
- promotion/demotion rules for agent behaviors
- explicit separation between learning, staging, and production policies

## 3. Goal

Create a **Learning Control Plane** that converts operational data into tested policy improvements.

## 4. Design

### 4.1 Learning pipeline

1. Collect experiences and outcomes  
2. Cluster recurring failure/success patterns  
3. Generate candidate policy changes  
4. Evaluate them in shadow mode or limited rollout  
5. Promote or reject based on measurable gains

### 4.2 Policy artifacts

Represent learnable behavior as explicit artifacts:

- routing policies
- prompting policies
- tool selection policies
- risk thresholds
- memory recall policies
- replanning heuristics

Each artifact needs:

- versioning
- offline metrics
- online rollout percentage
- rollback rules

### 4.3 Experiment framework

Add:

- `learning_experiments`
- `policy_candidates`
- `evaluation_runs`

Support:

- shadow evaluation
- traffic splitting
- regression alerts
- auto-rollback on quality drop

### 4.4 Self-modification boundary

The system must not edit arbitrary production prompts or code with no review path. Self-improvement should be constrained to:

- bounded config changes
- candidate policy generation
- sandboxed skill revisions
- explicitly approved code-change proposals

## 5. Implementation phases

### Phase 1: Candidate policy pipeline

- Generate candidate policies from RL experience analysis
- Store before/after rationale
- Add offline evaluation harness

### Phase 2: Controlled rollout

- Shadow mode for policy candidates
- Small-percentage production rollout
- Auto-rollback on score regression

### Phase 3: Agent learning dashboards

- Show which policies improved
- Show where learning is stalled
- Surface exploit/explore balance by tenant and decision point

## 6. Success criteria

- Policy changes are tested rather than guessed
- Strong strategies are promoted automatically within guardrails
- The system improves its routing and execution behavior measurably over time
- Learning becomes an operational pipeline, not a loose ambition

## 7. Why this matters for AGI

AGI is not just broad capability. It also implies the ability to adapt and get better. A controlled self-improvement loop is the minimum credible path from static competence to evolving competence.
