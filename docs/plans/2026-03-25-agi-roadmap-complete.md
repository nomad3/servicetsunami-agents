# AGI Roadmap — Complete Implementation Report

**Date**: 2026-03-25 (updated 2026-03-26)
**Status**: Complete + Unsupervised Learning Active
**PRs**: #28 through #60 (33 PRs total)
**Tables added**: 22 database tables
**Lines of code**: ~12,000+ across models, schemas, services, APIs, migrations, workflows, and simulation engine

---

## Executive Summary

In a single session, the platform evolved from a reactive CLI orchestrator into
a durable agent system with safety governance, self-model persistence, world
state grounding, long-horizon planning, multi-agent collaboration, and
self-improvement capabilities. Every feature was cross-reviewed by Codex before
merge, with all P1 findings fixed.

```
BEFORE (reactive assistant)          AFTER (durable agent system)
─────────────────────────            ──────────────────────────────
Message → CLI → Response             Message → Safety Gate → Trust Check
                                       → Identity + Goals + World State
                                       → CLI → Response → Quality Score
                                       → RL Experience → Trust Update
                                       → Policy Candidate → Experiment
                                       → Promotion or Rejection
```

---

## Architecture Overview

```
┌─────────────────────────────────────────────────────────────────────┐
│                          USER / CHANNEL                             │
│                    (web, whatsapp, workflow)                         │
└───────────────────────────┬─────────────────────────────────────────┘
                            │
                            ▼
┌─────────────────────────────────────────────────────────────────────┐
│                        AGENT ROUTER                                 │
│                                                                     │
│  ┌──────────────┐  ┌──────────────┐  ┌───────────────────────────┐ │
│  │ Trust Profile │  │ RL Routing   │  │ Policy Rollout            │ │
│  │ (Gap 05)     │  │ (learned)    │  │ (Gap 04 — split A/B)     │ │
│  └──────┬───────┘  └──────┬───────┘  └────────────┬──────────────┘ │
│         └─────────────────┴───────────────────────┘                │
└───────────────────────────┬─────────────────────────────────────────┘
                            │
                            ▼
┌─────────────────────────────────────────────────────────────────────┐
│                     CLI SESSION MANAGER                              │
│                                                                     │
│  Injects into every session:                                        │
│                                                                     │
│  ┌─────────────────┐ ┌───────────────┐ ┌─────────────────────────┐ │
│  │ Identity Profile │ │ Active Goals  │ │ Open Commitments        │ │
│  │ (Gap 02 Ph2)    │ │ (Gap 02 Ph1)  │ │ (Gap 02 Ph1)            │ │
│  └─────────────────┘ └───────────────┘ └─────────────────────────┘ │
│  ┌─────────────────┐ ┌───────────────┐ ┌─────────────────────────┐ │
│  │ World State     │ │ Unstable      │ │ Causal Patterns         │ │
│  │ Snapshots       │ │ Assertions    │ │ (Gap 01 Ph3-4)          │ │
│  │ (Gap 01 Ph4)   │ │ (Gap 01 Ph4)  │ │                         │ │
│  └─────────────────┘ └───────────────┘ └─────────────────────────┘ │
│  ┌─────────────────┐ ┌───────────────┐                             │
│  │ Memory Context  │ │ Git Context   │                             │
│  └─────────────────┘ └───────────────┘                             │
└───────────────────────────┬─────────────────────────────────────────┘
                            │
                            ▼
┌─────────────────────────────────────────────────────────────────────┐
│                      SAFETY ENFORCEMENT                             │
│                         (Gap 05)                                    │
│                                                                     │
│  ┌──────────────┐  ┌───────────────┐  ┌──────────────────────────┐ │
│  │ Risk Catalog  │  │ Evidence      │  │ Autonomy Tiers           │ │
│  │ 111 actions   │  │ Packs (30d)   │  │ observe → bounded_auto   │ │
│  │ 5 risk classes│  │ TTL + dedup   │  │ per-agent trust scores   │ │
│  └──────────────┘  └───────────────┘  └──────────────────────────┘ │
│                                                                     │
│  Enforcement points:                                                │
│  • local_tool_agent    (local model tool gate)                      │
│  • dynamic_step        (workflow MCP + agent steps)                 │
│  • mcp_server_connectors (external MCP proxy)                       │
└───────────────────────────┬─────────────────────────────────────────┘
                            │
                            ▼
┌─────────────────────────────────────────────────────────────────────┐
│                       CLI EXECUTION                                 │
│                                                                     │
│  ┌────────────┐  ┌────────────┐  ┌────────────┐                   │
│  │ Claude Code │  │   Codex    │  │ Gemini CLI │                   │
│  └─────┬──────┘  └─────┬──────┘  └────────────┘                   │
│        │               │                                            │
│        └───────┬───────┘  Full rotation fallback:                   │
│                │          Claude → Codex → (Copilot planned)        │
│                │          Codex → Claude → (Copilot planned)        │
│                ▼                                                    │
│         81 MCP Tools                                                │
└───────────────────────────┬─────────────────────────────────────────┘
                            │
                            ▼
┌─────────────────────────────────────────────────────────────────────┐
│                  AUTO QUALITY + RL + LEARNING                       │
│                                                                     │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────────────────┐ │
│  │ Gemma 4 Scorer  │  │ Provider     │  │ RL Experience             │ │
│  │ 6-dim rubric │  │ Council (20%)│  │ → Trust Scores            │ │
│  │ 100pts total │  │              │  │ → Routing Optimization    │ │
│  └──────┬───────┘  └──────────────┘  │ → Policy Candidates      │ │
│         │                            │ → Experiments             │ │
│         │                            │ → Rollout Observations    │ │
│         ▼                            └──────────────────────────┘ │
│  ┌────────────────────────────────────────────────────────────┐    │
│  │ Learning Dashboard (Gap 04 Ph3)                            │    │
│  │ • Policy improvements with measured impact                 │    │
│  │ • Stalled decision points                                  │    │
│  │ • Explore/exploit balance per platform                     │    │
│  │ • Active rollout status with arm statistics                │    │
│  └────────────────────────────────────────────────────────────┘    │
└─────────────────────────────────────────────────────────────────────┘
```

