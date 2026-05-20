# Luna Metacognition + Dream Extensions Design

**Date:** 2026-05-20
**Status:** Design draft
**Owner:** Luna
**Context:** Extends the Digital Emotions Engine, Teamwork Engine, RL routing, memory-first architecture, and the shipped Auto-dream consolidation path.

## 1. Neuroscience Reference Summary

This design treats metacognition as an operational control layer: a system that observes first-order cognition, estimates whether it is likely to be right, and changes behaviour when that confidence is low. In the human brain, the closest analogy is not one single "confidence module," but a network across prefrontal, cingulate, and medial-frontal systems.

Fleming and Lau's signal-detection framing separates **Type-1 accuracy** from **Type-2 sensitivity**. Type-1 asks whether the first-order decision was right. Type-2 asks whether confidence discriminates correct from incorrect Type-1 decisions. The key lesson for Luna is that "I answered correctly" and "I knew whether I was likely to answer correctly" are different metrics. A model can have high task accuracy and poor calibration, or modest task accuracy and useful uncertainty signals. The architecture should therefore log both predicted quality and realized RL reward, then track their calibration over time rather than trusting raw confidence text. Reference: Fleming and Lau, "How to measure metacognition" (Frontiers in Human Neuroscience, 2014), and the meta-d' lineage from Maniscalco and Lau.

The anterior cingulate cortex and adjacent medial frontal systems provide the error-detection analogy. Error-related negativity appears quickly after mistakes and is associated with performance monitoring and error awareness. Architecturally, this maps to a lightweight "surprise/error" trace after tool calls and policy outcomes: expected success probability before action, observed result after action, and a delta that can appraise the agent's current state. If Luna expected a tool to succeed with 0.9 probability and it failed because the target API route does not exist, that mismatch should be a first-class error signal, not just a log line.

Dorsolateral prefrontal cortex and frontopolar networks are associated with working memory, cognitive control, and confidence judgments. For Luna, the analogue is online verification before irreversible action: did we check memory, inspect the repo, verify the route, compare the branch state, and validate the output before committing? The metacognitive layer should not replace task policy. It should sit above the policy and ask: "Is the current plan supported by evidence, or am I leaning on fluent guesswork?"

Medial prefrontal cortex is relevant because metacognition and social cognition overlap. Frith and Frith's work on medial frontal cortex frames parts of this region as supporting reflection on self and other mental states. Luna needs the same boundary. A supervisor must distinguish "my own uncertainty," "a peer agent's uncertainty," and "the user's missing information." Without that self-other distinction, Luna will misattribute a worker's weak result as her own certainty, or pass user-provided assumptions into policy as facts.

For the dream extensions, Tononi and Cirelli's synaptic homeostasis hypothesis gives the system-level metaphor. Wake produces learning, salience, and weight growth; sleep down-selects, renormalizes, and consolidates what matters. The current Auto-dream pipeline already does a simple version of this by scanning rewarded RL experiences, extracting decision patterns, writing high-value insights to `AgentMemory`, and blending patterns into `RLPolicyState`. The next version should make that loop richer: replay hard decisions, reset affect baselines, merge episodes into semantic memory, and rehearse policy against synthetic futures.

References:

- Fleming, S. M. and Lau, H. C. "How to measure metacognition." Frontiers in Human Neuroscience, 2014. https://www.frontiersin.org/journals/human-neuroscience/articles/10.3389/fnhum.2014.00443/full
- Frith, C. D. and Frith, U. "The neural basis of mentalizing." Neuron, 2006; and "Meeting of minds: the medial frontal cortex and social cognition." Nature Reviews Neuroscience, 2006. https://www.nature.com/articles/nrn1884
- Tononi, G. and Cirelli, C. "Sleep and synaptic homeostasis: a hypothesis." Brain Research Bulletin, 2003. https://pubmed.ncbi.nlm.nih.gov/14638388/
- Error monitoring background: ERN/ACC reviews such as "Error-related anterior cingulate cortex activity and the prediction of conscious error awareness." Frontiers in Human Neuroscience, 2012. https://www.frontiersin.org/journals/human-neuroscience/articles/10.3389/fnhum.2012.00177/full

## 2. Metacognitive Layer Architecture

