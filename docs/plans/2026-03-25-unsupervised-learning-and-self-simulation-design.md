# Unsupervised Learning & Self-Simulation Engine — Design Document

**Date**: 2026-03-25
**Status**: Design
**Depends on**: Autonomous learning pipeline (Phase 1), all 6 AGI gaps

---

## 1. Vision

The platform should be able to simulate being its own customer base every night,
exercise every capability it has, discover what's broken or weak, and improve
itself — all without human intervention. Humans review morning reports and
provide course corrections, not manual testing.

```
                    THE SELF-SIMULATION LOOP

     ┌──────────────────────────────────────────────┐
     │                                              │
     │   SIMULATE                                   │
     │   Create synthetic personas                  │
     │   Exercise platform as real users             │
     │   across 15+ industry verticals              │
     │                                              │
     ▼                                              │
┌──────────┐     ┌──────────┐     ┌──────────┐     │
│ EVALUATE  │────▶│  LEARN   │────▶│ IMPROVE  │─────┘
│           │     │          │     │          │
│ Score     │     │ Generate │     │ Evaluate │
│ every     │     │ policy   │     │ + rollout│
│ simulated │     │ candidates│    │ + promote│
│ response  │     │ from     │     │ or reject│
│           │     │ patterns │     │          │
└──────────┘     └──────────┘     └──────────┘
     │                                   │
     ▼                                   ▼
┌──────────────────────────────────────────────┐
│              MORNING REPORT                   │
│  What worked, what broke, what improved,      │
│  what needs human attention                   │
└──────────────────────────────────────────────┘
     │
     ▼
┌──────────────────────────────────────────────┐
│           HUMAN FEEDBACK                      │
│  "Good call" / "Bad idea" / "Try X instead"  │
│  Incorporated into next cycle's priorities    │
└──────────────────────────────────────────────┘
```

---

## 2. What Exists Today

### 2.1 Implemented (AGI Roadmap Complete)

```
Infrastructure Layer:
  ✓ Safety governance (111 actions, trust tiers, evidence packs)
  ✓ Self-model (goals, commitments, identity profiles)
  ✓ World model (assertions, disputes, causal graph, snapshots)
  ✓ Long-horizon planning (steps, assumptions, budgets, replanning)
  ✓ Multi-agent collaboration (blackboard, patterns, coalition routing)
  ✓ Self-improvement pipeline (candidates, experiments, rollouts, dashboards)
  ✓ Autonomous learning workflow Phase 1 (nightly metrics + evaluation)

Runtime Layer:
  ✓ CLI orchestration (Claude Code, Codex, Gemini, fallback chain)
  ✓ 102 MCP tools (knowledge, email, calendar, Jira, GitHub, ads, etc.)
  ✓ Auto quality scoring (Gemma 4 6-dim rubric, 100pts)
  ✓ Provider council (20% sample, Claude+Codex+Gemma 4)
  ✓ RL-learned routing with exploration mode
  ✓ Temporal workflows for durable execution
```

### 2.2 What's Still Missing

```
Gap                                    Impact
─────────────────────────────────────  ─────────────────────────────────
No self-simulation capability          Platform only learns from real
                                       user traffic — which is sparse

No synthetic persona engine            Can't exercise industry verticals
                                       that don't have real users yet

No proactive agent behavior            Luna only responds, never initiates
                                       (except inbox/competitor monitors)

No self-diagnosis                      System can't identify its own
                                       blind spots or capability gaps

No skill gap detection                 Doesn't know what it can't do

No feedback incorporation loop         Morning reports go out but human
                                       responses aren't processed

No cross-tenant learning               Each tenant learns in isolation;
                                       patterns from one don't help others

Limited exploration strategy           Global knobs only, no per-decision-
                                       point control

No cost optimization                   Platform doesn't track or minimize
                                       its own operating costs

No regression detection on promoted    Once a policy is promoted, no
policies                               ongoing monitoring
```

---

## 3. The Self-Simulation Engine

### 3.1 Architecture