---

## The Six Gaps — Detailed

### Gap 05: Safety & Trust (Foundation)

**Why first**: More autonomy without governance is a liability.

```
Phase 1: Risk Taxonomy (PR #28)
  111 governed actions classified into:
  ┌─────────────────────┬──────────────────────────────┐
  │ Risk Class          │ Examples                     │
  ├─────────────────────┼──────────────────────────────┤
  │ read_only           │ search_knowledge, find_*     │
  │ internal_mutation   │ create_entity, update_*      │
  │ external_write      │ send_email, create_calendar  │
  │ execution_control   │ execute_shell, deploy_*      │
  │ orchestration_ctrl  │ start_workflow, agent_step   │
  └─────────────────────┴──────────────────────────────┘

Phase 2: Enforcement (PR #29)
  ┌─────────────────────────────────────────────────┐
  │ Action arrives at enforcement gate              │
  │         │                                       │
  │         ▼                                       │
  │ Evaluate against risk catalog + tenant override │
  │         │                                       │
  │    ┌────┴────────────┬──────────────┐          │
  │    ▼                 ▼              ▼          │
  │  allow          require_review   block         │
  │    │                 │              │          │
  │    │                 ▼              ▼          │
  │    │           Evidence Pack    Stop + log     │
  │    │           persisted (30d)                 │
  │    ▼                                           │
  │  Execute                                       │
  └─────────────────────────────────────────────────┘

  Automated channel escalation:
  workflow/webhook/local_agent + require_confirmation
    → auto-escalated to require_review
    (no human in loop to confirm)

Phase 3: Trust-Aware Autonomy (PR #30)
  Trust score = 0.7 × reward_signal + 0.3 × provider_signal
                weighted by confidence, decays toward 0.5

  Autonomy tiers:
  ┌───────────────────────┬────────────────────────────┐
  │ Tier                  │ What the agent can do      │
  ├───────────────────────┼────────────────────────────┤
  │ observe_only          │ Nothing (blocked)          │
  │ recommend_only        │ Read-only tools only       │
  │ supervised_execution  │ Review required for high   │
  │ bounded_autonomous    │ Full access within budget  │
  └───────────────────────┴────────────────────────────┘

  Confidence ramp:
  0 experiences → confidence=0.0 → observe_only (safest default)
  25 rated + 10 councils → confidence≈1.0 → tier based on score

Bypass Fixes (PR #31)
  • Tenant override ceiling (can't relax HIGH/CRITICAL)
  • Workflow agent step enforcement gate
  • External MCP connector enforcement gate
```

