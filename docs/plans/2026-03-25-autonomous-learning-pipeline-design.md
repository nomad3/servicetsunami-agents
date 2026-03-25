# Autonomous Learning Pipeline — Design Document

**Date**: 2026-03-25
**Status**: Design
**Depends on**: Gap 04 (self-improvement pipeline), Gap 05 (safety governance), Gap 02 (goals/commitments)

---

## 1. Goal

Build a nightly autonomous learning cycle where the platform:

1. Analyzes its own performance across all decision points
2. Generates policy candidates from patterns in RL data
3. Evaluates candidates against controlled baselines
4. Promotes or rejects candidates based on measured improvement
5. Starts controlled rollouts for promoted candidates
6. Reports findings to the human operator for review
7. Incorporates human feedback into the next cycle

The platform should own its learning end-to-end, with humans providing
oversight and course correction — not manual intervention at every step.

---

## 2. Architecture Overview

```
┌─────────────────────────────────────────────────────────────────────┐
│                    NIGHTLY LEARNING CYCLE                           │
│                  (AutonomousLearningWorkflow)                       │
│                                                                     │
│  ┌─────────┐  ┌──────────┐  ┌──────────┐  ┌─────────┐  ┌───────┐ │
│  │ Collect  │→│ Generate  │→│ Evaluate  │→│ Decide   │→│ Report│ │
│  │ & Analyze│  │Candidates│  │ Offline   │  │Promote/ │  │Morning│ │
│  │          │  │          │  │           │  │Reject   │  │Summary│ │
│  └─────────┘  └──────────┘  └──────────┘  └─────────┘  └───────┘ │
│       │                                        │             │     │
│       ▼                                        ▼             ▼     │
│  ┌─────────┐                            ┌──────────┐  ┌─────────┐ │
│  │ Start   │                            │ Auto-    │  │ Human   │ │
│  │ Rollouts│                            │ Rollback │  │ Feedback│ │
│  │ (split) │                            │ Monitor  │  │ Loop    │ │
│  └─────────┘                            └──────────┘  └─────────┘ │
│                                                                     │
│  Runs daily at 02:00 UTC via Temporal continue_as_new              │
│  Queue: servicetsunami-orchestration                                │
└─────────────────────────────────────────────────────────────────────┘
```

---

## 3. Nightly Candidate Generation

### 3.1 Inputs

The cycle analyzes RL experiences accumulated since the last run:

```
┌─────────────────────────────────────────────────────────────────────┐
│ Input Sources:                                                      │
│                                                                     │
│  rl_experiences          — reward, platform, agent, task_type       │
│  coalition_outcomes      — team shape performance per task type     │
│  auto_quality_scores     — 6-dimension rubric breakdown             │
│  provider_council_results — multi-provider agreement/disagreement   │
│  agent_trust_profiles    — per-agent trust trajectory               │
│  goal_records            — stalled/completed goal patterns          │
│  commitment_records      — fulfillment/broken rates                 │
│  world_state_assertions  — dispute frequency, decay patterns        │
│  plan_events             — replan/failure frequency by step type    │
└─────────────────────────────────────────────────────────────────────┘
```

### 3.2 Candidate Generation Filters

Not every pattern warrants a policy candidate. Filters prevent noise:

```
Filter                          Threshold       Rationale
──────────────────────────────  ──────────────  ─────────────────────────
Minimum sample size             ≥ 20 experiences  Avoid noise from small samples
Statistical improvement         > 10%             Don't propose marginal gains
Confidence interval             p < 0.1           Require directional confidence
Recency                         Last 14 days      Don't learn from stale data
Diversity                       ≥ 2 platforms     Need comparison data
Exclude already-evaluated       Skip if candidate Prevent re-proposing rejected
                                exists for same   candidates
                                policy change
```

### 3.3 Candidate Types Generated

```
Type                    Source Analysis              Example
──────────────────────  ─────────────────────────── ─────────────────────────
routing_platform        Per-platform reward avg      "Route code tasks to codex"
routing_agent           Per-agent reward by type     "Use luna for sales, codex for code"
coalition_shape         Coalition outcome stats      "Use planner+verifier for code tasks"
risk_threshold          Safety enforcement patterns  "Lower threshold for search tools"
memory_recall           Entity recall vs reward      "Recall more entities for sales"
replanning_heuristic    Plan failure patterns        "Retry transient errors 5x not 3x"
```

