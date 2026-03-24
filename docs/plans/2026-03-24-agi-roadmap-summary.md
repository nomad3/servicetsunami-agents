# AGI Roadmap — Gap Summary & Implementation Progress

**Date**: 2026-03-24
**Status**: In Progress

## The Six Gaps

```
Priority  Gap                   Status
────────  ────────────────────  ──────────────────
  1       Gap 05: Safety        COMPLETE (3 phases + bypass fixes)
  2       Gap 02: Self-Model    COMPLETE (3 phases)
  3       Gap 01: World Model   Phase 1 done, Phase 2-4 remaining
  4       Gap 03: Planning      Not started
  5       Gap 06: Society       Not started
  6       Gap 04: Self-Improve  Not started
```

## Architecture Overview

```
┌─────────────────────────────────────────────────────────────────┐
│                        USER / CHANNEL                           │
│                  (web, whatsapp, workflow)                       │
└──────────────────────────┬──────────────────────────────────────┘
                           │
                           ▼
┌──────────────────────────────────────────────────────────────────┐
│                      AGENT ROUTER                                │
│  ┌──────────────┐  ┌──────────────┐  ┌────────────────────────┐ │
│  │ Trust Profile │  │ RL Routing   │  │ Exploration Mode       │ │
│  │ (Gap 05 Ph3) │  │ (learned)    │  │ 70% Codex / 30% Claude │ │
│  └──────┬───────┘  └──────┬───────┘  └──────────┬─────────────┘ │
│         └─────────────────┴─────────────────────┘               │
└──────────────────────────┬──────────────────────────────────────┘
                           │
                           ▼
┌──────────────────────────────────────────────────────────────────┐
│                   CLI SESSION MANAGER                             │
│                                                                  │
│  Injects into every session:                                     │
│  ┌─────────────────┐ ┌───────────────┐ ┌──────────────────────┐ │
│  │ Identity Profile │ │ Active Goals  │ │ Open Commitments     │ │
│  │ (Gap 02 Ph2)    │ │ (Gap 02 Ph1)  │ │ (Gap 02 Ph1)         │ │
│  └─────────────────┘ └───────────────┘ └──────────────────────┘ │
│  ┌─────────────────┐ ┌───────────────┐ ┌──────────────────────┐ │
│  │ Memory Context  │ │ Git Context   │ │ World State (Gap 01) │ │
│  └─────────────────┘ └───────────────┘ └──────────────────────┘ │
└──────────────────────────┬──────────────────────────────────────┘
                           │
                           ▼
┌──────────────────────────────────────────────────────────────────┐
│                    SAFETY ENFORCEMENT                             │
│                       (Gap 05)                                   │
│                                                                  │
│  ┌──────────────┐  ┌──────────────┐  ┌────────────────────────┐ │
│  │ Risk Catalog  │  │ Evidence     │  │ Autonomy Tiers         │ │
│  │ 111 actions   │  │ Packs (30d)  │  │ observe → bounded_auto │ │
│  │ 5 risk classes│  │ TTL + dedup  │  │ per-agent trust scores │ │
│  └──────────────┘  └──────────────┘  └────────────────────────┘ │
│                                                                  │
│  Enforcement points:                                             │
│  • local_tool_agent.py    (local model tool gate)                │
│  • dynamic_step.py        (workflow MCP + agent steps)           │
│  • mcp_server_connectors  (external MCP proxy)                   │
└──────────────────────────┬──────────────────────────────────────┘
                           │
                           ▼
┌──────────────────────────────────────────────────────────────────┐
│                     CLI EXECUTION                                │
│  ┌────────────┐  ┌────────────┐  ┌────────────┐                │
│  │ Claude Code │  │   Codex    │  │ Gemini CLI │                │
│  └─────┬──────┘  └─────┬──────┘  └────────────┘                │
│        │               │                                         │
│        └───────┬───────┘  Full rotation fallback:                │
│                │          Claude → Codex → (Copilot planned)     │
│                │          Codex → Claude → (Copilot planned)     │
│                ▼                                                 │
│         81 MCP Tools                                             │
└──────────────────────────┬──────────────────────────────────────┘
                           │
                           ▼
┌──────────────────────────────────────────────────────────────────┐
│                    AUTO QUALITY + RL                              │
│  ┌──────────────┐  ┌──────────────┐  ┌────────────────────────┐ │
│  │ Qwen Scorer  │  │ Provider     │  │ RL Experience          │ │
│  │ 6-dim rubric │  │ Council (4)  │  │ → Trust Scores         │ │
│  │ 100pts total │  │ 20% sample   │  │ → Routing Optimization │ │
│  └──────────────┘  └──────────────┘  └────────────────────────┘ │
└─────────────────────────────────────────────────────────────────┘
```