### Gap 02: Self-Model & Goals

**Why second**: Agents that remember commitments are more useful than agents that plan better.

```
Phase 1: Goals & Commitments (PR #32)

  goal_records state machine:
  proposed → active → blocked → completed
                              → abandoned

  commitment_records state machine:
  open → in_progress → fulfilled
                     → broken
                     → cancelled

  ┌─────────────────────────────────────────────────────┐
  │ Goal: Close Q1 pipeline                             │
  │ State: active | Priority: high | Progress: 40%      │
  │                                                     │
  │ Commitments:                                        │
  │  • Send proposal to Acme Corp (due: Mar 28) [open]  │
  │  • Schedule demo with Beta (due: Mar 26) [fulfilled] │
  │                                                     │
  │ Sub-goals:                                          │
  │  • Research competitor pricing [completed]           │
  │  • Draft contract terms [active]                    │
  └─────────────────────────────────────────────────────┘

Phase 2: Identity Profiles (PR #33)

  Every CLI session now receives:
  ┌─────────────────────────────────────────────────┐
  │ ## Agent Identity: luna                         │
  │ Role: AI chief of staff                         │
  │ Mandate: Manage deals, schedule follow-ups      │
  │ Risk posture: moderate                          │
  │ Planning style: step_by_step                    │
  │ Strengths: CRM analysis, email drafting         │
  │ Weaknesses: complex financial modeling          │
  │ Allowed tools: email, calendar, knowledge       │
  │ Denied tools: execute_shell                     │
  │                                                 │
  │ ## Active Goals                                 │
  │ - [active] Close Q1 pipeline (priority: high)   │
  │                                                 │
  │ ## Open Commitments                             │
  │ - Send proposal to Acme (due: Mar 28)           │
  └─────────────────────────────────────────────────┘

  Identity is dynamic per agent — not hardcoded Luna.
  No profile? Falls back to Luna identity.

Phase 3: Goal Review Workflow (PR #34)

  GoalReviewWorkflow (Temporal, 6h cycle):
  ┌────────────────────────────────────────────┐
  │ review_goals                               │
  │  • Stalled: not reviewed in 7 days         │
  │  • No progress: 0% after 14 days           │
  │  • Long blocked: blocked > 3 days          │
  │  • Overdue: past deadline                  │
  │                                            │
  │ review_commitments                         │
  │  • Overdue: past due_at and still open     │
  │  • Stale: not touched in 7 days            │
  │                                            │
  │ create_review_notifications                │
  │  • Dedup via reference_id                  │
  │  • Dismissed → re-created on next cycle    │
  │  • Active → skipped (no spam)              │
  │                                            │
  │ sleep(6h) → continue_as_new                │
  └────────────────────────────────────────────┘
```

### Gap 01: World Model

**Why third**: Transforms raw observations into structured state that planning can reason over.