---

## 4. Offline Evaluation Gate

Every candidate MUST pass offline evaluation before any live action.

### 4.1 Evaluation Protocol

```
┌─────────────────────────────────────────────────────────────────────┐
│ For each candidate:                                                 │
│                                                                     │
│  1. Create LearningExperiment(type=offline)                        │
│                                                                     │
│  2. run_offline_evaluation()                                       │
│     • Control: exploration experiences NOT matching proposed policy │
│     • Treatment: exploration experiences matching proposed policy   │
│     • Both filtered to exploration-routed data only                │
│     • Min sample size: 20 per arm                                  │
│                                                                     │
│  3. Check results:                                                 │
│     • is_significant == "yes" AND improvement_pct > 5%             │
│       → candidate passes evaluation gate                           │
│     • is_significant == "no" OR insufficient_data                  │
│       → candidate stays proposed (re-evaluate next cycle)          │
│     • improvement_pct < -5% (regression)                           │
│       → auto-reject with reason                                    │
│                                                                     │
│  4. Gate is MANDATORY — no candidate reaches rollout without it    │
└─────────────────────────────────────────────────────────────────────┘
```

### 4.2 Insufficient Data Handling

If offline evaluation returns `insufficient_data`:

- The candidate is NOT rejected (it may become evaluable with more data)
- If insufficient exploration data exists, the cycle temporarily sets
  `EXPLORATION_MODE=balanced` globally to collect unbiased data across
  all platforms. **Note**: the current platform only has global exploration
  knobs (`EXPLORATION_MODE` / `EXPLORATION_RATE`), not per-decision-point
  controls. Per-decision-point exploration is a required infrastructure
  addition for Phase 3 — until then, exploration adjustments affect all
  decision points in the tenant.
- Re-evaluated on the next nightly cycle
- After 3 cycles with insufficient data → auto-reject with reason

---

## 5. Rollout Start/Stop Policy

### 5.1 When to Start a Rollout

```
Conditions for automatic rollout start:
  ✓ Candidate passed offline evaluation (is_significant=yes)
  ✓ No other rollout running for same decision_point
  ✓ Agent trust score >= recommend_only tier
  ✓ No active safety incident for this tenant
  ✓ Total tenant experiences > 100 (enough baseline)
```

### 5.2 Rollout Configuration

```
Parameter               Default     Rationale
──────────────────────  ──────────  ─────────────────────────
rollout_pct             0.10        Start conservative (10%)
min_sample_size         30          Need enough for significance
max_duration_hours      168         One week max
experiment_type         split       Only split supported (Phase 2)
```

### 5.3 When to Stop a Rollout

```
Automatic stop conditions:
  • Both arms reach min_sample_size → complete with results
  • max_duration_hours exceeded → complete with whatever data exists
  • treatment regression > 15% (after 10 samples) → abort + reject
  • Safety incident triggered by treatment → abort + reject
  • Human override → stop immediately
```

---

## 6. Auto-Promotion and Auto-Rejection Rules

### 6.1 Auto-Promotion

A candidate is automatically promoted when ALL of these are true:

```
┌─────────────────────────────────────────────────────────────────────┐
│ Auto-Promotion Checklist:                                           │
│                                                                     │
│  ✓ Offline evaluation: is_significant == "yes"                     │
│  ✓ Live rollout: completed with is_significant == "yes"            │
│  ✓ Live rollout: improvement_pct > 5%                              │
│  ✓ Live rollout: treatment_sample_size >= min_sample_size          │
│  ✓ No auto-rollback triggered during rollout                      │
│  ✓ No safety enforcement escalations during treatment              │
│  ✓ Provider council agreement >= 60% on treatment responses        │
│                                                                     │
│  If ALL pass → promote_candidate()                                 │
│  If ANY fail → keep as evaluating, report to human                 │
└─────────────────────────────────────────────────────────────────────┘
```

### 6.2 Auto-Rejection

A candidate is automatically rejected when ANY of these are true:

```
┌─────────────────────────────────────────────────────────────────────┐
│ Auto-Rejection Conditions:                                          │
│                                                                     │
│  ✗ Offline evaluation shows > 5% regression                       │
│  ✗ Live rollout aborted due to > 15% regression                   │
│  ✗ Safety enforcement blocked treatment responses > 3 times        │
│  ✗ Provider council rejected treatment responses > 30% of time     │
│  ✗ 3 nightly cycles with insufficient evaluation data              │
│  ✗ Human explicitly rejected                                       │
│                                                                     │
│  → reject_candidate(reason=<specific condition>)                   │
└─────────────────────────────────────────────────────────────────────┘
```

---

## 7. Safety and Trust Constraints

The autonomous learning system operates WITHIN the safety governance layer,
not around it.

### 7.1 What the Learning System Can Change

```
Allowed (bounded config changes):
  ✓ Routing platform preferences (which CLI handles which task type)
  ✓ Exploration rates per decision point
  ✓ Coalition template selection for task types
  ✓ Risk threshold relaxation for LOW-risk actions only
  ✓ Memory recall depth (how many entities to load)
  ✓ Retry policy parameters (max_attempts, backoff)
```

### 7.2 What the Learning System Cannot Change

```
Blocked (requires human approval):
  ✗ Safety policy overrides for HIGH/CRITICAL actions
  ✗ Tenant action policies (the ceiling enforcement)
  ✗ Agent identity profiles (role, mandate, domain boundaries)
  ✗ Trust tier thresholds
  ✗ Budget limits on plans
  ✗ Blackboard authority hierarchy
  ✗ Any production code or prompt modifications
```

### 7.3 Trust Gate

```
The learning system itself has a trust profile:

  Agent slug: "learning_system"
  Initial tier: recommend_only

  This means:
  • It can propose and evaluate candidates
  • It can start rollouts (because rollouts are bounded)
  • It CANNOT promote candidates without passing the experiment gate
  • It CANNOT modify safety policies
  • All its actions are logged as RL experiences

  Over time, if its promotions consistently improve outcomes,
  its trust score rises and it earns more autonomy.
```

---

## 8. Rollback Conditions

### 8.1 Automatic Rollback

```
Trigger                                     Action
──────────────────────────────────────────  ─────────────────────────
Treatment reward regression > 15%           Abort rollout, reject candidate
Safety enforcement blocks > 3 treatment     Abort rollout, reject candidate
  responses in one rollout
Provider council rejects > 30% of          Abort rollout, reject candidate
  treatment responses
Promoted policy shows -10% reward          Revert to previous policy,
  regression over 24h after promotion       demote candidate, alert human
Trust score for learning_system drops      Pause all autonomous learning
  below observe_only                        until human reviews
```

### 8.2 Manual Rollback

```
Human can at any time:
  • POST /learning/rollouts/{id}/stop → immediately stop a rollout
  • POST /learning/candidates/{id}/reject → reject any candidate
  • PATCH tenant env EXPLORATION_MODE=off → disable all exploration
  • POST /workflows/autonomous-learning/stop → pause the whole cycle
```

---

## 9. Observability

### 9.1 Dashboard Endpoints (already built)

```
GET /learning/dashboard/improvements
  → promoted policies with measured impact (before/after)

GET /learning/dashboard/stalls
  → decision points with no recent improvement

GET /learning/dashboard/explore-exploit
  → traffic split by routing source per decision point

GET /learning/dashboard/rollouts
  → active and recent rollout experiments with arm stats
```

### 9.2 Morning Summary (new)

After each nightly cycle, the system generates a structured report:

```
┌─────────────────────────────────────────────────────────────────────┐
│ Morning Learning Report — 2026-03-26                               │
│                                                                     │
│ ## What Improved                                                    │
│ • Codex routing for code tasks: +18% reward (promoted yesterday)   │
│                                                                     │
│ ## Active Experiments                                               │
│ • Coalition "planner+verifier" for sales: day 3/7, +8% so far     │
│                                                                     │
│ ## Proposed (awaiting data)                                         │
│ • Lower risk threshold for search tools: need 12 more samples      │
│                                                                     │
│ ## Rejected                                                         │
│ • Route sales to gemini: -12% regression in offline eval            │
│                                                                     │
│ ## Stalled                                                          │
│ • agent_routing: no improvement in 14 days                         │
│                                                                     │
│ ## Health                                                           │
│ • Trust: luna=0.52 (recommend_only), learning_system=0.61           │
│ • Explore/exploit: 10% exploration, 85% RL-learned, 5% rollout    │
│ • Goals: 2 active, 1 stalled, 0 overdue commitments               │
│ • World state: 45 active assertions, 3 disputed, 2 expired        │
│                                                                     │
│ ## Action Required                                                  │
│ • Review proposed coalition template for code tasks                 │
│ • 3 disputed world state assertions need resolution                │
└─────────────────────────────────────────────────────────────────────┘
```

Delivery:
- WhatsApp message to the tenant admin (via Luna)
- Notification in the web app
- Persisted as a goal_review notification

### 9.3 Audit Trail

Every autonomous decision is logged:

```
Table              What's logged
────────────────   ──────────────────────────────────────
policy_candidates  Every proposed/promoted/rejected candidate
learning_experiments  Every evaluation with control/treatment data
plan_events        Every autonomous action as a plan step
rl_experiences     Learning system's own decisions as RL data
notifications      Morning reports and alerts
```

---

## 10. Human Override Points

The system is designed for human oversight, not human replacement.

```
┌─────────────────────────────────────────────────────────────────────┐
│ Human can intervene at any point:                                   │
│                                                                     │
│ 1. BEFORE generation:                                               │
│    • Set EXPLORATION_MODE to control data collection               │
│    • Adjust PROVIDER_COUNCIL_SAMPLE_RATE for more/less review      │
│    • Modify tenant features (default_cli_platform)                 │
│                                                                     │
│ 2. AFTER generation, BEFORE evaluation:                            │
│    • Review proposed candidates in /learning/candidates             │
│    • Manually reject bad ideas before they're tested               │
│                                                                     │
│ 3. DURING rollout:                                                  │
│    • Monitor live rollout stats in /learning/dashboard/rollouts    │
│    • Stop any rollout immediately                                  │
│    • Adjust rollout_pct up or down                                 │
│                                                                     │
│ 4. AFTER promotion:                                                 │
│    • Revert to previous policy if production quality drops         │
│    • Mark learning_system trust as observe_only to pause autonomy  │
│                                                                     │
│ 5. DAILY:                                                           │
│    • Review morning summary                                        │
│    • Respond with feedback ("good call", "bad idea", "try X")      │
│    • Luna incorporates feedback into next cycle's priorities       │
└─────────────────────────────────────────────────────────────────────┘
```

---

## 11. Interaction with Exploration Mode and Learned Routing

```
┌─────────────────────────────────────────────────────────────────────┐
│ Routing Decision Flow (with autonomous learning):                   │
│                                                                     │
│ Request arrives                                                     │
│     │                                                               │
│     ▼                                                               │
│ Active rollout for this decision_point?                             │
│     │                                                               │
│ yes─┤─── no                                                        │
│     │       │                                                       │
│     ▼       ▼                                                       │
│ Apply     EXPLORATION_MODE != off?                                  │
│ rollout       │                                                     │
│ (split)   yes─┤─── no                                              │
│               │       │                                             │
│               ▼       ▼                                             │
│           Explore   RL-learned routing                              │
│           (10%)     (confidence >= 0.4)                             │
│                         │                                           │
│                     RL has confidence?                               │
│                         │                                           │
│                     yes─┤─── no                                    │
│                         │       │                                   │
│                         ▼       ▼                                   │
│                     Use RL    default_cli_platform                   │
│                     platform  (last-resort baseline)                │
│                                                                     │
│ All paths → fallback chain on credit exhaustion:                   │
│   Claude → Codex → (Copilot when integrated)                      │
│   Codex → Claude → (Copilot when integrated)                      │
└─────────────────────────────────────────────────────────────────────┘
```

### 11.1 Exploration Data Collection Strategy

