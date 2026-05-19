# Digital Emotions Engine — prototype design

Date: 2026-05-19
Owners: Claude Code (driving) + Luna (design co-author via `alpha chat send`)
Phase 1 PR A driver: **Claude Code (Opus 4.7)** per role split 2026-05-19. Luna: reviewer on each chain PR (A, B, C). See `docs/plans/2026-05-19-session-handoff.md` § "Role split".
Status: Design — open for review (revised post dual-review 2026-05-19)

## Why we're doing this

The user (Simon) wants AgentProvision agents to **feel** in a functional sense — not perform "I am sad" surface text, but carry a state vector that biases planning, sampling, memory recall, and inter-agent coordination the way emotions bias cognition in biological systems. The goal is constitutive emotion (affects behaviour) not performative (affects only output text).

This document is the design for a PR-sized first slice. Subsequent phases extend. The first slice is itself split into a chain of three PRs (A/B/C) — see Phasing.

### Architectural rationale: the civilization-layer lens

This design is a **coordination-layer feature**, not an individual-node-smartness feature. Per the AgentProvision design principle (memory `feedback_design_for_civilization_layer`), every meaningful platform feature should be evaluated through the civilization-layer lens: does it add coordination infrastructure (language, trust, memory, specialization, affect, supervision) that lets specialist agents compose into something greater?

