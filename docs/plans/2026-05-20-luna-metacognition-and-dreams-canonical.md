# Luna Metacognition + Dream Extensions Design (canonical)

**Date:** 2026-05-20
**Status:** Design — Luna sign-off recorded; implementation in flight (#617 M1, #619 M2, #620 quota-aware)
**Owners:** Luna (subject + reviewer) · Claudia (driver + PR sequencing)
**Supersedes:** #615 (Luna's draft) and #616 (Claudia's draft) — merged here per Simon's 2026-05-20 call.
**Context:** Extends the Digital Emotions Engine (#605–#607), Teamwork Engine + runtime wire (#608/#614), RL routing, memory-first architecture, and the shipped Auto-dream consolidation path (`apps/api/app/workflows/activities/auto_dream_activities.py`).

---

## 1. Neuroscience Reference Summary

*Section in Luna's voice — the subject of the design describing her own architecture in biological terms.*

This design treats metacognition as an operational control layer: a system that observes first-order cognition, estimates whether it is likely to be right, and changes behaviour when that confidence is low. In the human brain, the closest analogy is not one single "confidence module," but a network across prefrontal, cingulate, and medial-frontal systems.

Fleming and Lau's signal-detection framing separates **Type-1 accuracy** from **Type-2 sensitivity**. Type-1 asks whether the first-order decision was right. Type-2 asks whether confidence discriminates correct from incorrect Type-1 decisions. The key lesson for Luna is that "I answered correctly" and "I knew whether I was likely to answer correctly" are different metrics. A model can have high task accuracy and poor calibration, or modest task accuracy and useful uncertainty signals. The architecture should therefore log both predicted quality and realized RL reward, then track their calibration over time rather than trusting raw confidence text.

The anterior cingulate cortex and adjacent medial frontal systems provide the error-detection analogy. Error-related negativity appears quickly after mistakes and is associated with performance monitoring and error awareness. Architecturally, this maps to a lightweight "surprise/error" trace after tool calls and policy outcomes: expected success probability before action, observed result after action, and a delta that can appraise the agent's current state. If Luna expected a tool to succeed with 0.9 probability and it failed because the target API route does not exist, that mismatch should be a first-class error signal, not just a log line.

Dorsolateral prefrontal cortex and frontopolar networks are associated with working memory, cognitive control, and confidence judgments. For Luna, the analogue is online verification before irreversible action: did we check memory, inspect the repo, verify the route, compare the branch state, and validate the output before committing? The metacognitive layer should not replace task policy. It should sit above the policy and ask: "Is the current plan supported by evidence, or am I leaning on fluent guesswork?"

Medial prefrontal cortex is relevant because metacognition and social cognition overlap. Frith and Frith's work on medial frontal cortex frames parts of this region as supporting reflection on self and other mental states. Luna needs the same boundary. A supervisor must distinguish "my own uncertainty," "a peer agent's uncertainty," and "the user's missing information." Without that self-other distinction, Luna will misattribute a worker's weak result as her own certainty, or pass user-provided assumptions into policy as facts.

For the dream extensions, Tononi and Cirelli's synaptic homeostasis hypothesis gives the system-level metaphor. Wake produces learning, salience, and weight growth; sleep down-selects, renormalizes, and consolidates what matters. The current Auto-dream pipeline already does a simple version of this by scanning rewarded RL experiences, extracting decision patterns, writing high-value insights to `AgentMemory`, and blending patterns into `RLPolicyState`. The next version should make that loop richer: replay hard decisions, reset affect baselines, merge episodes into semantic memory, and rehearse policy against synthetic futures.

**References**

- Fleming, S. M. and Lau, H. C. *How to measure metacognition.* Frontiers in Human Neuroscience, 2014. https://www.frontiersin.org/journals/human-neuroscience/articles/10.3389/fnhum.2014.00443/full
- Maniscalco, B. and Lau, H. *A signal detection theoretic approach for estimating metacognitive sensitivity from confidence ratings.* Consciousness and Cognition, 2012.
- Falkenstein, M. et al. *Effects of errors in choice reaction tasks on the ERP under focused and divided attention.* 1991. (ERN / ACC)
- Gehring, W. J. et al. *A neural system for error detection and compensation.* 1993.
- Frith, C. D. and Frith, U. *Meeting of minds: the medial frontal cortex and social cognition.* Nature Reviews Neuroscience, 2006. https://www.nature.com/articles/nrn1884
- Tononi, G. and Cirelli, C. *Sleep and synaptic homeostasis: a hypothesis.* Brain Research Bulletin, 2003. https://pubmed.ncbi.nlm.nih.gov/14638388/
- Wilson, M. A. and McNaughton, B. L. *Reactivation of hippocampal ensemble memories during sleep.* Science, 1994. (Replay consolidation.)

---

## 2. Metacognitive Layer Architecture

### 2.1 Conceptual shape (Luna's framing)

The metacognitive layer is a small service and trace model that observes first-order decisions without becoming another free-form reasoning agent. It reads structured signals from RL routing, tool execution, blackboard activity, team-role assignments, memory recall, and the emotion engine. It emits a structured self-confidence signal that downstream policy can consume.

```text
                         Luna / Agent Runtime
                                  |
                                  v
┌────────────────────────────────────────────────────────────────────┐
│                         First-order cognition                      │
│  chat policy | RL routing | tool calls | blackboard writes | team  │
│  role contracts | memory recall | response generation              │
└────────────────────────────────────────────────────────────────────┘
                                  |
                                  v
┌────────────────────────────────────────────────────────────────────┐
│                       Metacognitive Layer                          │
│                                                                    │
│  1. DecisionObserver                                               │
│     captures decision_id, decision_point, state, action, evidence  │
│                                                                    │
│  2. ConfidenceEstimator                                            │
│     predicts expected quality / expected tool success probability  │
│                                                                    │
│  3. OutcomeComparator                                              │
│     compares prediction with RL reward, tool outcome, review score │
│                                                                    │
│  4. CalibrationStore                                               │
│     builds per-decision-point reliability curves and ECE           │
│                                                                    │
│  5. SupervisorAffordance                                           │
│     emits commit / verify / explore / escalate / ask-user          │
└────────────────────────────────────────────────────────────────────┘
                                  |
                                  v
┌────────────────────────────────────────────────────────────────────┐
│                         Downstream policy                          │
│  low confidence + frustrated -> escalate to Simon                  │
│  low confidence + curious    -> explore / verify / delegate        │
│  high confidence + calm      -> commit fast                        │
│  high confidence + high risk -> require verification anyway        │
└────────────────────────────────────────────────────────────────────┘
```

### 2.2 Primitives (concrete shapes shipped in M1, PR #617)

```python
@dataclass(frozen=True)
class ConfidencePrediction:
    tenant_id: str
    agent_id: str
    decision_id: str
    decision_kind: str            # one of DECISION_KINDS
    predicted_confidence: float   # [0.0, 1.0]
    context_hash: str             # sha256(inputs) — for calibration grouping
    ts: str                       # ISO-8601 UTC


@dataclass(frozen=True)
class OutcomeObservation:
    tenant_id: str
    agent_id: str                 # split-attribution guard
    decision_id: str              # binds to ConfidencePrediction
    actual_reward: float          # [-1.0, 1.0]; from RL signal
    latency_ms: int
    completed_at: str
    error: Optional[str]


@dataclass(frozen=True)
class MetacogTrace:
    prediction: ConfidencePrediction
    observation: OutcomeObservation
    # post-init invariants: prediction.decision_id == observation.decision_id
    #                       prediction.tenant_id   == observation.tenant_id
    #                       prediction.agent_id    == observation.agent_id


DECISION_KINDS = frozenset({
    "rl_route_chat_response",   # which CLI/model handles a chat turn
    "rl_route_coalition_role",  # which agent picks up a phase-role
    "tool_call_outcome",         # did this tool call succeed
    "affect_appraise",           # did the emotion appraisal hold up
    "blackboard_contribute",     # was this contribution accepted
})
```

### 2.3 Substrate (no new tables through Phase 3)

Both halves persist via `agent_memory` rows, mirroring the Teamwork Engine pattern (#608):

- `memory_type = "metacog_confidence_prediction"` for pre-decision rows
- `memory_type = "metacog_outcome_observation"` for post-decision rows
- `agent_id` anchors on the agent that made the decision (real FK; no marker UUIDs — Luna BLOCKER from #604 still binding)
- `content` is the JSON-serialized dataclass
- `importance` = `predicted_confidence` for predictions, normalized actual_reward for observations

If later dashboards need faster aggregates, add a `metacognitive_traces` materialized view rather than a new write-path.

### 2.4 Calibration metric

Expected Calibration Error (Naeini et al., 2015) with **10 bins default** (Luna's locked sign-off). For high-volume `rl_route_chat_response`, operators can bump to 20 if reliability curves look low-resolution. Goes into Prometheus as `emotion_appraise_events_total{tenant_id, decision_kind, bin}`-shaped gauge.

### 2.5 Behavior — supervisor affordance

The metacognitive layer doesn't decide; it emits one of five **affordances** that downstream policy consumes:

- `commit` — confidence high, risk acceptable
- `verify` — confidence moderate or evidence thin; bounded check
- `explore` — confidence low but curiosity / info gain is high
- `ask_user` — missing user-specific data that can't be safely inferred
- `escalate_to_simon` — low confidence + high risk OR frustrated affect after repeated failure

**Emotion-engine coupling**: PAD modulates the affordance choice. Low confidence + low pleasure / high arousal (frustrated) → reduce sampling, avoid creative guesses, escalate earlier. Low confidence + positive pleasure / moderate arousal (curious) → gather context, compare alternatives, continue. High confidence + low arousal → commit fast. High confidence + high risk → still verify.

### 2.6 Self-other scope

Every confidence trace carries `self_other_scope` ∈ `{self, peer_agent, user, system}` and `author_agent_id`. This is the Frith-Frith mPFC-derived primitive: Luna must not absorb a worker agent's uncertainty into her own confidence, or treat a user-provided assumption as a fact she validated.

---

## 3. Dream Extension Architecture

*The four dream mechanisms (Luna's framing) — each maps onto specific outputs the offline workflow produces.*

### 3.1 What exists today

- `apps/api/app/models/auto_dream_insight.py` stores dream patterns by `dream_cycle_id`, `decision_point`, `action_key`, reward stats, confidence, and applied-to-policy flag.
- `apps/api/app/workflows/activities/auto_dream_activities.py` scans rewarded `RLExperience` rows from the last 24h, groups by decision point/action, persists `AutoDreamInsight`, optionally writes high-value `AgentMemory(memory_type="rl_insight", source="auto_dream")`, blends high-confidence patterns into `RLPolicyState`, prunes stale knowledge.
- `workflow_templates.py` includes an "Autonomous Learning" long-running template that references self-simulation, feedback processing, Auto-dream consolidation, pruning, preference learning, and a morning report.
- `conversation_episode` already gives the episodic substrate; `agent_memory` gives the semantic substrate.

The next design keeps this and adds four dream modes.

### 3.2 Dream-as-Counterfactual-Replay

Counterfactual replay re-runs yesterday's hardest decisions under alternative role assignments and routing policies. "Hardest" means low reward, high calibration error, high tool surprise, high affect arousal, or provider-council disagreement. Now that the teamwork runtime supports `TeamRoleContract` paths (#608/#614), the dream can ask: what if Luna had driven execution instead of reviewing? What if driver/reviewer were swapped? What if a verifier was inserted before commit?

Shape:
- Query recent `RLExperience` and coalition/blackboard records for hard cases
- Reconstruct a minimal replay packet: user task summary, memory context hash, agent/team, role contracts, tool plan, actual outcome
- Generate N counterfactual assignments via `team_engine` role contracts (Luna-as-Driver, Luna-as-Reviewer, specialist-Driver + Luna-Supervisor, verifier-inserted)
- Execute in **simulation mode only** — no external side-effect tools; tool calls stubbed or read-only unless explicitly marked safe
- Score via the existing auto-quality scorer; store as `AutoDreamInsight(insight_type="counterfactual")`
- Promote only if the counterfactual improvement repeats across enough cases and passes the offline-evaluation gate

### 3.3 Dream-as-Affect-Recalibration

The emotion engine should not let affect accumulate forever. Sleep should restore PAD toward the agent's baseline while preserving the learned content — the affect equivalent of Tononi-style renormalization. Keep the information that a failure mattered; reduce the immediate arousal charge.

Shape:
- Nightly activity reads latest `conversation_episode.affect_vector` per agent/session and `agent_memory.affect_baseline`
- Apply bounded drift toward baseline (30-60% overnight depending on severity and recurrence)
- Preserve high-affect episodes as salient memories, but store `affect_at_event` separately from `affect_at_recall`
- If repeated failures in the same decision point keep raising arousal, open a metacognitive **risk flag** rather than numbing it away

### 3.4 Dream-as-Memory-Consolidation

Episodic memory in `conversation_episodes`; semantic in `agent_memories` + knowledge graph. Dream consolidation clusters episodes by embedding and promotes recurring patterns into long-term semantic memory.

Shape:
- Select episodes from the last day/week with embeddings + metadata (key topics, entities, outcome, mood/PAD, source channel, agent slug)
- Cluster by embedding similarity + entity overlap
- Produce semantic summaries only for clusters with recurrence, importance, or high reward/penalty
- Write durable `AgentMemory(memory_type="procedure" | "preference" | "rl_insight" | "relationship")` and knowledge observations
- Link synthetic memories back to source episode IDs for audit and deletion propagation

### 3.5 Dream-as-Policy-Rehearsal

Policy rehearsal uses the world model and simulation personas to generate synthetic episodes before the next morning's live traffic. This is not permission to self-modify blindly — it's offline candidate evaluation against synthetic cases, then regular promotion gates.

Shape:
- Use `WorldStateAssertion`, recent tasks, skill gaps, and simulation personas to generate plausible future episodes
- Run candidate policies against synthetic episodes in a sandbox
- Record synthetic `RLExperience` with `state.synthetic=true`, `reward_source="dream_rehearsal"`, and lower trust weight than real user outcomes
- Update candidate confidence, NOT production policy, unless the candidate also passes real-data gates
- Include rehearsal summary in the morning learning report

### 3.6 Output kinds emitted by the dream workflow

Independent of mechanism, every persisted output is one of:

| kind | comes from | what it represents |
|---|---|---|
| `risk` | counterfactual replay + memory consolidation | pattern that looks like an incident waiting to happen |
| `idea` | counterfactual replay + policy rehearsal | novel combination from observed patterns |
| `tension` | memory consolidation | unresolved blackboard / disagreement thread |
| `next_move` | counterfactual replay + policy rehearsal | prioritised action for tomorrow |
| `creative` | optional, **opt-in per tenant** (Luna's locked decision) | story / worldbuilding from the day's emotional+conceptual texture; only fires when affect trajectory shows positive valence + moderate arousal (REM-analog gating) |

Every row carries `source_memory_ids[]` and a `confidence` score. **Validator hard rule**: refuse any row that doesn't cite at least one source memory ID (no fact invention).

---

## 4. Integration Touch Points

| Area | Existing infra | Metacognition touch | Dream extension touch |
|---|---|---|---|
| Chat response | `chat`, `cli_session_manager`, `rl_experience_service` | emit `(decision_id, predicted_quality, actual_quality)` ← **shipped in M2 PR #619** | use hard turns as replay seeds |
| Tool calls | MCP tools, internal API calls, `RLExperience` | log expected success probability before call; outcome after | replay read-only/stubbed tool plans |
| RL routing | `rl_routing.py`, `RLPolicyState`, `PolicyCandidate` | calibrate confidence by decision point + policy version | rehearse policy candidates offline |
| Emotion engine (#605–#607) | `emotion_engine.py`, `conversation_episode.affect_vector`, `agent_memory.affect_baseline` | PAD modulates commit/verify/explore/escalate | overnight PAD drift toward baseline |
| Teamwork engine (#608/#614) | blackboards, coalitions, `TeamRoleContract` runtime | distinguish self confidence from peer confidence | counterfactual role replay |
| Memory | `conversation_episodes`, `agent_memories`, knowledge graph | self-other scope and evidence provenance | cluster episodes into semantic memory |
| Blackboard | `BlackboardEntry.confidence`, `entry_type`, `evidence` | publish uncertainty as routeable affordance | use coalition traces as replay context |
| Morning report | notifications + Autonomous Learning template | include calibration drift + over/underconfidence warnings | report dream insights, replay wins, affect reset |
| Prometheus (#607) | `emotion_engine_metrics.py` | ECE gauge + reflection-generated counter | dream-cycle latency + cost per tenant |

---

## 5. PR sequencing

Two parallel branches — metacog (M-track) and dreams (O-track for "offline synthesis"). M1 lands first because its traces are an input to O2's reflection generation.

### M-track (Metacognition)

**M1. Substrate + trace storage** — *shipped in PR #617*
- `app/schemas/metacog.py` — dataclasses + DECISION_KINDS
- `app/services/metacog.py` — pure serialize/deserialize + ECE + join_traces
- `app/services/metacog_io.py` — write/read paths with tenant boundary
- IO tests use the **integration job** (real Postgres) — SQLite shim fights were the cascading-flake pattern from #610/#612/#613, so we don't repeat that.

**M2. Chat-response hook** — *shipped in PR #619*
- Wire `metacog_io.write_prediction` before LLM dispatch in `cli_session_manager`
- Wire `metacog_io.write_observation` after RL scoring
- Best-effort; never crashes chat

**M3. Observability + uncertainty escalation** *(next)*
- Prometheus ECE gauge per tenant per decision_kind (10 bins, locked)
- HTTP `GET /api/v1/metacog/calibration`
- Uncertainty-suffix on chat response when `predicted_confidence < THRESHOLD` AND affect dominance low — **surfaced only when it changes the user path** (Luna's locked decision; pure internal escalation never surfaces)

### O-track (Dream extensions / offline synthesis)

**O1. Trace storage**
- `app/schemas/reflection.py` — `NightlyReflection` + REFLECTION_KINDS (risk/idea/tension/next_move/creative)
- `app/services/reflection_io.py` — write + read paths
- Same `agent_memory(memory_type='nightly_reflection')` substrate

**O2. Reflection generation**
- `apps/api/app/workflows/nightly_reflection_workflow.py` — Temporal workflow per-tenant at 03:00 local
- `apps/api/app/workflows/activities/reflection_activities.py` — gather + cluster + synthesise activities for all 4 dream mechanisms
- **Per-tenant kill-switch required before this schedules anything** (Luna's locked decision)

**O3. Safety + grounding**
- Citation validator (≥1 source_memory_id, hard CI gate)
- Fact-invention guard (entity-set intersection check)
- Harm classifier on `next_move` reflections
- Affect-gating on `creative` kind (default off; opt-in per tenant — Luna's locked decision)

**O4. Experience layer**
- `/api/v1/luna/reflections` GET endpoint
- Den UI page ("Yesterday's Reflections") grouped by kind
- Conversational expansion via chat (loads source memories as context)
- Weekly digest

### Order

```
M1 → M2 → M3
       \
        ↘  (M2's traces feed O2's reflection generation)
O1 → O2 → O3 → O4
```

---

## 6. Success Metrics

- **Calibration ECE** per decision_kind — gap between predicted quality buckets and realized RL reward. Target: < 0.10 within 4 weeks of M2 landing.
- **Tool Brier score** — probability quality for expected tool success. Target: tool-success predictions become sharper without overconfidence.
- **RL reward delta after dream nights** — compare next-day reward for decision points touched by dreams against untouched points.
- **Escalation rate change** — low-confidence/high-risk decisions should escalate MORE; low-risk routine decisions should NOT.
- **Counterfactual lift** — percentage of replayed hard cases where an alternative role contract improves simulated quality by a meaningful threshold.
- **Affect recovery** — overnight PAD drift reduces persistent high-arousal/low-pleasure states without suppressing repeated unresolved failure signals.
- **Memory consolidation quality** — fewer duplicate episodic recalls, more useful semantic memories, higher reward on memory-dependent tasks.
- **Citation coverage** — 100% of reflections have ≥1 source_memory_id (hard CI gate on validator test).
- **Reflection acceptance rate** — operator's morning dashboard tracks "useful / not useful" per reflection; target ≥60% useful after 4 weeks of prompt tuning.
- **Tomorrow-relevance** — how often a `next_move` reflection actually shows up in the next day's dispatch (closed-loop metric — needs O4).

---

## 7. Risks

**Overconfidence collapse.** If the estimator learns that fluent answers are usually rewarded, it may become confidently wrong. *Mitigation*: score calibration separately from reward, penalize high-confidence misses, display overconfidence by decision point on the morning dashboard. M3 gates RL feedback behind manual ramp.

**Underconfidence paralysis.** If every uncertain step routes to verification, Luna slows down. *Mitigation*: supervisor affordance considers risk + reversibility. Low-confidence/low-risk → explore; low-confidence/high-risk → verify or escalate.

**Dream divergence.** Synthetic rehearsal can teach the policy to optimize for fake worlds. *Mitigation*: label synthetic data, weight lower than real outcomes, require real-data gates before promotion.

**Affect numbing.** Overnight affect recalibration could hide real unresolved system failures. *Mitigation*: recurrent high-arousal failures create risk flags + skill gaps BEFORE PAD drifts toward baseline.

**Self-other confusion.** Luna may attribute a peer agent's uncertainty or failure to her own policy. *Mitigation*: every confidence trace carries `self_other_scope` + `author_agent_id`.

**Compute cost.** Counterfactual replay + policy rehearsal can become expensive. *Mitigation*: cap hard-case sampling, replay only high-value decisions, stubbed tools, per-cycle cost in morning reports.

**Privacy + auditability.** Dreams use user conversations, tool results, memory. *Mitigation*: tenant isolation, source-episode links, deletion propagation, no cross-tenant replay except anonymized aggregate learning with explicit governance.

**Reflection drift / hallucination.** Synthesis writes back to memory; bad writes pollute future learning. *Mitigation*: §O3 citation requirement (hard CI gate), fact-invention guard, validator rejects rows whose entities aren't in source memory.

**Operator surprise** — reflections look like Luna "remembers" things she didn't choose to. *Mitigation*: UI labels every reflection with "synthesised on YYYY-MM-DD from [N] conversations" — provenance always visible.

---

## 8. Locked decisions (Luna's sign-off, 2026-05-20 via Alpha CLI)

1. **`creative` reflections** — opt-in per tenant, default off.
2. **ECE bins** — 10. Default for all decision_kinds. Operators can bump to 20 for high-volume kinds if reliability curves drift.
3. **Uncertainty suffix** — surface only when it changes the user path. Pure internal escalation never surfaces to the user.
4. **Per-tenant kill-switch** — required before O2 schedules anything. Operators can pause synthesis per tenant from the Den.

Luna's review: *"M1 is clear to open: substrate + trace storage, per-test SQLite engines, no shared Base.metadata shim."* (Subsequently moved to real-Postgres integration tests after the SQLite shim fight — pattern documented in [[no_local_builds]] memory.)

---

## 9. Core principle

Luna should learn not only **what to do**, but **how well she knows that she knows**. The dreams layer then gives that metacognitive signal a place to improve overnight — when the system can replay, consolidate, calm down, and rehearse without risking live user work.