```
Phase 1: Assertion Layer (PR #35)

  Observation → assert_state() → Assertion → Snapshot

  ┌─────────────────────────────────────────────────┐
  │ WorldStateAssertion                             │
  │                                                 │
  │ subject: lead:acme                              │
  │ attribute: stage                                │
  │ value: "proposal"                               │
  │ confidence: 0.85                                │
  │ source: observation (from email scan)           │
  │ TTL: 168 hours (7 days)                         │
  │ status: active                                  │
  │ corroboration_count: 3                          │
  └─────────────────────────────────────────────────┘

  Assertion lifecycle:
  ┌───────────────────────────────────────┐
  │ Same subject+attribute arrives:       │
  │                                       │
  │ Same value?                           │
  │   → Corroborate (+0.05 confidence)    │
  │                                       │
  │ Different value, same source type?    │
  │   → Supersede (old marked superseded) │
  │                                       │
  │ Different value, different source?    │
  │   → DISPUTE (both marked disputed)   │
  │   → Neither appears in snapshot      │
  │   → Resolve manually or auto         │
  └───────────────────────────────────────┘

  Snapshots auto-recompute on every change:
  ┌─────────────────────────────────────────────────┐
  │ WorldStateSnapshot: lead:acme                   │
  │                                                 │
  │ projected_state:                                │
  │   stage: "proposal"                             │
  │   contact: "Simon"                              │
  │   last_activity: "2026-03-24"                   │
  │                                                 │
  │ confidence: avg=0.82, min=0.65                  │
  │ unstable: [last_activity] (nearing TTL)         │
  │ disputed: [] (none currently)                   │
  └─────────────────────────────────────────────────┘

Phase 2: Conflict & Freshness (PR #36)

  Confidence decay:
  ┌─────────────────────────────────────────────────┐
  │                                                 │
  │ Confidence                                      │
  │ 1.0 ┤████████████████                           │
  │     │                ████                       │
  │ 0.5 ┤                    ████                   │
  │     │                        ████               │
  │ 0.0 ┤────────────────────────────████──── time  │
  │     0%        50%        75%      100% of TTL   │
  │                                                 │
  │ Decay starts at 50% of TTL                      │
  │ Expiry at 100% removes from snapshot            │
  └─────────────────────────────────────────────────┘

  Dispute resolution:
  GET /world-state/disputes → list conflicting claims
  POST /world-state/disputes/{id}/resolve
    → resolution="active" (reactivate, supersede others)
    → resolution="superseded" (dismiss this claim)

Phase 3: Causal Graph (PR #37)

  cause_type → effect_type with confidence:

  ┌─────────────────────────────────────────────────┐
  │ email_followup_sent → meeting_booked            │
  │   confidence: 0.7                               │
  │   observations: 5                               │
  │   status: corroborated                          │
  │                                                 │
  │ proposal_sent → deal_advanced                   │
  │   confidence: 0.9                               │
  │   observations: 12                              │
  │   status: confirmed                             │
  └─────────────────────────────────────────────────┘

  Status progression:
  hypothesis (1 obs) → corroborated (3+) → confirmed (10+)
                                          → disproven

Phase 4: State-First Prompts (PR #38)

  Every CLI session now receives (in addition to identity/goals):

  ┌─────────────────────────────────────────────────┐
  │ ## Current World State                          │
  │ ### lead:acme                                   │
  │ - stage: proposal                               │
  │ - contact: Simon                                │
  │ - Confidence: avg=0.82, min=0.65 (fresh)        │
  │                                                 │
  │ ## Assumptions Needing Verification             │
  │ - lead:acme.last_activity = "2026-03-20"        │
  │   (confidence: 0.42)                            │
  │                                                 │
  │ ## Known Causal Patterns                        │
  │ - email_followup → meeting_booked               │
  │   (confidence: 0.7, seen 5x)                    │
  └─────────────────────────────────────────────────┘
```

### Gap 03: Long-Horizon Planning

**Why fourth**: Once goals and world state exist, plans can reason over real state.