```
The autonomous learning system manages exploration strategically:

Cycle 1 (initial):
  EXPLORATION_MODE=balanced, EXPLORATION_RATE=0.15
  → Collect unbiased data across all platforms

Cycle 2+ (steady state):
  EXPLORATION_MODE=balanced, EXPLORATION_RATE=0.10
  → RL routing handles 90% of traffic (learned decisions)
  → 10% randomly explores underrepresented platforms
  → Rollouts provide additional controlled treatment data
  → This ensures the RL system always has fresh comparative data

When learning is stalled globally (no improvement in 14 days):
  → Temporarily increase EXPLORATION_RATE to 0.20
  → Collect more comparative data across all platforms
  → Revert to 0.10 after one cycle
  → NOTE: per-decision-point exploration control is a Phase 3
    infrastructure addition. Until then, exploration adjustments
    are global.
```

---

## 12. Workflow Implementation

### 12.1 AutonomousLearningWorkflow

```
Temporal workflow on servicetsunami-orchestration queue.
One instance per tenant. Runs nightly via continue_as_new.
Workflow ID: autonomous-learning-{tenant_id}

Activities:
  1. collect_learning_metrics(tenant_id)
     → RL stats, trust profiles, goal health, world state health

  2. generate_candidates(tenant_id, metrics)
     → Policy candidates from pattern analysis
     → Filtered by thresholds and dedup

  3. evaluate_candidates(tenant_id)
     → Run offline evaluation for all proposed candidates
     → Auto-reject regressions, keep insufficient-data candidates

  4. manage_rollouts(tenant_id)
     → Start rollouts for newly passing candidates
     → Check completion/rollback on running rollouts
     → Auto-promote completed successful rollouts

  5. generate_morning_report(tenant_id, cycle_results)
     → Structured summary of what happened
     → Delivered via notification + WhatsApp

  6. process_human_feedback(tenant_id)
     → Check for human responses to previous reports
     → Adjust priorities for next cycle

  sleep(until 02:00 UTC next day) → continue_as_new
```

### 12.2 API Endpoints (new)

```
POST /workflows/autonomous-learning/start
  → Start the nightly learning cycle for a tenant

POST /workflows/autonomous-learning/stop
  → Pause the learning cycle

GET /workflows/autonomous-learning/status
  → Check if running, last cycle results

GET /learning/dashboard/morning-report
  → Get the latest morning summary

POST /learning/dashboard/feedback
  → Submit human feedback on learning decisions
```

---

## 13. Implementation Phases

### Phase 1: Nightly Candidate Generation + Evaluation

- AutonomousLearningWorkflow with collect + generate + evaluate
- Auto-reject regressions
- Morning report (notification only, no WhatsApp yet)
- API: start/stop/status

### Phase 2: Rollout Management + Auto-Promotion

- Automatic rollout starts for passing candidates
- Auto-promotion on successful rollout completion
- Auto-rollback monitoring
- WhatsApp morning summary via Luna

### Phase 3: Human Feedback Loop + Strategic Exploration

- Feedback endpoint for human responses
- Priority adjustment from feedback
- Strategic exploration management for stalled decision points
- Learning system trust profile evolution

---

## 14. Success Criteria

```
The autonomous learning pipeline is successful when:

✓ Routing quality improves measurably week-over-week
  (tracked via /learning/dashboard/improvements)

✓ Policy candidates are generated, evaluated, and promoted
  without human intervention for routine improvements

✓ Regressions are caught and rolled back within hours,
  not days (auto-rollback + morning report)

✓ The human operator spends <5 min/day reviewing the
  morning summary instead of manually analyzing RL data

✓ Stalled decision points are detected and addressed
  automatically via strategic exploration

✓ The learning system's own trust score rises over time
  as its promotions consistently improve outcomes
```

---

## 15. Risks and Mitigations

```
Risk                              Mitigation
────────────────────────────────  ──────────────────────────────────
Self-reinforcing bias             Counterfactual evaluation with
                                  exploration-only data (Phase 1)

Overfitting to recent data        14-day recency window + min samples

Runaway autonomy                  Trust-gated actions + safety layer
                                  + human override at every stage

Alert fatigue from reports        Morning report is structured and
                                  concise, not a data dump

Exploration hurting production    10% max exploration rate + rollouts
quality                           are bounded (1 week, 10% traffic)

Cascading failures                Each candidate evaluated independently
                                  + one rollout per decision point
```