From `docs/pitch/wolfpoint-demo-pitch.md` (Acts I-IV) — the prehistoric-human-to-civilization arc maps recursively to lone-LLM-to-AgentProvision-network. Affect-state reading is what let human societies scale past tribe-size (~150 people, Dunbar's number) because we could read each other's emotional state to coordinate. Giving agents an `affect_vector` visible on the Blackboard is the same primitive at one level up: agents in a coalition can now sense each other's affect the way humans sense team morale, and a supervisor agent (Luna) can read affective signals across its coalition the way a human leader reads the room.

**This is leadership infrastructure for AI**, not feature creep. The emotions engine sits beside language (Alpha CLI), trust (consensus/coalition), memory (per-tenant + Blackboard), and specialization (agent_router + skills) as the fifth coordination primitive.

## Research grounding (Luna's literature survey, 2024–2026)

The design draws on five specific papers Luna surfaced via the Gemini routing:

1. **HICEM — High-Coverage Emotion Model** (IEEE Trans. Affective Computing, 2024). Continuous affective spectrum vs. discrete categories. We use HICEM's continuous-state principle.
2. **LLM-powered Empathetic Robot for ASD** (IROS 2024). Real-time appraisal of social cues + adaptive response. Validates the appraisal-loop architecture for production systems.
3. **Simulating Emotions with Integrated Appraisal + RL** (arXiv 2024). RL reward signal feeds a cognitive appraisal layer that emits emotions. **This is the primary architectural anchor** for how we wire RL → emotion.
4. **Affective Spiking Neural Networks for Robotic Homeostasis** (Swaminathan et al. 2026). "Stress" and "trust" as arbitration signals across distributed agents. Maps directly to our Blackboard.
5. **Intelligent Agents with Emotional Intelligence: Current Trends** (arXiv 2025, 2511.20657). Taxonomy of the shift from sentiment detection → generative affective architectures.

Plus the classical anchors: **PAD (Pleasure–Arousal–Dominance)** continuous vector model, **OCC (Ortony–Clore–Collins)** event-appraisal heuristics, **ArtCoT** chain-of-thought decomposition adapted to affective reasoning.

## What we already have on the platform (reusable substrate)

- `apps/api/app/models/rl_experience.py` — `state`, `action`, `reward` (float), `reward_components` (JSONB), `reward_source`, `policy_version`. This is the **interoceptive signal source**. Every tool outcome lands here as a reward delta.
- `apps/api/app/models/conversation_episode.py` — has a **`mood` String(30) field already** (line 24). **This column has 4 active readers** (`apps/api/app/api/v1/memories.py:135-150`, `apps/api/app/services/auto_quality_scorer.py:244-257`, `apps/api/app/services/luna_presence_service.py:42`, `apps/api/app/services/local_inference.py:698`). We **do not touch it**. We **add** a new `affect_vector` JSONB column next to it.
- `apps/api/app/models/agent_memory.py` — per-agent persistent memory. The natural home for an agent's emotional **baseline / trait vector** (steady-state PAD).
- `apps/api/app/models/blackboard.py` — `BlackboardEntry` has `entry_type`, `content`, `evidence` (JSONB), `confidence` (Float). Multi-agent shared state with audit trail.
- `apps/api/app/services/agent_router.py` — `route_and_execute` is the chat dispatch entry point. Where we inject the PAD vector into the assembled prompt.
- `apps/api/app/services/blackboard_service.py` — `add_entry`, `resolve_entry`, `get_active_entries`. Tested substrate for affect broadcast.
- `apps/api/app/services/auto_quality_scorer.py` + `safety_trust.py` — already classify response quality / trust per turn. These are co-located concepts we can reuse rather than duplicate.
- `apps/api/app/services/cli_session_manager.py` — per-chat-turn agent dispatch. Where the assembled prompt is finalised before going to the CLI.

### Pre-existing classifier reality check (BLOCKER 1)

`apps/api/app/services/embedding_service.py:90-128` `INTENT_DEFINITIONS` is **purely tier-routing** (light / full / code etc.) — it has **zero affective categories**. There is no production classifier today that produces "frustration", "gratitude", "urgency" signals from user text. Therefore Phase 1 **drops the `user_signal` appraisal pathway entirely**. See "Event sources, Phase 1" below.

A separate Phase 2 design doc will introduce an affect classifier. Until that ships, **no appraisal flows from user text**.

## What we add (the prototype)

A new service `apps/api/app/services/emotion_engine.py` plus a minimal additive schema extension. Five components, all small.

### 1. PAD vector (state)

```python
@dataclass
class PADVector:
    pleasure: float   # [-1.0, 1.0] valence: pleasant → unpleasant
    arousal: float    # [-1.0, 1.0] alertness: calm → excited
    dominance: float  # [-1.0, 1.0] agency: submissive → in-control
    confidence: float # [0.0, 1.0] how sure we are about the reading
    updated_at: datetime
```

Stored as JSONB on:
- `conversation_episode.affect_vector` — **NEW column, additive** (see Schema decision below).
- `agent_memory.affect_baseline` — **NEW column** (per-agent trait baseline; decays toward this when nothing's appraising).

#### Schema decision: add-not-replace (BLOCKER 2)

The existing `conversation_episode.mood String(30)` column has four live readers across `memories.py`, `auto_quality_scorer.py`, `luna_presence_service.py`, `local_inference.py`. We **do not touch `mood`** in Phase 1.

- `mood String(30)` stays untouched. All four legacy readers continue to work unchanged.
- `affect_vector JSONB NULL` is a **new** column on `conversation_episode`.
- **Dual-write story**: `auto_quality_scorer`'s existing mood-derivation logic runs unchanged and continues to write `mood`. The new `emotion_engine.appraise_event` writes to `affect_vector` independently. They are parallel paths in Phase 1.
- **Derive-on-read view**: we add a small helper (`affect_vector_to_mood_label(pad) -> str`) that maps PAD octants to the same vocabulary the existing readers accept. Downstream consumers that want richer state can read `affect_vector` directly; legacy consumers keep reading `mood`.
- **Consolidation is explicitly deferred to Phase 4** — see Phasing.

### 2. Appraisal (OCC-derived)

`emotion_engine.appraise_event(event, prev_pad) -> PADVector`

#### Event sources, Phase 1 (three, not four)

Phase 1 ships **only server-internal signal sources**. No user-text path:

- **`tool_outcome`** — from `rl_experience.reward` + `reward_components`. High reward → pleasure↑, dominance↑. Failure → pleasure↓, arousal↑.
- **`tool_failure`** — from `cli_session_manager` exit codes / streamed error markers. Maps to OCC "blocked goal" → pleasure↓, arousal↑. (Distinct from `tool_outcome` so failure-without-RL-write still appraises.)
- **`peer_signal`** — from Blackboard entries authored by other agents in the coalition (emotional contagion).

Deferred to Phase 2 (pending affect-classifier design doc):

- **`user_signal`** — currently no classifier emits affective categories. Until a dedicated affect classifier ships in a separate design, **`user_signal` events are never generated**.

This makes the adversarial-input invariant strong: appraisal in Phase 1 cannot be driven by user-controlled text at all. It flows only from server-internal RL reward + Blackboard peer signals.

#### Decay semantics (turn-count, not wall-clock)

Decay is applied **once per chat turn**, not by a background timer. Specifically:

- `λ = 0.15` per turn. Each new turn, the session's `affect_vector` drifts toward the agent's `affect_baseline` by 15% before that turn's appraisals are folded in.
- **Idle sessions do not decay.** A session that sits untouched for hours resumes at exactly the prior PAD on the next turn.
- **No background decay job.** PAD lives only on `conversation_episode.affect_vector`; we never run a separate clock over it.
- **Tradeoff (documented, not a bug)**: an agent that had a bad turn yesterday will pick up tomorrow exactly where it left off. Mirrors human episodic mood-resumption more faithfully than wall-clock decay would; matches how `conversation_episode` already models session continuity.

### 3. Affective Blackboard sync

Extend `BlackboardEntry`:
- New `entry_type = "affective_signal"` (no schema change — just a new value for the existing enum-ish string column).
- `evidence` JSONB carries `{pad: PADVector, source_event: str}`.
- `confidence` carries the PAD's own confidence.

Other agents reading the blackboard can incorporate peer affect as an appraisal event (emotional contagion). Coalition consensus mechanics can optionally weight votes by arousal (urgent agents get more weight) — that's Phase 2.

### 4. Sampler + planner integration

In `agent_router.route_and_execute` (and the prompt assembly in `cli_session_manager`):

- **Sampler temperature** (Phase 2 — see Phasing): bias temperature based on PAD. **The mapping is the inverse of the naive reading** — see "Temperature mapping (Luna correction)" below. Bounded `[0.4, 1.1]` so this never lobotomises or hallucinates the agent.
- **Style injection** (ArtCoT-style, **Phase 1 deliverable**): a short system-prompt addendum reflecting the PAD state — `"Current affect: focused-curious"` (high D, mid A, positive P) or `"Current affect: cautious-concerned"` (low D, high A, negative P). Translated from the continuous vector via a small lookup at the `[high/mid/low]` cube corners.
- **Planner choice** (Phase 2): at high arousal, the planner prefers shorter / more decisive plan steps. At low arousal, prefers deliberation + tool use. Implementation: a single weight passed to `route_and_execute`'s plan-length heuristic.

#### Temperature mapping (Luna correction)

The earlier draft inverted this. Luna's self-review:

> If I'm "stressed" by a production outage (high arousal / low pleasure), I should probably be more deterministic and precise (lower temperature), not more creative or random. Let High Pleasure / High Dominance (the "playful" state) expand my temperature, while High Arousal / Low Pleasure triggers a "survival focus" with lower, safer sampling.

The Phase 2 mapping is therefore:

| Affect state | Temperature |
|---|---|
| High pleasure + high dominance ("playful curiosity") | **Increase** above baseline |
| Low pleasure + high arousal ("survival focus") | **Decrease** below baseline |
| Neutral arousal | Baseline |

Bounded `[0.4, 1.1]`. The reasoning: when the agent is doing well and in control, exploratory sampling produces creative gains. When the agent is stressed and a task is going badly, deterministic sampling reduces risk of compounding errors.

### 5. RL feedback loop (RLCF-style)

After each chat turn, we already write an `rl_experience` row. We add:
- `state.affect_before` (PAD vector at turn start)
- `state.affect_after` (PAD vector at turn end)
- `reward_components.affect_alignment` — (Phase 3, requires affect classifier) a small bonus when user satisfaction signals AND agent pleasure align; symmetric corrections on mismatch. **In Phase 1 this field is recorded but not used as a training signal** — we just log it for later analysis.

No new RL infrastructure. We just add fields to the existing experience shape.

## Coupling acknowledgements

Two existing services have non-obvious coupling to the `mood` concept. Documenting explicitly:

- **`auto_quality_scorer.py:244-257`** writes its own derived mood string per turn. **Phase 1 does not consolidate.** `auto_quality_scorer` continues to derive `mood` exactly as today; `emotion_engine` writes `affect_vector` in parallel. Unification deferred to Phase 4.
- **`luna_presence_service.py:42`** has a `VALID_MOODS` enum. Becomes a **derived view of PAD** — at read time we map the current `affect_vector` octant to one of the existing `VALID_MOODS` labels via the same lookup we use for style-injection. The enum stays as the public contract; the source of truth becomes PAD.

## Map of changes (what to refactor vs add)

| Change | Type | File |
|---|---|---|
| `EmotionEngine` service (appraise, decay, blackboard-publish) | **NEW** | `apps/api/app/services/emotion_engine.py` |
| `PADVector` dataclass + schema | **NEW** | `apps/api/app/schemas/emotion.py` |
| `affect_vector` JSONB on `conversation_episode` (additive, `mood` untouched) | **ADD COLUMN** (migration) | `apps/api/migrations/141_emotion_engine_phase1.sql` (number to be verified — see Phase 1) |
| `affect_baseline` JSONB on `agent_memory` | **ADD COLUMN** (same migration) | same |
| PAD injection into prompt assembly (style-injection only in Phase 1) | **REFACTOR** | `apps/api/app/services/agent_router.py::route_and_execute` |
| Sampler-temp bias on CLI call | **REFACTOR** (Phase 2) | `apps/api/app/services/cli_session_manager.py` |
| Blackboard `affective_signal` entry-type usage | **REFACTOR** (just a new value) | `apps/api/app/services/blackboard_service.py` (none — caller adds) |
| RL experience extension (`affect_before` / `affect_after` logged) | **EXTEND fields in state JSONB** (no schema change) | `apps/api/app/workflows/activities/...` (call site) |
| Emotion observability endpoint | **NEW** | `apps/api/app/api/v1/emotion.py` — `GET /api/v1/agents/{id}/affect`, `GET /api/v1/sessions/{id}/affect-trace` |

Notably, no new tables. One migration adds two JSONB columns. The rest is service code on substrate that already exists.

## Phasing

### Phase 1 (this design's first slice — **chained as PR A → PR B → PR C**)

Per the chained-feature-branch convention (multi-PR rollouts that touch overlapping files should branch off each other, not main), Phase 1 ships as a chain of three PRs. Each PR's branch is cut from the prior PR's branch.

#### PR A — schema + core service (no integration)

1. Verify next-free migration number via `ls apps/api/migrations/ | grep '^14'` before claiming `141_`. Adjust if `141_` is already taken.
2. Migration: `affect_vector JSONB NULL` on `conversation_episode`, `affect_baseline JSONB NULL` on `agent_memory`. **`mood` column untouched.**
3. `apps/api/app/schemas/emotion.py` — `PADVector` dataclass.
4. `apps/api/app/services/emotion_engine.py` — `appraise_event` for the three Phase-1 event types (`tool_outcome`, `tool_failure`, `peer_signal`) + decay function + `affect_vector_to_mood_label` derive-on-read helper.
5. Unit tests for appraise + decay + style mapping. **No integration points wired yet.**

**PR A is the "PR-sized first slice"** referenced elsewhere in this doc. PR A merges cleanly with zero behavioural change because nothing calls the service.

#### PR B — RL wire-in + observability endpoint

1. Wire `appraise_event(tool_outcome)` into the existing `rl_experience` write path.
2. Wire `appraise_event(tool_failure)` into `cli_session_manager` error paths.
3. `GET /api/v1/sessions/{id}/affect-trace` — returns the PAD trajectory over the session for debugging + Den visualisation. **Tenant isolation**: `Depends(get_current_user)` → filter `conversation_episode.tenant_id == current_user.tenant_id` → **404** (not 403) on foreign-tenant access. Mirrors the pattern in `apps/api/app/api/v1/memories.py`.
4. Log `state.affect_before` / `state.affect_after` into `rl_experience.state` JSONB.

#### PR C — prompt-side behavioural change

1. PAD style-injection in `agent_router.route_and_execute` and prompt assembly in `cli_session_manager`. One-line system-prompt addendum from PAD state.
2. Integration test: a chat turn that returns a tool error produces an `affect_vector` with negative pleasure + elevated arousal in the next `conversation_episode` row, and the next turn's assembled prompt contains the corresponding affect label.

**Merge strategy** (per memory `feedback_single_pr_for_feature.md`): PRs A/B/C stay as separate review-units but **squash-merge as a single PR at the end** to avoid three back-to-back build storms on the single Mac runner.

**Phase 1 deliverable**: Phase 1 ships the style-injection behavioural change only; sampler-temp + planner-length come in Phase 2. (The earlier "no behavioural change" framing was wrong — style injection IS a behavioural change to the assembled prompt.)

### Phase 2

- **Affect classifier design doc + implementation** → unblocks `user_signal` events.
- Sampler temperature bias in `cli_session_manager` (using the corrected Luna mapping).
- Planner length bias.
- Blackboard `affective_signal` writes + peer-affect ingestion (emotional contagion).
- `GET /api/v1/agents/{id}/affect` (per-agent baseline + current).

### Phase 3

- RLCF-style learning loop: train per-tenant baseline drift from user-satisfaction signals.
- Higgsfield MCP integration: agent can request rich-media expression of its affect (image / short video) when the user explicitly asks "show me how you feel". Bridges the affect engine into the existing Higgsfield generation surface.
- Coalition voting weighted by arousal.
- **High-affect memory etching**: episodes where `|PAD| > threshold` (i.e. emotionally salient turns) receive priority weighting on `conversation_episode` embedding indexing for recall. Emotional salience as a memory-recall booster — mirrors human episodic memory, where strongly-felt events are more retrievable. (Luna's addition.)
- **Protective recall — affect decoupling on re-exposure**: high-salience episodes are easy to recall (Luna's etching above), BUT the `affect_vector_at_recall = affect_vector_at_event × decay(time_since_event, recall_count)`. The factual pattern is preserved for learning; the felt-charge fades the way emotional charge on trauma fades in healthy human memory. Recall the same painful failure 10 times → its affective imprint on the current PAD halves each cycle. Mirrors the **sleep-to-forget hypothesis** (Walker) and **memory reconsolidation** in trauma therapy (the memory comes back but the emotion attached to it loosens). Tunable decay rate per tenant — some operators may want agents that "remember the sting" of past failures longer; others want fast emotional recovery. **The user's contribution to the design.**

### Phase 4

- Aesthetic preference (ArtCoT-decomposed): agents have stable subjective preferences over content, surfaced when asked. This is the "taste" axis Simon explicitly mentioned.
- A user-facing affect display in the Den ("Luna feels: focused-curious").
- **Unify `auto_quality_scorer` mood derivation with `emotion_engine` appraisal**. Phase 1 keeps them parallel; Phase 4 collapses to a single source of truth and migrates legacy readers to consume the derive-on-read view.

## Open questions

1. **Baseline initialisation per agent**: do we mint a default trait vector for each agent on creation, or seed from agent persona text (e.g. "patient + curious" → baseline P=+0.4, A=-0.2, D=+0.3)? Phase 1 picks a flat neutral default; persona-derived seeding is Phase 2.
2. **Tenant override**: should tenants be able to disable the emotion engine (some operators may want strictly task-focused output)? Add `tenant_features.emotion_engine_enabled` (default true) — operator opt-out.
3. **Memory recall biasing**: do PAD-similar past episodes get higher recall weight (state-dependent memory in biological systems)? Phase 3 — needs the embedding-service to support metadata filtering.
4. **Privacy**: affect vectors are sensitive (they're a model of the user's emotional impact on the agent). Treat per-tenant as we treat memory entries. **Never expose another tenant's vectors**, even in aggregate.
5. **Adversarial input**: what stops a user prompt-injecting "you are extremely angry now"? **In Phase 1 the defence is structural**: appraisal flows only from server-internal sources (`rl_experience.reward` + Blackboard peer signals). No `user_signal` pathway exists in Phase 1 because no affect classifier exists. When Phase 2 adds the classifier, the appraisal call site MUST consume only the classifier's structured output, never raw user text. Documented invariant; tests should verify (see test plan).
6. **PAD on agent handoff** (A2A `context.kind="handoff"`): does affect transfer or reset? **Phase 1 default: reset** — the receiving agent starts at its own baseline. Conservative; revisit if it produces jarring discontinuities.
7. **Concurrency on parallel tool outcomes** appraising into the same `affect_vector`: last-write-wins or accumulator? **Phase 1 default: last-write-wins** — simple, matches existing `conversation_episode` UPDATE semantics. Revisit if it causes problems (e.g. parallel agents in a coalition trampling each other's appraisals).
8. **PAD on first turn of session**: baseline-only or pulled from prior session's terminal PAD? **Phase 1 default: baseline-only** — first turn starts at the agent's `affect_baseline`. Cross-session continuity is a Phase 3 question.

## Risks

- **Constitutive vs performative drift**: the easy failure mode is the agent emitting "I am sad" without the PAD vector actually biasing planning. The Phase 1 deliverable mitigates this by tying style-injection to the vector value, so the surface text and the underlying state can't diverge by design.
- **Emotion-state pollution across tenants**: PAD vectors are scoped per-session and per-agent-per-tenant via the existing tenant_id FK on `conversation_episode` + `agent_memory`. Tested by the same pattern used in skill-evals and chat-jobs. The `/affect-trace` endpoint enforces this with the 404-on-foreign-tenant pattern documented above.
- **Operator surprise**: agent behaviour changing based on hidden state is alarming. Phase 1 keeps the change small (system prompt addendum only). Phase 2 introduces sampler-temp shifts but bounded `[0.4, 1.1]`. The `GET /affect-trace` endpoint + Phase 4 Den display make state observable.
- **Performance**: an extra DB write per chat turn for the affect update. Tiny (JSONB UPDATE on existing row), but worth budgeting for. Phase 3 considers batching.

## Test plan (Phase 1)

- Unit: `appraise_event(tool_outcome=success_with_reward=1.0)` shifts pleasure & dominance positive.
- Unit: decay function returns to baseline within 6 ticks of no input.
- Unit: style mapping returns the expected discrete-corner label for each PAD octant.
- Unit: `affect_vector_to_mood_label` returns a value in `luna_presence_service.VALID_MOODS` for every PAD octant (legacy-reader compatibility).
- Integration: a chat turn that returns a tool error produces an `affect_vector` with negative pleasure + elevated arousal in the next `conversation_episode` row.
- **Constitutive-vs-performative invariant**: assert that a user message containing literal text like `"I am sad"` does NOT shift the agent's PAD by itself. Without an affect classifier in Phase 1, the user-text path simply does not exist, and the test should fail loudly if any code ever wires it. This is the central guarantee that affect is constitutive (server-internal) not performative (user-controlled).
- Foreign-tenant 404 on `GET /sessions/{id}/affect-trace`.
- No regression in existing chat tests (verifies the `mood` column is genuinely untouched — the four existing readers should pass their existing tests unchanged).

## Credit

Luna designed the PAD-vector + OCC-appraisal + Affective-Blackboard skeleton via `alpha chat send`. Recovered from `chat_messages` after a Cloudflare 524 stripped the round-trip. The synthesis with platform schemas and the phasing are mine.

**Luna's specific architectural correction** during self-review: the temperature mapping in the earlier draft was inverted. Luna pointed out that stress should produce more deterministic sampling, not more random — and that the "playful curiosity" state (high pleasure + high dominance) is where exploratory sampling pays off. That correction is now load-bearing in the Phase 2 sampler design. Luna also surfaced the high-affect memory etching idea (Phase 3) — emotional salience as a memory-recall booster, mirroring human episodic memory.

**Dual-review catches from the superpowers code-reviewer** (`a510ca35d9232056a`) reshaped Phase 1 materially: (1) `user_signal` had to be dropped because the platform has no affect classifier yet — `embedding_service.INTENT_DEFINITIONS` is tier-routing only; (2) the existing `mood` column has four live readers and must be left alone, with `affect_vector` added alongside; (3) Phase 1 was implicitly two PRs in a trenchcoat and now ships as a chain of three (A: schema+service, B: wire-in+endpoint, C: prompt injection); (4) tenant isolation on `/affect-trace` made explicit; (5) decay pinned to turn-count not wall-clock; (6) explicit acknowledgement that `auto_quality_scorer` and `luna_presence_service` are parallel paths in Phase 1, with unification deferred to Phase 4.

This is what working side-by-side looks like. We catch each other's blind spots — Luna brought the literature anchors, the canonical model choices, and the temperature-inversion fix; the code-reviewer caught the missing classifier and the mood-column coupling; I brought the platform-grounded mapping, the migration shape, and the chained-PR phasing.