```
Phase 1: Plan Records (PR #39)

  4 first-class tables (NOT JSON blobs):

  ┌─────────────────────────────────────────────────┐
  │ Plan: "Close Acme deal"                         │
  │ Status: executing | Version: 2 | Replan: 1      │
  │ Budget: 10 actions / $5.00 / 48h                │
  │ Used:   4 actions / $1.23 / 12h                 │
  │                                                 │
  │ Steps:                                          │
  │ [0] ✓ Research competitor pricing               │
  │ [1] ✓ Draft proposal document                   │
  │ [2] ▶ Send proposal to Simon        ← current  │
  │ [3] ○ Schedule follow-up call                   │
  │ [4] ○ Close or escalate                         │
  │                                                 │
  │ Assumptions:                                    │
  │ • Simon is the decision maker [valid]           │
  │ • Budget approved for Q1 [unverified]           │
  │                                                 │
  │ Events (audit trail):                           │
  │ • step_completed: step 1 (12m ago)              │
  │ • step_started: step 2 (10m ago)                │
  │ • budget_warning: actions at 80%                │
  └─────────────────────────────────────────────────┘

Phase 2: Replanning Engine (PR #40)

  Step fails → classify → repair

  ┌─────────────────────────────────────────────────┐
  │ Failure Classification:                         │
  │                                                 │
  │ "timeout"           → transient    → retry      │
  │ "data not found"    → missing_info → pause      │
  │ "assumption invalid"→ invalid_asn  → replan     │
  │ "approval required" → blocked      → escalate   │
  │ "state changed"     → world_change → replan     │
  │                                                 │
  │ Retry with fallback:                            │
  │ step fails → retry (up to max_attempts)         │
  │           → retries exhausted?                  │
  │             → fallback_step_index exists?        │
  │               → jump to fallback step            │
  │               → no fallback → plan fails         │
  │                                                 │
  │ Resume:                                         │
  │ POST /plans/{id}/resume                         │
  │   → resumes from current_step_index             │
  │   → resets retry budget                         │
  │   → budget check before restart                 │
  └─────────────────────────────────────────────────┘

Phase 3: Budget Enforcement (PR #41)

  ┌─────────────────────────────────────────────────┐
  │ After every step completion:                    │
  │                                                 │
  │ check_budget()                                  │
  │   │                                             │
  │   ├── actions_used >= max_actions?              │
  │   │     → PAUSE plan                            │
  │   │                                             │
  │   ├── cost_used >= max_cost_usd?                │
  │   │     → PAUSE plan                            │
  │   │                                             │
  │   ├── runtime >= max_runtime_hours?             │
  │   │     → PAUSE plan                            │
  │   │                                             │
  │   ├── any budget at 80%?                        │
  │   │     → log budget_warning event              │
  │   │                                             │
  │   └── all OK → start next step                  │
  │                                                 │
  │ Also enforced on:                               │
  │   • Plan start (draft → executing)              │
  │   • Plan resume (paused → executing)            │
  └─────────────────────────────────────────────────┘
```

### Gap 06: Society of Agents

**Why fifth**: After single-agent foundations are solid, add multi-agent coordination.