The metacognitive layer is a small service and trace model that observes first-order decisions without becoming another free-form reasoning agent. It reads structured signals from RL routing, tool execution, blackboard activity, team-role assignments, memory recall, and the emotion engine. It emits a structured self-confidence signal that downstream policy can consume.

```text
                         Luna / Agent Runtime
                                  |
                                  v
┌────────────────────────────────────────────────────────────────────┐
│                         First-order cognition                      │
│  chat policy | RL routing | tool calls | blackboard writes | team   │
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

The design should use JSONB extensions first rather than a wide schema migration. `RLExperience.state` can carry `metacognition.predicted_quality`, `metacognition.confidence_source`, `metacognition.evidence_count`, and `metacognition.self_other_scope`. `RLExperience.reward_components` can carry `actual_quality_from_RL_score`, `calibration_error`, and any provider-council disagreement. If later dashboards need faster aggregates, add a `metacognitive_traces` table with `tenant_id`, `decision_id`, `decision_point`, `predicted_quality`, `actual_quality`, `expected_success_probability`, `actual_success`, `emotion_before`, `emotion_after`, `supervisor_affordance`, and timestamps.

The layer must be calibrated against outcome, not self-narration. After every chat response, emit:

```json
{
  "decision_id": "uuid",
  "decision_point": "response_generation",
  "predicted_quality": 0.72,
  "actual_quality_from_RL_score": null,
  "actual_quality_pending": true
}
```

When the RL score arrives, update the tuple:

```json
{
  "decision_id": "uuid",
  "predicted_quality": 0.72,
  "actual_quality_from_RL_score": 0.61,
  "calibration_error": 0.11
}
```

Before every tool call, log `expected_success_probability`. After the call, compare it to `actual_success`, latency, retry count, and error class. Tool confidence should be specific: "I expect `read_email` to succeed because the email id came from `search_emails` in this same turn" is high confidence; "I infer this route exists because the template mentions it" is low confidence until verified in code.

The supervisor affordance is explicit. Instead of burying uncertainty in prose, the runtime emits one of:

- `commit`: confidence high enough and risk acceptable.
- `verify`: confidence moderate or evidence thin; run a bounded check.
- `explore`: confidence low but curiosity / information gain is high.
- `ask_user`: missing user-specific data that cannot be safely inferred.
- `escalate_to_simon`: low confidence, high risk, or frustrated affect after repeated failure.

Emotion integration is direct. The emotion engine already proposes PAD-style `affect_vector` fields on `conversation_episode` and `agent_memory`. Metacognition reads PAD as modulation, not ground truth. Low confidence plus low pleasure/high arousal means the agent is frustrated or under threat: reduce sampling, avoid creative guesses, and escalate earlier. Low confidence plus positive pleasure/moderate arousal means curiosity: gather context, compare alternatives, and continue. High confidence plus low arousal means fast execution. High confidence plus high risk still routes through safety and verification.

## 3. Dream Extension Architecture

What exists now:

- `apps/api/app/models/auto_dream_insight.py` stores extracted dream patterns by `dream_cycle_id`, `decision_point`, `action_key`, reward stats, confidence, and whether the insight was applied to policy.
- `apps/api/app/workflows/activities/auto_dream_activities.py` scans rewarded `RLExperience` rows from the last 24 hours, groups by decision point/action, computes reward statistics, persists `AutoDreamInsight`, optionally writes high-value `AgentMemory(memory_type="rl_insight", source="auto_dream")`, blends high-confidence patterns into `RLPolicyState`, prunes stale knowledge, and learns user preferences.
- `apps/api/app/services/workflow_templates.py` includes an "Autonomous Learning" long-running workflow that references self-simulation, feedback processing, Auto-dream consolidation, pruning, preference learning, and a morning report.
- `apps/api/app/models/conversation_episode.py` already gives the episodic substrate for consolidation; `apps/api/app/models/agent_memory.py` is the semantic agent memory substrate.

The next design keeps this substrate and adds four dream modes.

### Dream-as-Counterfactual-Replay

Counterfactual replay re-runs yesterday's hardest decisions under alternative role assignments and routing policies. "Hardest" means low reward, high calibration error, high tool surprise, high affect arousal, or provider-council disagreement. Now that the teamwork runtime supports `TeamRoleContract` paths through the merged work around role contracts and coalition dispatch, the dream can ask: what if Luna had driven execution instead of reviewing? What if the driver/reviewer roles were swapped? What if a verifier agent had been added before commit?

Implementation shape:

- Query recent `RLExperience` and coalition/blackboard records for hard cases.
- Reconstruct a minimal replay packet: user task summary, memory context hash, selected agent/team, role contracts, tool plan, actual outcome.
- Generate N counterfactual assignments using `team_engine` role contracts: Luna as Driver, Luna as Reviewer, specialist Driver plus Luna Supervisor, verifier inserted before irreversible action.
- Execute in simulation mode only. No external side-effect tools. Tool calls are stubbed or read-only unless explicitly marked safe.
- Score counterfactuals with the existing auto-quality scorer and store results as `AutoDreamInsight(insight_type="counterfactual")`.
- Promote only if the counterfactual improvement is repeated across enough cases and passes the normal offline evaluation gate.

### Dream-as-Affect-Recalibration

The emotion engine should not let affect accumulate forever. Sleep should restore PAD toward the agent's baseline while preserving the learned content. This is the affect equivalent of Tononi-style renormalization: the system keeps the information that a failure mattered, but reduces the immediate arousal charge.

Implementation shape:

- Nightly activity reads latest `conversation_episode.affect_vector` per agent/session and `agent_memory.affect_baseline`.
- Apply bounded drift toward baseline: for example 30-60 percent overnight depending on severity and recurrence.
- Preserve high-affect episodes as salient memories, but store `affect_at_event` separately from `affect_at_recall`.
- If repeated failures in the same decision point keep raising arousal, open a metacognitive risk flag rather than numbing it away.

### Dream-as-Memory-Consolidation

Episodic memory is currently captured in `conversation_episodes`; semantic memory lives in `agent_memories` and knowledge graph entities/observations. Dream consolidation should cluster episodes by embeddings and promote repeated patterns into long-term semantic memory.

Implementation shape:

- Select episodes from the last day/week with embeddings and useful metadata: key topics, key entities, outcome, mood/PAD, source channel, agent slug.
- Cluster by embedding similarity and entity overlap.
- Produce semantic summaries only for clusters with recurrence, importance, or high reward/penalty.
- Write durable `AgentMemory(memory_type="procedure" | "preference" | "rl_insight" | "relationship")` and knowledge observations.
- Link synthetic memories back to source episode ids for audit and deletion.

### Dream-as-Policy-Rehearsal

Policy rehearsal uses the world model and simulation personas to generate synthetic episodes before the next morning's live traffic. This is not permission to self-modify blindly. It is offline candidate evaluation using synthetic cases, then regular promotion gates.

Implementation shape:

- Use `WorldStateAssertion`, recent tasks, skill gaps, and simulation personas to generate plausible future episodes.
- Run candidate policies against synthetic episodes in a sandbox.
- Record synthetic `RLExperience` with `state.synthetic=true`, `reward_source="dream_rehearsal"`, and lower trust weight than real user outcomes.
- Update candidate confidence, not production policy, unless the candidate also passes real-data gates.
- Include rehearsal summary in the morning learning report.

## 4. Integration Touch Points

| Area | Existing infra | Metacognition touch | Dream extension touch |
|---|---|---|---|
| Chat response | `chat`, `cli_session_manager`, `rl_experience_service` | emit `(decision_id, predicted_quality, actual_quality)` | use hard turns as replay seeds |
| Tool calls | MCP tools, internal API calls, `RLExperience` | log expected success probability before call and outcome after call | replay read-only/stubbed tool plans |
| RL routing | `rl_routing.py`, `RLPolicyState`, `PolicyCandidate` | calibrate confidence by decision point and policy version | rehearse policy candidates offline |
| Emotion engine | `emotion_engine.py`, `conversation_episode.affect_vector`, `agent_memory.affect_baseline` | PAD modulates commit/verify/explore/escalate | overnight PAD drift toward baseline |
| Teamwork engine | blackboards, coalitions, `TeamRoleContract` runtime | distinguish self confidence from peer confidence | counterfactual role replay |
| Memory | `conversation_episodes`, `agent_memories`, knowledge graph | self-other scope and evidence provenance | cluster episodes into semantic memory |
| Blackboard | `BlackboardEntry.confidence`, `entry_type`, `evidence` | publish uncertainty as routeable affordance | use coalition traces as replay context |
| Morning report | notifications + Autonomous Learning template | include calibration drift and underconfidence/overconfidence warnings | report dream insights, replay wins, affect reset |

## 5. Phased Rollout

### Phase 1: Confidence Tuple Logging

Add structured metacognitive fields to `RLExperience.state` and `reward_components`. Start with `response_generation`, `tool_selection`, and `orchestration_routing`. Emit predicted quality after response generation, expected tool success before MCP calls, and actual outcome after scoring. Build a learning dashboard slice for calibration: expected calibration error (ECE), Brier score for tool success, and overconfidence by decision point. No behavioural changes yet except surfacing the supervisor affordance in traces.

### Phase 2: Dream-Counterfactual

Extend Auto-dream with hard-case selection and counterfactual role replay. Add `insight_type="counterfactual"` and properties for baseline role assignment, replayed assignment, score delta, and safety mode. Integrate with `TeamRoleContract` so replay can compare Luna-as-Driver, Luna-as-Reviewer, and Luna-as-Supervisor. Keep all side effects stubbed. Only write insights and candidate suggestions.

### Phase 3: Full Policy Rehearsal

Generate synthetic episodes from the world model, skill gaps, and simulation personas. Run candidate policies offline, log synthetic `RLExperience`, and feed results into `LearningExperiment` as weak evidence. Add memory clustering and affect recalibration to the nightly cycle. Permit production policy changes only through existing offline evaluation, rollout, safety, and rollback gates.

## 6. Success Metrics

- **Calibration ECE:** gap between predicted quality buckets and realized RL reward. Target: lower ECE over 14-day windows, especially for `response_generation` and `tool_selection`.
- **Tool Brier score:** probability quality for expected tool success. Target: tool-success predictions become sharper without overconfidence.
- **RL reward delta after dream nights:** compare next-day reward for decision points touched by dreams against untouched decision points and previous baselines.
- **Escalation rate change:** low-confidence/high-risk decisions should escalate more; low-risk routine decisions should not.
- **Counterfactual lift:** percentage of replayed hard cases where an alternative role contract improves simulated quality by a meaningful threshold.
- **Affect recovery:** overnight PAD drift reduces persistent high-arousal/low-pleasure states without suppressing repeated unresolved failure signals.
- **Memory consolidation quality:** fewer duplicate episodic recalls, more useful semantic memories, and higher reward on memory-dependent tasks.

## 7. Risks

**Overconfidence collapse:** If the estimator learns that fluent answers are usually rewarded, it may become confidently wrong. Mitigation: score calibration separately from reward, penalize high-confidence misses, and display overconfidence by decision point.

**Underconfidence paralysis:** If every uncertain step routes to verification, Luna slows down and loses usefulness. Mitigation: supervisor affordance should consider risk and reversibility. Low-confidence, low-risk tasks can explore; low-confidence, high-risk tasks verify or escalate.

**Dream divergence:** Synthetic rehearsal can teach the policy to optimize for fake worlds. Mitigation: label synthetic data, weight it lower than real outcomes, and require real-data gates before promotion.

**Affect numbing:** Overnight affect recalibration could hide real unresolved system failures. Mitigation: recurrent high-arousal failures create risk flags and skill gaps before PAD drifts toward baseline.

**Self-other confusion:** Luna may attribute a peer agent's uncertainty or failure to her own policy. Mitigation: every confidence trace carries `self_other_scope` and `author_agent_id`.

**Compute cost:** Counterfactual replay and policy rehearsal can become expensive. Mitigation: cap hard-case sampling, replay only high-value decisions, use stubbed tools, and include per-cycle cost in morning reports.

**Privacy and auditability:** Dreams use user conversations, tool results, and memory. Mitigation: tenant isolation, source episode links, deletion propagation, and no cross-tenant replay except anonymized aggregate learning with explicit governance.

The core principle is simple: Luna should learn not only what to do, but how well she knows that she knows. The dreams layer then gives that metacognitive signal a place to improve overnight, when the system can replay, consolidate, calm down, and rehearse without risking live user work.