## Gap 05: Safety & Trust — COMPLETE

```
Phase 1: Risk Taxonomy & Policy Engine (PR #28)
  ├── 111 governed actions (MCP tools + workflow actions)
  ├── 5 risk classes: read_only → orchestration_control
  ├── 4 risk levels: low → critical
  ├── Channel-aware defaults (local_agent, workflow, web, whatsapp)
  ├── Tenant-scoped policy overrides with ceiling enforcement
  └── Tables: tenant_action_policies

Phase 2: Policy Enforcement (PR #29)
  ├── Central enforcement service (safety_enforcement.py)
  ├── Evidence packs with 30-day TTL
  ├── Automated channel escalation (require_confirmation → require_review)
  ├── Runtime hooks: local_tool_agent, dynamic_step, mcp_server_connectors
  └── Tables: safety_evidence_packs

Phase 3: Trust-Aware Autonomy (PR #30)
  ├── Agent trust profiles computed from RL + provider council
  ├── 4 autonomy tiers: observe_only → bounded_autonomous_execution
  ├── Trust = 0.7*reward_signal + 0.3*provider_signal (confidence-weighted)
  ├── Auto-refresh stale profiles (6h default)
  ├── Routing surfaces trust metadata in RL experiences
  └── Tables: agent_trust_profiles

Bypass Fixes (PR #31)
  ├── Tenant override ceiling (can't relax HIGH/CRITICAL below default)
  ├── Workflow agent step enforcement gate
  └── External MCP connector enforcement gate
```

### Trust Score Formula

```
reward_signal = normalize(avg_reward from rl_experiences)
provider_signal = avg(agreement from provider_council reviews)
confidence = clamp((rated_count/25)*0.7 + (council_count/10)*0.3)

trust_score = (reward*0.7 + provider*0.3) * confidence
            + 0.5 * (1 - confidence)    # decay toward neutral

Tier thresholds:
  confidence < 0.2 OR trust < 0.35  →  observe_only
  trust < 0.55                       →  recommend_only
  trust < 0.80                       →  supervised_execution
  trust >= 0.80                      →  bounded_autonomous_execution
```

## Gap 02: Self-Model & Goals — COMPLETE

```
Phase 1: Goal & Commitment Storage (PR #32)
  ├── goal_records: proposed → active → blocked → completed/abandoned
  │   ├── Hierarchical (parent_goal_id)
  │   ├── Success criteria, deadlines, progress tracking
  │   └── Cross-tenant parent validation enforced
  ├── commitment_records: open → in_progress → fulfilled/broken/cancelled
  │   ├── Source tracking (tool_call, workflow_step, manual)
  │   ├── Due dates, goal linkage
  │   └── Cross-tenant goal_id validation enforced
  ├── Runtime helpers: list_active_goals, list_open_commitments, list_overdue
  └── Tables: goal_records, commitment_records

Phase 2: Identity Profile Wiring (PR #33)
  ├── agent_identity_profiles: per-agent operating profile
  │   ├── Role, mandate, domain boundaries
  │   ├── Allowed/denied tool classes
  │   ├── Escalation threshold, planning style, communication style
  │   ├── Risk posture, strengths, weaknesses
  │   └── Preferred/avoided strategies, operating principles
  ├── Runtime injection: identity + goals + commitments in every CLI session
  ├── Dynamic agent identity (not hardcoded Luna)
  └── Tables: agent_identity_profiles

Phase 3: Goal Review Workflow (PR #34)
  ├── GoalReviewWorkflow: 6h cycle per tenant via continue_as_new
  ├── Detects: stalled (7d), no-progress (14d), long-blocked (3d), overdue
  ├── Notification dedup via reference_id (no spam)
  ├── Stalled goals keep old timestamp (don't self-erase detection)
  ├── API: POST /workflows/goal-review/start|stop, GET /status
  └── Registered on servicetsunami-orchestration queue
```

### Self-Model Runtime Injection