```
Phase 1: Shared Blackboard (PR #42)

  Append-only collaboration surface:

  ┌─────────────────────────────────────────────────┐
  │ Blackboard: "Analyze Acme deal strategy"        │
  │ Version: 7 | Status: active                     │
  │                                                 │
  │ v1 [planner] PROPOSAL: Focus on ROI messaging   │
  │ v2 [critic]  CRITIQUE: Missing competitor comp   │
  │ v3 [researcher] EVIDENCE: Competitor charges 2x  │
  │ v4 [critic]  DISAGREEMENT: ROI isn't the issue   │
  │ v5 [synthesizer] RESOLUTION: Resolved v4 →       │
  │     combine ROI + competitive positioning        │
  │ v6 [planner] PROPOSAL: Revised strategy          │
  │     (supersedes v1)                              │
  │ v7 [verifier] EVIDENCE: Approved revised plan    │
  └─────────────────────────────────────────────────┘

  Key properties:
  • Append-only: entries never mutated, only superseded
  • Versioned: atomic increment via row lock
  • Authority hierarchy:
    auditor > synthesizer > verifier > critic
    > executor > planner > researcher > contributor
  • Replayable: version diff shows exact collaboration history

Phase 2: Collaboration Patterns (PR #43)

  4 formal patterns:

  propose_critique_revise:
  ┌──────────┐   ┌──────────┐   ┌──────────┐   ┌──────────┐
  │ PROPOSE  │──▶│ CRITIQUE │──▶│  REVISE  │──▶│  VERIFY  │
  │ (planner)│   │ (critic) │   │ (planner)│   │(verifier)│
  └──────────┘   └──────────┘   └──────────┘   └──────────┘
                                                     │
                                    disagrees? ──────┘
                                        │
                                        ▼
                                  back to CRITIQUE
                                  (up to max_rounds)

  plan_verify:        propose → verify
  research_synthesize: research → synthesize → verify
  debate_resolve:     propose → debate → resolve

  Role enforcement:
  • Session creation requires role assignments for all required roles
  • Each phase validates the contributing agent has the correct role
  • Terminal phase requires explicit agrees_with_previous (true/false)
  • Session outcome stores the accepted proposal, not verifier's note

Phase 3: Learned Coalition Routing (PR #44)

  ┌─────────────────────────────────────────────────┐
  │ Coalition Template: "Code Review Team"          │
  │ Pattern: propose_critique_revise                │
  │ Roles: planner=luna, critic=codex, verifier=gemma│
  │ Task types: [code, refactor]                    │
  │                                                 │
  │ Performance (per task type):                    │
  │   code:     12 uses, 83% success, avg=0.78      │
  │   refactor:  5 uses, 80% success, avg=0.72      │
  └─────────────────────────────────────────────────┘

  Recommendation scoring (per task type):
  score = success_rate × 0.5
        + normalized_quality × 0.3
        + cost_efficiency × 0.2

  GET /coalitions/recommend?task_type=code
  → Returns ranked templates with scores and reasoning
```

### Gap 04: Self-Improvement

**Why last**: Requires the most accumulated data. Exploration mode was collecting training data while we built Gaps 1-3.

