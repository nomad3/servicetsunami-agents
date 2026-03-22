# Consensus Resilience & Multi-Agent Hardening — Design Document

**Date**: 2026-03-23
**Status**: Design
**Context**: Simon shared ChatGPT recommendations on multi-agent consensus systems. This document critically evaluates each recommendation and proposes what to implement.

## Current State

What we have today:
- **Consensus Review Council**: 3 local Qwen reviewers (Accuracy, Helpfulness, Persona) running in parallel via `asyncio.gather`, 2/3 majority to pass
- **Auto Quality Scorer**: 6-dimension rubric (100pt scale) + consensus penalty (up to 15pt)
- **RL Experience Logging**: Every response scored, reward components stored with consensus metadata
- **GPU Semaphore**: Serialized Ollama calls to prevent contention
- **Fallback chain**: subscribed CLI → local tool agent → plain text → error

## Critical Review of Recommendations

### 1. Stability Tracking ("Track instability like a dynamical system")

**Recommendation**: Track consensus time, position reversals, confidence variance, sensitivity to agent removal, KL divergence.

**Assessment**: Partially useful, mostly over-engineered for our scale.

**What's worth implementing**:
- **Sensitivity to agent removal** (leave-one-out): Cheap to compute. If removing any single reviewer flips the consensus, that's a weak consensus. Store as `fragile: bool` in ConsensusResult.
- **Confidence variance**: If all 3 reviewers return widely different verdicts (one APPROVED, one REJECTED, one CONDITIONAL), flag as unstable.

**What's NOT worth implementing**:
- KL divergence between initial/final belief states — we don't have iterative rounds, it's a single-shot vote
- Position reversals — same reason, no deliberation rounds
- Consensus time — all reviewers are identical Qwen calls, timing variance is just GPU scheduling noise

### 2. Chaos Engineering ("Inject controlled failures")

**Recommendation**: Kill agents mid-deliberation, delay by 10-30s, return stale results, inject malformed tool responses, swap models, corrupt memory.

**Assessment**: Good for production hardening, but we already handle most of this.

**What we already handle**:
- Agent failure → `return_exceptions=True` + fail-open (reviewer marked SKIPPED)
- Malformed response → JSON parse fallback with lenient extraction
- Model unavailable → fallback chain (qwen3 → qwen2.5-coder → skip)

**What's worth implementing**:
- **Configurable chaos mode** for testing: env var `CHAOS_MODE=true` that randomly delays/fails one reviewer per consensus round. Run in staging only.
- NOT worth building a full harness — the `return_exceptions` + fail-open pattern already provides the resilience these tests would verify.

### 3. Risk-Based Quorum Rules

**Recommendation**: Low-risk (2/3), Medium-risk (2/3 + verifier + grounding), High-risk (2 model families + tool validation + safety gate + human approval).

**Assessment**: The strongest recommendation. Currently we apply the same 2/3 rule to "what's the weather" and "deploy this to production."

**What's worth implementing**:
- **Risk classification**: Classify each message as low/medium/high risk based on:
  - `low`: general chat, knowledge search, information retrieval
  - `medium`: entity creation, email drafts, data modifications
  - `high`: code tasks, email sending, Jira issue creation, deployment actions
- **Tiered quorum**:
  - `low`: 1/3 (or skip consensus entirely — just rubric score)
  - `medium`: 2/3 (current behavior)
  - `high`: 3/3 unanimous + tool validation
- **Skip consensus for trivial messages**: "hello", "thanks", simple greetings don't need 3 reviewer calls

### 4. Fault-Tolerant Distributed Graph

**Recommendation**: Model agents as a graph with typed roles (planner, retriever, synthesizer, verifier, critic, policy gate, executor, auditor).

**Assessment**: Architecturally sound but we already have this via Temporal + the agent team hierarchy. Not actionable as a code change — it's a design philosophy we already follow.

### 5. Weighted Adjudication with Diversity Penalties

**Recommendation**: Don't just majority vote — weight by model family diversity, grounding quality, and citation density.

**Assessment**: Over-engineered for our setup. All 3 reviewers use the same model (qwen3:1.7b). Weighting would only matter with heterogeneous model families. File this for when we add a cloud reviewer alongside local ones.

### 6. Six-Stage Architecture

**Recommendation**: Isolated first pass → Cross-examination → Adversarial review → Weighted adjudication → Meta-consensus → Chaos harness.

**Assessment**: We implement stages 1 (agent response), 3 (consensus review), and 4 (rubric scoring + RL). Stages 2 (cross-examination between agents) and 5 (meta-consensus across sessions) are too expensive for local inference. Stage 6 (chaos) covered above.

## Implementation Plan

### Phase 1: Risk-Based Quorum (High Impact, Low Effort)

**File**: `apps/api/app/services/consensus_reviewer.py`

1. Add risk classifier function that maps task_type + tool usage to risk level
2. Skip consensus for low-risk messages (saves 3 Ollama calls per trivial message)
3. Require 3/3 unanimous for high-risk actions
4. Add `risk_level` to ConsensusResult and RL metadata

```python
def _classify_risk(tools_called: list, agent_slug: str, channel: str) -> str:
    HIGH_RISK_TOOLS = {"send_email", "create_jira_issue", "deploy_changes", "execute_shell"}
    if any(t in HIGH_RISK_TOOLS for t in tools_called):
        return "high"
    if tools_called:  # any tool use = medium
        return "medium"
    return "low"
```

### Phase 2: Leave-One-Out Fragility Check (Low Effort)

**File**: `apps/api/app/services/consensus_reviewer.py`

After computing consensus, check if removing any single reviewer would flip the result. Store as `fragile: bool`.

```python
def _is_fragile(reviews: list, required: int = 2) -> bool:
    approved = [r for r in reviews if r.get("approved")]
    # If exactly at the threshold, removing one approved reviewer flips it
    return len(approved) == required
```

### Phase 3: Chaos Testing Mode (Medium Effort, Staging Only)

**File**: `apps/api/app/services/consensus_reviewer.py`

When `CHAOS_MODE=true`:
- Randomly delay one reviewer by 5-15s
- Randomly return a malformed response from one reviewer
- Log chaos injection details for analysis

Not for production. Testing harness only.

## What NOT to Implement

- KL divergence / position reversals — no iterative rounds to measure
- Multiple model families — all reviewers use same Qwen, no diversity to weight
- Cross-examination between reviewers — too expensive on local GPU
- Meta-consensus across sessions — premature, need more RL data first
- Full chaos engineering harness — `return_exceptions` + fail-open already provides resilience

## Cost Analysis

| Feature | Ollama Calls | GPU Time | Value |
|---------|-------------|----------|-------|
| Current (flat 2/3) | 4/msg (1 rubric + 3 consensus) | ~20s | Baseline |
| Phase 1 (risk quorum) | 1-4/msg (skip consensus for low-risk) | 5-20s | 50-70% reduction for chat |
| Phase 2 (fragility) | 0 extra | 0 | Pure compute on existing results |
| Phase 3 (chaos) | 0 extra in prod | 0 in prod | Testing only |

**Phase 1 alone would cut Ollama GPU usage by 50-70%** since most messages are low-risk chat.