```
Every CLI session receives:

┌─────────────────────────────────────────────┐
│ ## Agent Identity: {agent_slug}             │
│ Role: AI chief of staff                     │
│ Mandate: Manage deals, schedule follow-ups  │
│ Risk posture: moderate                      │
│ Planning style: step_by_step                │
│ Strengths: CRM analysis, email drafting     │
│ Weaknesses: complex financial modeling      │
├─────────────────────────────────────────────┤
│ ## Active Goals                             │
│ - [active] Close Q1 pipeline (priority: hi) │
│ - [blocked] Migrate CRM data (priority: lo) │
├─────────────────────────────────────────────┤
│ ## Open Commitments                         │
│ - Send proposal to Acme (due: 2026-03-25)   │
│ - Schedule demo with Beta Corp              │
└─────────────────────────────────────────────┘
```

## Gap 01: World Model — Phase 1 COMPLETE

```
Phase 1: Assertion Layer (PR #35)
  ├── world_state_assertions: normalized claims from observations
  │   ├── subject_slug + attribute_path + value_json
  │   ├── Confidence, provenance (links to source observation)
  │   ├── Freshness TTL with automatic expiry
  │   ├── Corroboration (same value → confidence boost)
  │   ├── Supersession chain (different value → old marked superseded)
  │   └── Cross-tenant entity/observation validation
  ├── world_state_snapshots: auto-recomputed current state per entity
  │   ├── Projected state as flat key-value map
  │   ├── Confidence stats (min, avg)
  │   ├── Unstable attributes (low confidence or nearing expiry)
  │   └── Refreshed on all affected subjects during expiry
  ├── Fully atomic: single commit covers expiry + supersession + snapshots
  ├── API: /world-state/assertions, /world-state/snapshots
  └── Tables: world_state_assertions, world_state_snapshots

Remaining:
  Phase 2: Conflict and freshness handling (disputed claims, decay)
  Phase 3: Causal graph (action → outcome linking)
  Phase 4: State-first agent prompts (inject world state instead of raw memory)
```

### Assertion Lifecycle

```
Observation arrives
       │
       ▼
  ┌─────────────────┐
  │  assert_state()  │
  └────────┬────────┘
           │
           ├── Expire stale assertions (TTL check)
           │   └── Refresh affected snapshots
           │
           ▼
  ┌──────────────────────────┐
  │ Same subject + attribute │
  │ already active?          │
  └────────┬─────────────────┘
           │
     ┌─────┴─────┐
     │           │
  same value  different value
     │           │
     ▼           ▼
  corroborate  supersede old
  (+0.05 conf)  (status=superseded)
     │           │
     │           ▼
     │        create new assertion
     │           │
     └─────┬─────┘
           │
           ▼
  ┌──────────────────────┐
  │ _update_snapshot()   │
  │ Recompute projected  │
  │ state from active    │
  │ assertions only      │
  └──────────────────────┘
           │
           ▼
       db.commit()
   (single atomic commit)
```

## Why This Order

**Gap 05 (Safety) first**: More autonomy without governance is a liability. The pre-execution safety gate we already built is a partial implementation. Completing the risk taxonomy gives every subsequent gap a trust foundation.

**Gap 02 (Goals) second**: Highest immediate ROI — goal and commitment persistence leverages existing memory infrastructure and directly improves user experience. Agents that remember what they committed to are more useful than agents that plan better.

**Gap 01 (World Model) third**: Transforms raw observations (4,823 existing) into structured state. Enables Gaps 3-6 to operate on reliable state instead of raw memory dumps.

**Gap 04 (Self-Improvement) last**: Requires the most accumulated data and infrastructure. The exploration mode (currently routing 70% to Codex) is already collecting the training data this gap needs. Let it run while building Gaps 1-3.

## PRs Merged

```
PR #28  Gap 05 Phase 1 — Risk taxonomy & policy engine
PR #29  Gap 05 Phase 2 — Policy enforcement & evidence packs
PR #30  Gap 05 Phase 3 — Trust-aware autonomy
PR #31  Gap 05 — Enforcement bypass fixes
PR #32  Gap 02 Phase 1 — Goal & commitment records
PR #33  Gap 02 Phase 2 — Agent identity profiles + runtime injection
PR #34  Gap 02 Phase 3 — Periodic goal review workflow
PR #35  Gap 01 Phase 1 — World state assertions & snapshots
```

## Database Tables Added

```
Gap 05:
  tenant_action_policies        — tenant policy overrides
  safety_evidence_packs         — enforcement audit trail (30d TTL)
  agent_trust_profiles          — per-agent trust scores + autonomy tiers

Gap 02:
  goal_records                  — durable goals with state machine
  commitment_records            — agent promises with due dates
  agent_identity_profiles       — auditable operating profiles

Gap 01:
  world_state_assertions        — normalized claims with provenance
  world_state_snapshots         — auto-projected current state per entity
```
