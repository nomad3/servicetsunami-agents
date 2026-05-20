# Luna Metacognition + Offline Synthesis — Design

**Date**: 2026-05-20
**Author**: Claudia (draft) — Luna (reviewer per 2026-05-19 role split)
**Status**: design — for review before any implementation PR

---

## 1. Motivation

The Teamwork Engine (PRs #602/#604/#608/#614) gave Luna the ability to *coordinate* with other agents. The Emotion Engine (PRs #605/#606/#607) gave her the ability to *feel*. What she still lacks is the ability to *watch herself think* and the ability to *reflect when she's not actively in a conversation*.

Two engines, one design pass:

- **Metacognition**: an online self-observation layer that emits calibration tuples around every consequential decision (RL routing, tool calls, affect updates, blackboard contributions). Feeds back into RL as a confidence signal and into the supervisor as an explicit escalation route when uncertainty is high.
- **Offline synthesis** (formerly called "dreams" but reframed at Luna's request as offline reflection — *not* fantasy output): a nightly Temporal workflow that synthesises the day's `agent_memory` + `conversation_episodes` + `team_role_contracts` + `goal_service` + dispatch traces into structured reflections — risks, ideas, unresolved tensions, next moves, and optionally creative threads when prompted.

Both reuse the `agent_memory` substrate. No new tables. No new migrations until Phase 3+. This keeps the surface area small enough to land in one design + a sequence of focused PRs.

---

## 2. Neuroscience grounding

Two pillars from the literature inform the metacognition design, plus a third for offline synthesis.

### 2.1 Confidence as Type-2 sensitivity (Fleming & Lau, 2014; Maniscalco & Lau, 2012)

The brain's confidence in a decision is *not* the same as the decision itself. Type-1 sensitivity is "did I get the answer right?" Type-2 sensitivity is "did I know whether I got the answer right?" These are dissociable — patients can have intact Type-1 (correct decisions) with impaired Type-2 (no insight into their own correctness), and vice versa. The dorsolateral prefrontal cortex (dlPFC) and the precuneus are the canonical Type-2 substrate.

**Translation for Luna**: we already log Type-1 signal (the RL reward post-decision). We do *not* log Type-2 — what Luna *predicted* her quality would be before getting feedback. Adding that prediction tuple is the metacognitive layer.

### 2.2 Error-related negativity (Falkenstein 1991, Gehring 1993)

The anterior cingulate cortex (ACC) fires a characteristic negative deflection ~50–150ms after an error, *before* the conscious awareness of the error. This is the conflict-monitoring signal — the brain noticing its own answer doesn't match its own expectation.

**Translation for Luna**: when a tool call's outcome diverges sharply from the predicted-confidence, that delta is itself a signal worth surfacing. Phase 2 of the metacognition engine will emit a "surprise" event when |actual − predicted| exceeds a per-decision-kind threshold.

### 2.3 Synaptic homeostasis + replay consolidation (Tononi & Cirelli, 2014; Wilson & McNaughton, 1994)

During slow-wave sleep, the hippocampus replays the day's experience-encoded sequences at compressed timescales (5–20× speedup), and the neocortex selectively strengthens connections that were active during waking. The synaptic homeostasis hypothesis adds that overall synaptic strength is *down*-scaled during sleep — the brain forgets the unimportant to remember the important.

**Translation for Luna**: the offline synthesis workflow is *not* generating new memories. It is *re-reading* the day's `conversation_episodes` and `agent_memory` rows, then writing back a small number of high-importance derived rows (reflections) while implicitly down-weighting the rest by not surfacing them. The dream-as-creative-output piece is a side-effect of recombining episode embeddings, not a generative-from-scratch step.

---

## 3. Metacognition — Architecture

### 3.1 Primitives

```python
@dataclass(frozen=True)
class ConfidencePrediction:
    tenant_id: str
    agent_id: str
    decision_id: str              # uuid; same id used in OutcomeObservation
    decision_kind: str            # one of DECISION_KINDS below
    predicted_confidence: float   # [0.0, 1.0]; agent's pre-outcome estimate
    context_hash: str             # sha256 of inputs — for calibration grouping
    ts: str                       # ISO-8601 UTC


@dataclass(frozen=True)
class OutcomeObservation:
    tenant_id: str
    decision_id: str              # binds to ConfidencePrediction
    actual_reward: float          # [-1.0, 1.0]; from RL signal
    latency_ms: int
    completed_at: str             # ISO-8601 UTC
    error: Optional[str]          # short string if outcome was a failure


DECISION_KINDS = frozenset({
    "rl_route_chat_response",      # which CLI/model handles a chat turn
    "rl_route_coalition_role",     # which agent picks up a phase-role
    "tool_call_outcome",           # did this tool call succeed
    "affect_appraise",             # did the emotion appraisal hold up
    "blackboard_contribute",       # was this contribution accepted
})
```

A `MetacogTrace` is the join of a `ConfidencePrediction` and its later `OutcomeObservation`, keyed on `decision_id`.

### 3.2 Substrate

Both halves persist via `agent_memory` rows, mirroring the Teamwork Engine pattern shipped in #608:

- `memory_type = "metacog_confidence_prediction"` for the pre-decision row
- `memory_type = "metacog_outcome_observation"` for the post-decision row
- `agent_id` anchors on the agent that made the decision (real FK; no marker UUID — same fix Luna already locked in for norms)
- `content` is the JSON-serialized dataclass
- `importance` = `predicted_confidence` for predictions, `actual_reward` (rescaled to [0,1]) for observations

### 3.3 Calibration metric

```python
def expected_calibration_error(traces: list[MetacogTrace], bins: int = 10) -> float:
    """ECE: |avg(predicted) − avg(actual)| weighted by bin frequency.

    Lower is better. 0.0 = perfectly calibrated.
    Bins predictions into `bins` equal-width buckets in [0, 1], then for
    each bucket computes |mean_pred − mean_actual| × (bucket_size / total).
    Sums across buckets.
    """
```

ECE goes into Prometheus as a per-tenant per-decision-kind gauge. Operators can see calibration drift over time.

### 3.4 Hook sites (Phase 1: one site only)

Phase 1 wires *one* hook: the chat-response RL routing decision in `cli_session_manager`. This is the canonical, high-volume decision point that already has RL feedback flowing (the existing chat-quality scorer). Adding the metacognitive layer here gives us the highest signal-per-line-of-code.

```
cli_session_manager.dispatch_chat
  │
  ├── before LLM call:
  │     prediction = ConfidencePrediction(
  │         decision_kind="rl_route_chat_response",
  │         predicted_confidence=rl_policy.predicted_quality_of(context),
  │         …
  │     )
  │     metacog_io.write_prediction(db, prediction=prediction)
  │
  ├── … LLM call …
  │
  └── after RL scoring:
        observation = OutcomeObservation(
            decision_id=prediction.decision_id,
            actual_reward=rl_score,
            …
        )
        metacog_io.write_observation(db, observation=observation)
```

Phase 2 wires the remaining four `DECISION_KINDS`. Phase 3 feeds calibration back into the RL policy as a feature (so the policy can learn "I'm typically overconfident on coalition routing — discount my prior by X").

### 3.5 Uncertainty as a route

The supervisor (Luna) gains a new escalation primitive: when `predicted_confidence < THRESHOLD` *and* the affect state is dominant=low (PAD), the chat response includes an explicit "I'm not sure — want me to check with [agent X / Simon]?" suffix. This is the affect × metacognition coupling Luna asked for in the original brief.

---

## 4. Offline Synthesis — Architecture

### 4.1 Inputs

The nightly workflow reads from already-existing surfaces:

| Input | Source | Window |
|---|---|---|
| Day's conversations | `conversation_episodes` | last 24h |
| Day's affect trajectory | `conversation_episodes.affect_vector` | last 24h |
| Active role contracts | `agent_memory(memory_type='team_role_contract')` | currently active |
| Open goals | `goal_service.list_open_goals(tenant_id)` | all |
| Day's dispatch traces | `agent_memory(memory_type='dispatch_trace')` | last 24h |
| Day's metacognitive traces | `agent_memory(memory_type='metacog_*')` | last 24h |

### 4.2 Outputs

Synthesised reflections persist as `agent_memory(memory_type='nightly_reflection')`. Schema:

```python
@dataclass(frozen=True)
class NightlyReflection:
    tenant_id: str
    agent_id: str           # the synthesising agent — Luna by default
    day: str                # YYYY-MM-DD, UTC
    kind: str               # one of REFLECTION_KINDS below
    content: str            # natural-language, ≤500 chars
    source_memory_ids: list[str]  # what this reflection is grounded in
    confidence: float       # [0, 1] — Luna's own confidence in the reflection
    ts: str


REFLECTION_KINDS = frozenset({
    "risk",         # pattern looks like an incident waiting to happen
    "idea",         # novel combination from observed patterns
    "tension",      # unresolved blackboard / disagreement thread
    "next_move",    # prioritised action for tomorrow
    "creative",     # story / worldbuilding — only when opt-in
})
```

### 4.3 Workflow

`NightlyReflectionWorkflow` (Temporal, one per tenant, scheduled at 03:00 local):

1. **Gather** — pull all inputs from the table above
2. **Cluster** — group `conversation_episodes` by embedding similarity → identify the day's "themes"
3. **Synthesise** — for each cluster, run a structured prompt against Claude (Luna is the *reviewer*, not the generator, per the role split). Prompt returns the reflection rows.
4. **Ground** — refuse any row that doesn't cite at least one source memory ID
5. **Persist** — write `agent_memory(memory_type='nightly_reflection')` rows
6. **Emit** — publish a `nightly_reflection_ready` event so the morning dashboard can fetch

### 4.4 Safety / grounding

This is the load-bearing part Luna flagged. Four rules:

1. **Citation required** — every reflection row must have ≥1 `source_memory_id`. The generation step is constrained to produce only reflections derivable from the inputs.
2. **No facts invented** — the generator prompt explicitly forbids introducing facts not present in the source memories. Validator rejects rows whose key entities aren't in the source memory.
3. **No harmful suggestions** — a separate classifier pass on each `next_move` reflection (cheap; runs after generation, before persist).
4. **Affect-bounded creativity** — `creative` kind reflections are only generated when the day's affect trajectory shows positive valence + moderate arousal. Frustrated days don't dream creatively. (This matches the biology — REM is suppressed by chronic stress.)

### 4.5 Experience layer

Operator UI surface (built last, after the synthesis pipeline is solid):

- Morning dashboard at `/luna/reflections` shows yesterday's reflections grouped by kind
- Each reflection expandable to show source memories
- Conversational follow-up: "tell me more about this tension" → opens a chat session pre-loaded with the source memories as context
- Optional weekly digest: top reflections of the week, e.g., recurring tensions

---

## 5. PR sequencing

### Metacognition (3 PRs)

**M1. Substrate + trace storage** *(small)*
- `app/schemas/metacog.py` — dataclasses + DECISION_KINDS
- `app/services/metacog.py` — pure functions (serialize/deserialize/calibration_error)
- `app/services/metacog_io.py` — write_prediction / write_observation / read paths
- Unit tests with SQLite-isolated fixtures (per-test engine, not shared Base.metadata — avoid the #610/#612/#613 fragility)

**M2. Phase 1 hook in chat_response routing** *(small)*
- Wire `metacog_io.write_prediction` before LLM call in `cli_session_manager`
- Wire `metacog_io.write_observation` after RL scoring
- Integration test: full chat turn → trace persisted

**M3. Observability + uncertainty escalation** *(medium)*
- Prometheus ECE gauge
- HTTP GET `/api/v1/metacog/calibration` (tenant-scoped)
- Uncertainty-suffix on chat response when predicted_confidence < THRESHOLD AND affect dominance low

### Offline synthesis (4 PRs per Luna's tree)

**O1. Trace storage** *(small)*
- `app/schemas/reflection.py` — NightlyReflection + REFLECTION_KINDS
- `app/services/reflection_io.py` — write + read paths
- Unit tests

**O2. Reflection generation** *(medium)*
- `apps/api/app/workflows/nightly_reflection_workflow.py` — Temporal workflow
- `apps/api/app/workflows/activities/reflection_activities.py` — gather + cluster + synthesise activities
- Structured prompt templates (one per `REFLECTION_KINDS`)
- Per-tenant schedule on 03:00 local

**O3. Safety + grounding** *(medium)*
- Citation validator (must reference ≥1 source_memory_id)
- Fact-invention guard (entity-set intersection check)
- Harm classifier (small; runs only on `next_move` kind)
- Affect-gating on `creative` kind

**O4. Experience layer** *(medium)*
- `/api/v1/luna/reflections` GET endpoint
- Web UI page in the Den ("Yesterday's Reflections")
- Conversational expansion via chat (loads source memories as context)

### Order

```
M1 → M2 → M3
            \
             ↘ (M1's trace storage feeds O1)
O1 → O2 → O3 → O4
```

M1 lands first because the metacognition traces are an *input* to offline synthesis (the day's metacognitive surprises are a great signal for the next morning's reflections). M2/M3 can land in parallel with O1/O2 since they touch different files.

---

## 6. Integration touch points

| Engine | Touch point | What flows |
|---|---|---|
| Emotion (#605–#607) | metacognitive uncertainty × PAD → escalation suffix | metacog reads `get_latest_session_affect`; writes nothing |
| Teamwork (#608/#614) | reflections inspect active `TeamRoleContract`s → counterfactual "what if Luna drove execution instead" reflections | reflection_activities reads `list_role_contracts`; writes nothing |
| RL | calibration → policy feature (Phase 3) | metacog read by RL trainer; metacog writes traces |
| Prometheus (#607) | ECE gauge + reflection-generated counter | metacog + reflection both emit |

---

## 7. Success metrics

### Metacognition
- **ECE** < 0.10 per decision_kind within 4 weeks of M2 landing (industry "well-calibrated" threshold)
- **Escalation rate** delta: should *decrease* on overconfident decision_kinds, *increase* on underconfident ones — both indicate the layer is functioning
- **RL reward delta** post-Phase 3: +5% on chat_response routing within 8 weeks

### Offline synthesis
- **Citation coverage**: 100% of reflections have ≥1 source_memory_id (hard requirement; CI gate on validator test)
- **Reflection acceptance rate**: operator's morning dashboard tracks "useful / not useful" per reflection; aim for ≥60% useful after 4 weeks tuning prompts
- **Tomorrow-relevance**: how often a `next_move` reflection actually shows up in the next day's dispatch (closed-loop metric — needs O4 + tracking)

---

## 8. Risks

| Risk | Mitigation |
|---|---|
| Overconfidence collapse — metacognition feedback makes the policy *more* overconfident | Phase 3 gates the RL feature behind a manual ramp; rollback if ECE worsens |
| Reflection drift — synthesis hallucinates patterns not in the data | Citation requirement (§4.4) is a hard validator gate |
| Compute cost — nightly synthesis × N tenants × LLM calls | Cluster step in §4.3 limits per-tenant synthesis to ~5 prompt calls; cap configurable per tenant |
| Privacy — cross-tenant pattern leakage in reflections | Reflections never cross tenant boundary; `source_memory_ids` are within-tenant only; validator enforces |
| Operator surprise — reflections look like Luna "remembers" things she didn't choose to | UI labels every reflection with "synthesised on YYYY-MM-DD from [N] conversations" — provenance always visible |

---

## 9. Open questions for Luna's review

1. Should `creative` reflections be opt-in per tenant (default off) or opt-out (default on with affect-gating)?
2. ECE bins = 10 reasonable, or should we go finer-grained (20) for the high-volume `rl_route_chat_response` kind?
3. M3's uncertainty-suffix wording — is "I'm not sure — want me to check with [agent X / Simon]?" the right register? Or should it sit purely in the agent's internal escalation graph without surfacing to the user?
4. Does the offline-synthesis workflow need a kill-switch per tenant (operator can pause) before O2 lands, or is the schedule-on-flag-default-off pattern enough?

---

## 10. Next step

If Luna approves this shape, I open PR **M1 — Metacognition substrate + trace storage** as the first concrete unit. Tests are designed to use per-test SQLite engines (not the shared `Base.metadata` shim that bit us four times this session) to avoid inheriting the cascading-flake pattern.