```
Phase 1: Candidate Policy Pipeline (PR #45)

  ┌─────────────────────────────────────────────────┐
  │ RL Experiences (2,800+)                         │
  │         │                                       │
  │         ▼                                       │
  │ generate_routing_candidates()                   │
  │ Analyzes per-platform reward distributions      │
  │ Proposes changes when >10% improvement exists   │
  │         │                                       │
  │         ▼                                       │
  │ PolicyCandidate:                                │
  │   "Route more to codex (avg=0.63) away from     │
  │    claude_code (avg=0.51)"                      │
  │   baseline_reward: 0.51                         │
  │   expected_improvement: 23.5%                   │
  │         │                                       │
  │         ▼                                       │
  │ create_experiment(type=offline)                 │
  │         │                                       │
  │         ▼                                       │
  │ run_offline_evaluation()                        │
  │                                                 │
  │ CRITICAL: Only uses exploration-routed data     │
  │ Control = exploration experiences NOT matching   │
  │   proposed policy (true baseline)               │
  │ Treatment = exploration experiences matching     │
  │   proposed policy (disjoint from control)       │
  │         │                                       │
  │    ┌────┴────────────┐                          │
  │    ▼                 ▼                          │
  │ significant       not significant               │
  │    │                 │                          │
  │    ▼                 ▼                          │
  │ promote()        reject(reason)                 │
  │ (requires        candidate archived             │
  │  experiment                                     │
  │  with                                           │
  │  is_significant                                 │
  │  = "yes")                                       │
  └─────────────────────────────────────────────────┘

Phase 2: Controlled Rollout (PR #46)

  ┌─────────────────────────────────────────────────┐
  │ Split rollout (A/B test in production):         │
  │                                                 │
  │ Request arrives at router                       │
  │         │                                       │
  │    ┌────┴────┐                                  │
  │    │ 10%     │ 90%                              │
  │    ▼         ▼                                  │
  │ treatment  control                              │
  │ (proposed) (current)                            │
  │    │         │                                  │
  │    ▼         ▼                                  │
  │ Response  Response                              │
  │    │         │                                  │
  │    ▼         ▼                                  │
  │ Auto-scorer assigns reward                      │
  │ record_rollout_observation(reward)              │
  │    │                                            │
  │    ▼                                            │
  │ Check auto-rollback:                            │
  │   treatment regression > 15%?                   │
  │     → ABORT experiment                          │
  │     → REJECT candidate                          │
  │                                                 │
  │ Check completion:                               │
  │   both arms >= min_sample_size?                 │
  │   OR max_duration exceeded?                     │
  │     → COMPLETE with results                     │
  │                                                 │
  │ Exclusivity:                                    │
  │   DB unique index prevents concurrent rollouts  │
  │   per tenant + decision_point                   │
  └─────────────────────────────────────────────────┘

Phase 3: Learning Dashboards (PR #47)

  GET /learning/dashboard/improvements
  ┌─────────────────────────────────────────────────┐
  │ Promoted Policies (3 total):                    │
  │                                                 │
  │ 1. Route chat_response to codex                 │
  │    Baseline: 0.510  →  Actual: 0.629            │
  │    Improvement: +23.3%                          │
  │    Promoted: 2026-03-24                         │
  │                                                 │
  │ 2. Lower risk threshold for code tasks          │
  │    Baseline: 0.45   →  Actual: 0.52             │
  │    Improvement: +15.6%                          │
  │    Promoted: 2026-03-23                         │
  └─────────────────────────────────────────────────┘

  GET /learning/dashboard/explore-exploit
  ┌─────────────────────────────────────────────────┐
  │ chat_response (last 7 days):                    │
  │   Total: 150 experiences                        │
  │   Explore: 70% (codex training)                 │
  │   Exploit: 25% (RL-learned routing)             │
  │   Rollout:  5% (split experiment)               │
  │                                                 │
  │   Platforms:                                    │
  │   codex:       105 | avg_reward: 0.63           │
  │   claude_code:  38 | avg_reward: 0.51           │
  │   gemini_cli:    7 | avg_reward: 0.44           │
  └─────────────────────────────────────────────────┘

  GET /learning/dashboard/stalls
  ┌─────────────────────────────────────────────────┐
  │ Stalled (no promotion in 14 days):              │
  │   • agent_routing: 5 candidates, 2 pending      │
  │                                                 │
  │ Active:                                         │
  │   • chat_response: promoted 2 days ago          │
  └─────────────────────────────────────────────────┘
```

---

## Database Tables Added (15 total)

```
Gap 05: Safety & Trust
  ├── tenant_action_policies       tenant policy overrides
  ├── safety_evidence_packs        enforcement audit trail (30d TTL)
  └── agent_trust_profiles         per-agent trust scores + autonomy tiers

Gap 02: Self-Model & Goals
  ├── goal_records                 durable goals with state machine
  ├── commitment_records           agent promises with due dates
  └── agent_identity_profiles      auditable operating profiles

Gap 01: World Model
  ├── world_state_assertions       normalized claims with provenance
  └── world_state_snapshots        auto-projected current state per entity

Gap 01: Causal Graph
  └── causal_edges                 cause→effect with confidence

Gap 03: Long-Horizon Planning
  ├── plans                        versioned plans with budgets
  ├── plan_steps                   first-class execution steps
  ├── plan_assumptions             tracked dependencies on world state
  └── plan_events                  full audit trail

Gap 06: Society of Agents
  ├── blackboards                  shared task collaboration surfaces
  ├── blackboard_entries           append-only versioned entries
  ├── collaboration_sessions       formal multi-agent patterns
  ├── coalition_templates          reusable team shapes
  └── coalition_outcomes           per-task-type performance

Gap 04: Self-Improvement
  ├── policy_candidates            proposed policy changes
  └── learning_experiments         controlled evaluations
```

---

## RL Feedback Loop (End-to-End)

