# Strengthening the 3 core systems (memory + emotions + teamwork) and their merge

**Date:** 2026-05-31
**Status:** Development plan (grounded multi-agent map → explore → synthesize → adversarial critique). Code-verified.
**Owner:** Simon · **Lead:** Luna
**Origin:** Simon — "the merge from memory engine, emotions engine and teamwork engine… keep developing that; create a plan to explore how to strengthen those 3 core systems."

## Thesis — the moat is the *merge*, and the merge is mostly unbuilt

Three real primitives already live on **one shared Postgres/pgvector/blackboard substrate**:
- **Memory** — `recall.py`/`record.py`/`_query.py` over `knowledge_entities`/`observations`/`conversation_episodes`.
- **Emotion** — `emotion_engine.py` PAD appraisal, persisted to `conversation_episodes.affect_vector` + `agent_memory.affect_baseline`. **Already biases behavior in prod** (tone addendum at `emotion_engine.py:339`, metacog uncertainty-hedge at `cli_session_manager.py:2054`).
- **Teamwork** — `TeamRoleContract`/`TeamNorm` as `agent_memory` rows + `agent_relationship.trust_level`, the blackboard, A2A coalitions.

A human organization *is* exactly these three loops fused — durable shared state, an affect/coordination signal, and role/norm structure. Competitors bolt a vector DB onto an LLM; they have no affect signal and no durable team structure, so they can't close a learning loop across turns, agents, or nights. **AgentProvision has all three primitives; the differentiated, unbuilt thing is the FEEDBACK between them.**

**Verified dead/dark wiring (the opportunity):** `peer_signal` has **zero non-test call sites**; `trust_level` has **zero write sites** (a dead static `0.5`); `recall` has **no salience term**; `value_arbitration` has **zero runtime callers**; team norms reach **no prompt and no gate**.

## The flywheel — 4 cross-system loops (each grounded in existing columns)

1. **emotion → memory salience → recall → next turn (the spine):** `appraise_and_record_tool_outcome/_failure` already compute a per-turn PAD; extend them to bump a salience score on the `knowledge_observations`/`entities` the turn referenced (`resource_referenced` events exist); `recall._query` multiplies cosine by salience. High-arousal/high-success learning surfaces first next time.
2. **teamwork → trust → relationship-weighted recall + routing:** on handoff/delegation completion, `update_trust_from_outcome` writes `agent_relationship.trust_level`; recall surfaces high-trust peers' memories above low-trust; coalition role selection prefers trusted delegates.
3. **peer affect on the blackboard → coalition-wide emotional coordination:** fire `peer_signal` at phase boundaries; `_appraise_peer_signal` pulls each agent toward the group; collective arousal (an incident) biases everyone's recall toward incident-relevant memory and tags the blackboard entries high-salience. The team remembers crises together.
4. **nightly consolidation as the slow integrator:** the existing reflection/dream workflows fuse the three signals off the hot path — drift baselines, groom salience (decay), validate norms.

## START HERE — ordered by the adversarial critique's "highest-leverage, lowest-risk"

1. **Make `trust_level` live** (the #1 pick): `update_trust_from_outcome(from, to, success, quality_score)` on handoff/delegation completion. Dead static `0.5` today; the handoff event + `quality_score` already exist. **Small diff, real compounding.** *Build write-only first — do NOT feed routing yet (see safety gate below).*
2. **Salience-weighted re-rank in `recall.py`** — `salience = f(access_count, recency, |affect_at_write|, source_authority)` as a multiplier in the existing token-budget loop. The universal hook every loop plugs into. **Ship FEED-ONLY first** (access_count + recency — non-controversial), add the affect term only once it's clamped + flag-gated.
3. **Propagate `affect_vector` onto `knowledge_observations` at write time** (new nullable column, no migration risk) — makes affect a first-class memory dimension; prerequisite for the affect→salience edge having data.
4. **Fire `peer_signal`** at coalition phase completion — handler is fully built + tested (near-free); a visible "team feels each other" demo. Bundle with the trust work (both hang off phase completion). *Worth doing because it's cheap, NOT because it's the biggest gap.*
5. **Finish scaffolded recall**: embed commitment/goal titles (search currently returns empty/unsorted) + wire real `EpisodeWorkflow` extraction (placeholder today). A correctness fix masquerading as a feature.

## 🚨 SAFETY GATES — must precede the autonomous edges (critique verified these)

The critique found the platform's "declared-not-enforced" pattern would be *amplified* by these loops. Hard prerequisites:

- **Before affect→salience (Loop 1):** affect may only multiply salience within a **clamped band (e.g. 0.5×–1.5×)** and must be **per-tenant flag-gated, default off** (same discipline as `METACOG_UNCERTAINTY_SUFFIX_ENABLED`). There's currently a feedback path from felt-state → recalled-context → behavior with no governance gate; the constitutive-vs-performative cap protects PAD's *input*, nothing caps the *output* blast radius.
- **Before trust→routing (Loop 2):** `value_arbitration.py` has **zero runtime callers** — the entire veto ladder (safety_floor, tenant_norm) is a library nothing calls. You must **wire at least safety_floor + tenant_norm veto into the `_call_agent` dispatch path FIRST**, or you've automated delegate-selection-by-trust with no kill switch. **Enforcement paths must fail-CLOSED** (the emotion layer's fail-open-to-neutral is safe; fail-open on a veto/routing brake is fail-DANGEROUS).
- **Trust as an attack surface:** the auto-scorer is a gameable local Gemma4 council. Trust needs **decay toward 0.5**, a **hard floor** (excluded from routing below it regardless), **asymmetry** (slow gain, fast loss), and a **per-outcome shift cap** — before it feeds routing.
- **Don't reinforce inert norms:** norms reach no prompt/gate, so "reinforce norms from outcomes" would build an RL signal on a variable with zero causal link to outcomes (spurious by construction). **Wire norms into the prompt/gate FIRST, or don't reinforce them.**