```
┌─────────────────────────────────────────────────────────────────────┐
│                    NIGHTLY SIMULATION CYCLE                          │
│              (extends AutonomousLearningWorkflow)                    │
│                                                                     │
│  ┌─────────┐  ┌──────────┐  ┌──────────┐  ┌──────────┐           │
│  │ Select  │→│ Generate  │→│ Execute   │→│ Score    │           │
│  │ Personas│  │ Scenarios │  │ Scenarios │  │ Results  │           │
│  └─────────┘  └──────────┘  └──────────┘  └──────────┘           │
│       │                          │              │                   │
│       ▼                          ▼              ▼                   │
│  ┌─────────┐              ┌──────────┐  ┌──────────┐              │
│  │ 15+     │              │ Route to │  │ Compare  │              │
│  │ Industry│              │ CLI via  │  │ against  │              │
│  │ Verticals│             │ agent    │  │ expected │              │
│  │         │              │ router   │  │ quality  │              │
│  └─────────┘              └──────────┘  └──────────┘              │
│                                              │                     │
│                                              ▼                     │
│                                    ┌──────────────┐               │
│                                    │ Feed into    │               │
│                                    │ learning     │               │
│                                    │ pipeline     │               │
│                                    └──────────────┘               │
└─────────────────────────────────────────────────────────────────────┘
```

### 3.2 Industry Personas

Each persona simulates a real company using the platform:

```
Persona                 Industry       Typical Actions
──────────────────────  ────────────   ─────────────────────────────────
TechStartup CEO        startups       Investor outreach, pitch deck review,
                                      market research, product roadmap

PE Analyst             investment      Deal screening, portfolio monitoring,
                                      financial analysis, competitor intel

Vet Clinic Manager     veterinary      Appointment scheduling, client comms,
                                      billing inquiries, pet records

Ecommerce Operator     ecommerce      Product listings, order tracking,
                                      customer support, ad campaign mgmt

Marketing Director     marketing      Campaign management, competitor
                                      monitoring, content strategy, analytics

Sales Rep              sales          Lead qualification, follow-ups,
                                      proposal drafting, pipeline management

Law Firm Partner       law            Case research, document review,
                                      client communications, billing

Finance Controller     finance        Invoice processing, reconciliation,
                                      reporting, audit preparation

DevOps Engineer        operations     Server monitoring, deployment,
                                      incident response, infrastructure

Research Scientist     research       Literature review, data analysis,
                                      experiment tracking, collaboration

Booking Agent          bookings       Reservation management, availability
                                      checks, client communications

Recruitment Lead       HR             Candidate sourcing, interview
                                      scheduling, offer management

Real Estate Agent      real estate    Property listings, client matching,
                                      market analysis, deal tracking

Restaurant Owner       hospitality    Menu management, supplier ordering,
                                      reservation handling, reviews

Accounting Firm        accounting     Tax preparation, client queries,
                                      document collection, filing deadlines
```

### 3.3 Scenario Generation

Each persona generates realistic scenarios:

```
┌─────────────────────────────────────────────────────────────────────┐
│ Scenario Generation Pipeline:                                       │
│                                                                     │
│ 1. Select persona for tonight's simulation                         │
│    (rotate through all personas over a week)                       │
│                                                                     │
│ 2. Generate 5-10 realistic messages per persona:                   │
│    • Simple queries ("What meetings do I have tomorrow?")          │
│    • Tool-dependent tasks ("Send a follow-up to the Acme lead")   │
│    • Multi-step workflows ("Research competitors and draft report")│
│    • Knowledge-dependent ("What did we discuss with investor X?")  │
│    • Edge cases ("Cancel the meeting but only if they haven't      │
│      confirmed yet")                                               │
│                                                                     │
│ 3. Define expected quality criteria per scenario:                  │
│    • Did it use the right MCP tools?                               │
│    • Did it check memory before responding?                        │
│    • Was the response factually grounded?                          │
│    • Did it handle missing data gracefully?                        │
│    • Was the tone appropriate for the industry?                    │
│                                                                     │
│ 4. Execute via the normal agent router path                        │
│    (same path as real users — no shortcuts)                        │
│                                                                     │
│ 5. Score responses using the auto-quality scorer                   │
│    (same 6-dimension rubric as real traffic)                       │
└─────────────────────────────────────────────────────────────────────┘
```

### 3.4 Scenario Types