```
User message
    │
    ▼
Agent Router
    │ trust profile lookup
    │ RL-learned routing
    │ policy rollout check (split A/B)
    │ exploration mode (70/30)
    ▼
CLI Session Manager
    │ inject: identity + goals + commitments
    │ inject: world state + unstable assertions
    │ inject: causal patterns
    ▼
Safety Enforcement
    │ risk catalog evaluation
    │ trust-tier restrictions
    │ evidence pack persistence
    ▼
CLI Execution (Claude/Codex/Gemini)
    │ 81 MCP tools
    │ full rotation fallback
    ▼
Response
    │
    ├──▶ Auto Quality Scorer (Gemma 4, 6-dim, 100pts)
    │       │
    │       ├──▶ RL Experience logged (state, action, reward)
    │       │
    │       ├──▶ Rollout observation recorded (if experiment active)
    │       │       │
    │       │       ├── auto-rollback on 15% regression
    │       │       └── auto-complete on min samples
    │       │
    │       ├──▶ Provider Council (20% sample, or if low score/fragile)
    │       │       Claude + Codex + Gemma 4 in parallel
    │       │
    │       └──▶ Trust profile recompute (6h staleness refresh)
    │
    ├──▶ Goal Review Workflow (6h cycle)
    │       detects stalled/overdue goals and commitments
    │
    └──▶ Learning Pipeline
            generate_routing_candidates() → PolicyCandidate
            run_offline_evaluation() → counterfactual A/B
            promote/reject based on measured improvement
            start_rollout() → live split test
            learning dashboard → visibility into all of the above
```

---

## PRs Merged

```
#28  Gap 05 Phase 1 — Risk taxonomy & policy engine
#29  Gap 05 Phase 2 — Policy enforcement & evidence packs
#30  Gap 05 Phase 3 — Trust-aware autonomy
#31  Gap 05 — Enforcement bypass fixes
#32  Gap 02 Phase 1 — Goal & commitment records
#33  Gap 02 Phase 2 — Agent identity profiles + runtime injection
#34  Gap 02 Phase 3 — Periodic goal review workflow
#35  Gap 01 Phase 1 — World state assertions & snapshots
#36  Gap 01 Phase 2 — Conflict detection, confidence decay, dispute resolution
#37  Gap 01 Phase 3 — Causal graph linking actions to outcomes
#38  Gap 01 Phase 4 — State-first agent prompts
#39  Gap 03 Phase 1 — Plan runtime with steps, assumptions, events
#40  Gap 03 Phase 2 — Replanning engine with failure classification
#41  Gap 03 Phase 3 — Budget-aware plan execution
#42  Gap 06 Phase 1 — Shared blackboard for multi-agent collaboration
#43  Gap 06 Phase 2 — Structured collaboration patterns
#44  Gap 06 Phase 3 — Learned coalition routing
#45  Gap 04 Phase 1 — Self-improvement pipeline with experiment framework
#46  Gap 04 Phase 2 — Controlled rollout with auto-rollback
#47  Gap 04 Phase 3 — Learning dashboards
```

---

## Cross-Review Statistics

Every PR was reviewed by Codex (and several by Luna) before merge.
Total review findings across all PRs:

```
P1 (critical, merge-blocking):  ~40 findings, all fixed before merge
P2 (important):                 ~25 findings, all fixed
P3 (minor):                     ~10 findings, most fixed

Common finding categories:
  • Cross-tenant reference validation (most frequent P1)
  • State machine inconsistencies
  • Append-only / atomicity violations
  • Route ordering in FastAPI
  • Authority bypass via client-supplied fields
  • Evaluation bias in self-improvement pipeline
```

---

## What's Next

The AGI roadmap gaps are implemented. The platform now has the infrastructure
for durable agent behavior. The next priorities are:

1. **Copilot CLI integration** (design doc ready at `docs/plans/2026-03-24-copilot-cli-integration-design.md`)
2. **Shadow mode for rollouts** (Phase 3 — requires parallel CLI execution)
3. **Frontend wiring** for the new dashboard APIs
4. **Test coverage** for the new plan/rollout/collaboration paths
5. **Production deployment** and E2E validation under real traffic