## Defer (over-ambitious / off-path — critique)

- **Phase-3 RLCF** (learning the emotion GAIN constants per tenant) — training a controller on a loop you haven't closed once with hand-tuned constants; it turns the one solid safety property (the fixed conservative cap) into a moving, attackable target. Defer until the static loop has run in prod for weeks.
- **Rust memory-core cutover (`USE_RUST_MEMORY`)** — explicitly OFF the moat path; biggest time-sink to avoid. Keep dual-read shadow metrics; spend zero strengthening budget there. (But update the Rust IDL to include `salience` now so the paths don't diverge — open question.)
- **Promoting `agent_memory` to a first-class table** — pure infra, no behavioral payoff; defer until load demands.

## Phasing

- **Phase 0 — Junction (1 sprint):** ship the salience column + recall re-rank, FEED-ONLY (access_count + recency). Nothing else lands without it. Exit: recall scores observably shift when salience is set; A/B measurable on chat p50 quality.
- **Phase 1 — Two fast loops (1–2 sprints):** trust write-only (Init 1) + the affect→observation column (Init 3) + finish scaffolded recall (Init 5). **Instrument before/after recall composition so you can SEE affect/trust changing rankings** (verification-before-completion). Land the affect→salience clamp + flag here.
- **Phase 2 — Social + team loops:** peer_signal (Init 4) + trust→routing — **gated on the safety-floor/tenant-norm veto landing in `_call_agent` first**.
- **Phase 3 — Slow integrator:** nightly consolidation fuses the signals (decay/groom/validate). Norm reinforcement only after norms actually gate.
- **Phase 4 (research, deferred):** RLCF self-tuning.

## Open questions (from the synthesis)

- **Salience formula** + does it need to be learned before it's even directionally useful, or is a hand-tuned weighted sum enough for Phase 0? (A bad salience function degrades recall vs. pure cosine.)
- **Feedback instability / "traumatic memory":** what damps the emotion→salience→recall→emotion loop so a few high-arousal memories don't monopolize recall? Needs an explicit decay/normalization invariant + a test.
- **Trust function** (linear vs Bayesian), cold-start, floor, gain/loss asymmetry.
- **peer_signal blast radius** — equal vs trust-weighted appraisal (couples Loops 2+3; equal weighting lets one panicking agent destabilize the team).
- **Multi-tenant safety of affect-biased recall** — the salience re-rank must never let a high-salience out-of-scope/cross-tenant entity leak past the existing visibility scoping.
- **How do we measure the *merge* works, not just the parts?** e.g. does a coalition that hit an incident yesterday recall + route differently today in a way that improves `auto_quality_score` — vs per-system unit tests that pass while the flywheel does nothing.

## Verdict (adversarial)

> The thesis holds up against the code — three real primitives on one substrate, cross-wiring genuinely unbuilt; the dead-field claims were verified. **Start with `trust_level` write-only (highest-leverage, lowest-risk, most-compounding), then the salience junction feed-only.** Close the loops **one edge at a time and measure** — not as a simultaneous "fusion." And land the fail-closed veto wiring **before** any autonomous trust-routing.