```
Type                    Purpose                         Example
──────────────────────  ─────────────────────────────── ─────────────────────
Simple Query            Test basic responsiveness        "What's my schedule?"
Tool Exercise           Verify MCP tools work            "Search for entity X"
Memory Recall           Test knowledge graph recall      "What do we know about Y?"
Multi-Step              Test planning and execution      "Research and draft a report"
Edge Case               Test graceful failure handling   "Book a meeting for yesterday"
Industry-Specific       Test domain knowledge            "Calculate ROI on campaign"
Cross-Tool              Test tool coordination            "Email the report to client"
Adversarial             Test safety boundaries           "Delete all customer data"
Stale Data              Test world state freshness        "Is the deal still in proposal?"
Commitment Follow-Up    Test commitment tracking          "Did I promise to send that?"
```

---

## 4. Self-Diagnosis Engine

### 4.1 Capability Gap Detection

After each simulation cycle, the system analyzes failures:

```
┌─────────────────────────────────────────────────────────────────────┐
│ Failure Analysis:                                                   │
│                                                                     │
│ For each simulated response scored below 60/100:                   │
│                                                                     │
│ 1. Classify the failure:                                           │
│    • tool_not_found: Needed a tool that doesn't exist              │
│    • tool_failed: Tool exists but returned error                   │
│    • no_memory: Knowledge graph had no relevant data               │
│    • wrong_memory: Retrieved irrelevant entities                   │
│    • bad_reasoning: Tools + memory were fine, reasoning was wrong   │
│    • safety_blocked: Action was blocked by safety layer            │
│    • timeout: CLI took too long                                    │
│    • hallucination: Response contained fabricated data              │
│                                                                     │
│ 2. Aggregate by failure type across all simulations:               │
│    tool_not_found: 12 occurrences → skill gap detected             │
│    no_memory: 8 occurrences → knowledge gap detected               │
│    timeout: 5 occurrences → performance issue                      │
│                                                                     │
│ 3. Generate improvement proposals:                                 │
│    • "Create MCP tool for appointment scheduling" (skill gap)      │
│    • "Backfill knowledge for veterinary terminology" (data gap)    │
│    • "Route veterinary queries to specialized prompt" (routing)    │
└─────────────────────────────────────────────────────────────────────┘
```

### 4.2 Skill Gap Registry

Track what the platform can't do yet:

```
┌─────────────────────────────────────────────────────────────────────┐
│ skill_gaps table:                                                   │
│                                                                     │
│ id             UUID                                                │
│ tenant_id      UUID                                                │
│ gap_type       tool_missing | knowledge_gap | prompt_weakness      │
│ description    "No appointment booking MCP tool"                   │
│ industry       "veterinary"                                        │
│ frequency      12 (how many simulations hit this gap)              │
│ severity       high (based on failure scores)                      │
│ proposed_fix   "Create book_appointment MCP tool"                  │
│ status         detected | acknowledged | in_progress | resolved    │
│ detected_at    timestamp                                           │
│ resolved_at    timestamp                                           │
└─────────────────────────────────────────────────────────────────────┘
```

---

## 5. Proactive Agent Behavior

### 5.1 What Luna Should Do Without Being Asked

```
Currently Luna only responds. She should also:

Daily:
  • Review stalled goals and nudge the user
  • Check overdue commitments and follow up
  • Scan inbox for items requiring action
  • Monitor world state assertions nearing expiry
  • Detect and flag contradictory information

Weekly:
  • Summarize pipeline progress
  • Compare this week vs last week metrics
  • Identify leads going cold
  • Suggest follow-ups based on causal patterns

On Events:
  • New email from known lead → update world state + notify
  • Calendar event approaching → prepare context briefing
  • Deal stage changed externally → update assertions
  • Competitor news detected → alert + analysis
```

### 5.2 Implementation: Proactive Action Queue

```
┌─────────────────────────────────────────────────────────────────────┐
│ proactive_actions table:                                           │
│                                                                     │
│ id             UUID                                                │
│ tenant_id      UUID                                                │
│ agent_slug     "luna"                                              │
│ action_type    nudge | followup | briefing | alert | analysis      │
│ trigger        stalled_goal | overdue_commitment | cold_lead |     │
│                expiring_assertion | new_email | calendar_prep      │
│ target_ref     goal_id / commitment_id / entity_id                 │
│ priority       high | medium | low                                 │
│ content        "Your proposal to Acme is 3 days overdue..."       │
│ channel        whatsapp | notification | email                     │
│ status         pending | sent | acknowledged | dismissed           │
│ scheduled_at   when to send                                        │
│ sent_at        when actually sent                                  │
│ created_at     timestamp                                           │
└─────────────────────────────────────────────────────────────────────┘
```

---

## 6. Feedback Incorporation Loop

### 6.1 How Human Feedback Flows Back

```
Morning Report sent via WhatsApp
    │
    ▼
Human responds (via WhatsApp or web):
    "Good call on the codex routing change"
    "Bad idea — don't route sales to gemini"
    "Try focusing on improving code task quality"
    │
    ▼
Luna parses feedback:
    ┌──────────────────────────────────────────┐
    │ feedback_records table:                   │
    │                                           │
    │ id             UUID                       │
    │ tenant_id      UUID                       │
    │ report_id      reference to morning report│
    │ candidate_id   if about a specific policy │
    │ feedback_type  approval | rejection |     │
    │                direction | correction     │
    │ content        raw message text           │
    │ parsed_intent  "approve_routing_change"   │
    │ applied        bool                       │
    │ created_at     timestamp                  │
    └──────────────────────────────────────────┘
    │
    ▼
Next cycle incorporates:
    • Approved changes get confidence boost
    • Rejected changes get auto-rejected
    • Directions become priority for next generation
    • Corrections update learning constraints
```

---

## 7. Cross-Tenant Learning (Privacy-Preserving)

### 7.1 What Can Be Shared

```
Shareable (anonymized patterns):
  ✓ "Platform X outperforms Y for task_type=code by 20%"
  ✓ "Coalition shape A works better than B for sales tasks"
  ✓ "Retry policy of 5 attempts works better than 3 for transient errors"
  ✓ "Search_knowledge before responding improves quality by 15%"

NOT shareable:
  ✗ Any tenant data, entities, messages, or content
  ✗ Specific routing decisions tied to tenant context
  ✗ Knowledge graph contents
  ✗ Identity profiles or goals
```

### 7.2 Implementation

```
The existing archive_old_experiences + anonymize_and_aggregate_global
activities in RLPolicyUpdateWorkflow already do this. Extend to:

1. Aggregate promoted policies across tenants (anonymized)
2. Seed new tenants with proven patterns
3. Share coalition templates that consistently outperform
```

---

## 8. Remaining Infrastructure Gaps

### 8.1 Per-Decision-Point Exploration Control

```
Current: Global EXPLORATION_MODE / EXPLORATION_RATE
Needed:  Per decision_point exploration overrides

decision_point_config table:
  tenant_id       UUID
  decision_point  "chat_response" | "agent_routing" | "code_task"
  exploration_rate  0.0 - 1.0
  exploration_mode  "off" | "balanced" | "targeted"
  target_platforms  ["codex", "gemini_cli"]  (for targeted mode)
  updated_at       timestamp

The autonomous learning workflow sets these per-decision-point
based on where data is stale or stalled.
```

### 8.2 Post-Promotion Regression Monitoring

```
Current: Once promoted, no ongoing monitoring
Needed:  Continuous quality check on promoted policies

After promotion:
  • Track rolling 24h reward average for the promoted platform/route
  • Compare against the pre-promotion baseline
  • If rolling average drops > 10% for > 24h:
    → Auto-revert to previous policy
    → Demote candidate back to "evaluating"
    → Alert in morning report
    → Log as causal_edge (promotion → regression)
```

### 8.3 Cost Optimization Layer

```
Current: Cost tracked but not optimized
Needed:  Active cost management

cost_optimization_service:
  • Track cost per quality point ($/QP) per platform
  • When two platforms have similar quality, prefer cheaper one
  • Alert when a platform's cost spikes
  • Include cost_efficiency in routing recommendations
  • Budget caps per tenant per day/week/month
```

### 8.4 Skill Auto-Creation Pipeline

```
Current: Skills are manually created or imported from GitHub
Needed:  Auto-create skills from simulation gaps

When self-diagnosis detects a skill gap:
  1. Generate a skill definition (prompt.md or script.py)
  2. Create as a "draft" skill in the skill marketplace
  3. Test against the simulation scenarios that triggered the gap
  4. If quality improves → promote to "community" tier
  5. Report in morning summary for human review
```

---

## 9. Implementation Phases

### Phase 1: Nightly Heartbeat (PR #50 — done)

```
✓ collect_learning_metrics
✓ generate_and_evaluate_candidates
✓ manage_active_rollouts
✓ generate_morning_report
✓ API: start/stop/status
```

### Phase 2: Self-Simulation Engine

```
Activities to add:
  • select_personas_for_cycle(tenant_id)
  • generate_simulation_scenarios(personas)
  • execute_simulation_scenarios(scenarios)
  • score_simulation_results(results)
  • classify_failures(low_scoring_results)
  • detect_skill_gaps(failure_classifications)

New tables:
  • simulation_personas
  • simulation_scenarios
  • simulation_results
  • skill_gaps
```

### Phase 3: Proactive Agent Behavior

```
Activities to add:
  • scan_for_proactive_actions(tenant_id)
  • generate_nudges(stalled_goals, overdue_commitments)
  • generate_briefings(upcoming_events, expiring_assertions)
  • send_proactive_messages(actions, channel)

New tables:
  • proactive_actions
```

### Phase 4: Feedback Loop + Self-Diagnosis

```
Activities to add:
  • process_human_feedback(tenant_id)
  • run_self_diagnosis(simulation_results)
  • generate_improvement_proposals(diagnosis)
  • update_learning_priorities(feedback, diagnosis)

New tables:
  • feedback_records
```

### Phase 5: Cost Optimization + Regression Monitoring

```
Activities to add:
  • monitor_promoted_policies(tenant_id)
  • detect_regressions(rolling_averages)
  • optimize_cost_per_quality(platforms)
  • enforce_cost_budgets(tenant_id)

New tables:
  • decision_point_config
  • cost_budgets
```

### Phase 6: Skill Auto-Creation

```
Activities to add:
  • propose_skill_from_gap(skill_gap)
  • test_proposed_skill(skill, scenarios)
  • promote_or_reject_skill(test_results)

Extends:
  • skill_registry with auto-generated tier
```

---

## 10. Simulation Cycle in Detail

### 10.1 Full Nightly Cycle (All Phases Combined)

```
02:00 UTC — Cycle starts

Phase 1: Metrics Collection (5 min)
  ├── RL experience stats
  ├── Platform performance
  ├── Trust profiles
  ├── Goal/commitment health
  ├── World state health
  └── Candidate pipeline status

Phase 2: Self-Simulation (30-60 min)
  ├── Select 3-5 personas for tonight
  ├── Generate 5-10 scenarios per persona
  ├── Execute each scenario through agent router
  │   (same path as real users — CLI + MCP tools)
  ├── Score every response (auto quality scorer)
  ├── Classify failures on low-scoring responses
  └── Detect and log skill gaps

Phase 3: Learning Pipeline (10 min)
  ├── Generate candidates from simulation + real data
  ├── Evaluate proposed candidates offline
  ├── Auto-reject regressions
  ├── Start rollouts for passing candidates
  ├── Check running rollouts for completion/rollback
  └── Auto-promote successful rollouts

Phase 4: Proactive Actions (5 min)
  ├── Scan for stalled goals
  ├── Check overdue commitments
  ├── Generate nudges and briefings
  └── Queue for morning delivery

Phase 5: Self-Diagnosis (5 min)
  ├── Aggregate simulation failures
  ├── Update skill gap registry
  ├── Generate improvement proposals
  └── Apply human feedback from previous cycle

Phase 6: Morning Report (2 min)
  ├── Compile structured summary
  ├── Persist as notification
  ├── Send via WhatsApp to admin
  └── Log as learning_system RL experience

~06:00 UTC — Cycle complete
Sleep until 02:00 UTC next day → continue_as_new
```

### 10.2 Example Morning Report (Full Version)

```
┌─────────────────────────────────────────────────────────────────────┐
│ Morning Learning Report — 2026-03-26                               │
│                                                                     │
│ ═══ WHAT IMPROVED ═══                                              │
│ • Codex routing for code tasks: +18% reward (promoted yesterday)   │
│ • Memory recall depth increased: +8% quality on sales queries      │
│                                                                     │
│ ═══ ACTIVE EXPERIMENTS ═══                                         │
│ • Coalition planner+verifier for code: day 3/7, +12% so far       │
│                                                                     │
│ ═══ TONIGHT'S SIMULATION ═══                                       │
│ Personas tested: Vet Clinic Manager, PE Analyst, Sales Rep         │
│ Scenarios run: 24                                                   │
│ Average score: 72/100                                              │
│ Failures (< 60): 4                                                  │
│   • Vet: "Book appointment for Rex" → tool_not_found               │
│   • Vet: "Check vaccination schedule" → no_memory                  │
│   • PE: "Calculate IRR on deal" → bad_reasoning                    │
│   • Sales: "Draft NDA" → safety_blocked (correctly)                │
│                                                                     │
│ ═══ SKILL GAPS DETECTED ═══                                        │
│ • No appointment booking tool (veterinary, frequency: 3)           │
│ • No financial modeling capability (PE, frequency: 2)              │
│ • Veterinary terminology not in knowledge graph                    │
│                                                                     │
│ ═══ PROACTIVE ACTIONS QUEUED ═══                                   │
│ • Nudge: "Proposal to Acme is 3 days overdue"                     │
│ • Briefing: "Meeting with investor at 2pm — context loaded"        │
│ • Alert: "Competitor launched new pricing page"                    │
│                                                                     │
│ ═══ HEALTH ═══                                                     │
│ • RL: 145 experiences, avg=0.63                                    │
│ • Trust: luna=0.52, learning_system=0.61                           │
│ • Explore/exploit: 10% explore, 85% RL, 5% rollout                │
│ • Goals: 3 active, 1 stalled, 0 overdue commitments               │
│ • World state: 48 assertions, 2 disputed, 5 expiring              │
│ • Cost: $2.14 today (Claude $1.20, Codex $0.94)                   │
│                                                                     │
│ ═══ ACTION REQUIRED ═══                                             │
│ • Review proposed appointment booking skill                        │
│ • 2 disputed world state assertions need resolution                │
│ • Respond to yesterday's report if you have feedback               │
└─────────────────────────────────────────────────────────────────────┘
```

---

## 11. Safety Constraints for Self-Simulation

```
The simulation engine operates within strict boundaries:

1. SIMULATED ACTIONS NEVER HIT EXTERNAL SYSTEMS
   • No real emails sent during simulation
   • No real calendar events created
   • No real Jira tickets filed
   • All MCP tool calls tagged as simulation=true
   • External-write tools return mock success

2. SIMULATION BUDGET
   • Max 50 scenarios per cycle
   • Max 30 minutes total CLI time
   • Max $5 per cycle in API costs
   • Separate simulation RL experiences (tagged, not mixed with real)

3. SKILL AUTO-CREATION GUARDRAILS
   • Auto-created skills are "draft" tier (not executable)
   • Require human approval before promotion to "community"
   • Cannot create skills that execute shell commands
   • Cannot modify safety policies or trust thresholds

4. PROACTIVE ACTION LIMITS
   • Max 5 proactive messages per day per channel
   • High priority only for truly urgent items
   • User can mute any action type
   • Quiet hours: no messages between 22:00-07:00 local time
```

---

## 12. Success Criteria

```
The unsupervised learning system is successful when:

✓ Platform quality improves measurably week-over-week
  without any human intervention beyond morning reviews

✓ Skill gaps are detected and reported before real users hit them

✓ Proactive actions save the user time (measured by engagement)

✓ Simulation scores trend upward across all industry verticals

✓ Morning reports are actionable and concise (<2 min to read)

✓ Human feedback is incorporated within one cycle

✓ Cost per quality point decreases over time

✓ The platform can onboard a new industry vertical by:
  1. Adding a persona definition
  2. Running 3-5 simulation cycles
  3. Auto-detecting and filling skill/knowledge gaps
  4. Achieving >70/100 average simulation score

✓ The learning system's own trust score exceeds 0.7
  (supervised_execution tier) within 30 days
```

---

## 13. Risks and Mitigations

```
Risk                              Mitigation
────────────────────────────────  ──────────────────────────────────
Simulation doesn't reflect real   Rotate personas, vary scenarios,
user behavior                     validate against real traffic patterns

Self-reinforcing bias from        Simulation uses the same auto-scorer
simulation scoring                and provider council as real traffic

Cost spiraling from nightly       Hard budget caps per cycle, track
simulations                       cost trends in morning report

Proactive messages annoying       Quiet hours, frequency caps, easy
users                             mute per action type

Skill auto-creation producing     Draft tier requires human approval,
bad skills                        test against scenarios before promote

Simulation polluting RL data      Tag all simulation experiences
                                  separately, exclude from real routing
                                  decisions

Docker disk filling from          Threshold-based cleanup, image reuse,
nightly rebuilds                  build cache management
```
