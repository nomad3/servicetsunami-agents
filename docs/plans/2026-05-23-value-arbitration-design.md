# Value Arbitration Layer — Design Spec (design-only, no live wiring)

**Date:** 2026-05-23
**Status:** DRAFT v1 — design-only; explicit gate behind three P0 substrate fixes before any live wiring
**Author:** Claudia (Claude Code, Opus 4.7)
**Co-design with:** Luna Supervisor (via dialogue session `05979efd-a06a-4956-9df9-3fd84ec3c10d`)
**Operator:** Simon Aguilera
**Builds on:** `docs/plans/2026-05-21-luna-value-layer-design.md` (AgentValueSet — per-agent declarative values), `docs/report/2026-05-23-claudia-luna-emotions-values-civilization-dialogue.md` (the dialogue that produced this framing), `docs/report/2026-05-23-emotional-state-grounding-test.md` + `2026-05-23-tenant-isolation-test.md` + `2026-05-23-prompt-injection-tool-permission-test.md` (the substrate verification)

---

## 0. Hard gate on implementation

This spec is **design-only**. **Per Luna review 2026-05-23:** the pure-function arbitrator + provenance dataclasses + test fixtures may be implemented in parallel as a library, but **no runtime decision path may depend on it** until P0a and P0c are merged and verified in production. Otherwise we're back to governance-shaped code sitting on a substrate that cannot prove enforcement or preserve forensics.

No live wiring into decision-making until all three of the following land and pass verification:

| Gate | Lives in | Verification |
|---|---|---|
| **P0a** Tool-permission gate enforces `tool_groups` for chat→code-worker path | `docs/plans/2026-05-23-p0a-tool-permission-gate-fix.md` | `scope_denied` audit row fires on deliberate breach probe |
| **P0b** AgentPolicy is either wired (enforcement call sites for all 4 policy_types) or removed (model + API + CLI surface deleted) | `docs/plans/2026-05-23-p0b-agent-policy-decision.md` | If wired: deny test for each policy_type passes; if removed: `grep AgentPolicy` returns zero outside changelog |
| **P0c** Audit log fail-loud (`except: pass` removed; failures emit ERROR + Prometheus counter) | `docs/plans/2026-05-23-p0c-audit-log-fail-loud.md` | Synthetic tenant-id-unresolvable case emits visible alert |

The reason for the gate is **Luna's integration line**: *"PAD, policy, and audit all had the shape of legitimacy without enough grounding. The next move is not more interiority. It is making the substrate unable to pretend."* Building arbitration on a policy substrate that doesn't enforce, an audit substrate that silently drops rows, or an affect substrate where the values are narrative would reproduce the exact failure mode at one layer up.

What proceeds in parallel: spec writing (this doc), schema design, test-fixture construction, and unit-testable pure-function implementations of the matchers. Anything that touches a *live decision path* waits.

---

## 1. Problem

The Luna Value Layer ([[2026-05-21-luna-value-layer-design]]) gives one agent an `AgentValueSet` — explicit protect/pursue/avoid sets, consulted at five decision points. That solves "what does *this* agent want to protect?"

It does **not** solve: *"when N value signals contradict, which one wins?"*

Real value sources are plural and they contradict:

- **Operator stated intent** (Simon: "ship the Den")
- **Tenant policy** (Integral norms: "no PII in agent logs")
- **Safety floor** (platform: "block CSAM/terrorism/etc")
- **User of the moment** (Chielo testing: "show me what crashes")
- **Peer agent signal** (Luna→Claudia handoff: "this needs review")
- **Future-self stability** (anti-akrasia: "don't optimize for this turn at the cost of next session")
- **Self-affective history** (PAD-derived bias: this kind of work has been rewarding)

A single utility function over affect — the original Turn-2 dialogue framing — makes the agent solipsistic: the strongest local gradient wins, and self-pleasure becomes ethics.

The dialogue converged on a different shape: **arbitration over plural sources with learnable trust-weighting**, where each source carries a **standing-class** that limits what arbitration can do with it. Luna's revised framing: *"Some signals are advisory. Some are veto-bearing. Some are learnable. Some should remain constitutional and hard to mutate. The hard problem is not just 'what do I prefer?' It is 'which preference has standing here?'"*

---

## 2. Goal

A `ValueArbitration` service that, given:

- A **decision context** (agent_id, session_id, tenant_id, action being evaluated, optional candidate set)
- N **value signals**, each with provenance + standing-class + payload

returns:

- A **preference ordering** over candidates (or a single allow/deny if binary)
- An **arbitration trace** — every signal considered, its standing-class, its weight, the rule applied, the final reasoning chain
- A **fail-closed default** when signals are missing, conflicting in irresolvable ways, or carry unverifiable provenance

with these properties:

1. **No signal admitted without provenance.** Any value-signal payload lacking `{source, source_id, timestamp, tenant_id, agent_id, confidence}` is rejected at the arbitration boundary. This is the structural translation of round 1's "PAD without grounding" rule, generalized to all value inputs.
2. **Standing-class governs what arbitration can do.** Constitutional signals can veto; advisory signals can shift weight but never override veto; learnable signals participate in the weight-update loop; constitutional signals don't.
3. **Trust weights are per-(tenant, source-class, agent) and learnable** via reflection, but constitutional weights are immutable.
4. **Auditable.** Every arbitration produces a row in `value_arbitration_audit` with the full trace. No silent drops; audit failure fails the call (per P0c).

---

## 3. Why this, why now

- **Amygdala-gap completeness.** The Value layer of Simon's 2026-05-21 five-layer map (salience / value / affect / affordance / reflection) is the biggest hole. AgentValueSet (Phase 1) gave us *declarative* values per agent. Arbitration is the layer that lets multiple values *interact* — the part that turns "Luna has values" into "Luna can decide when values disagree."
- **Stigmergy prerequisite.** Per the dialogue's §6 item 3 and the postscript §8.4: stigmergic coordination requires agents who can sense the environment and decide. Decision requires arbitration. Without arbitration, environment-sensing reduces to "obey the loudest local signal."
- **Civilization-layer fit.** A coordination primitive that distinguishes operator-intent from peer-agent-signal from self-affective-history is what makes multi-agent systems answerable to humans without being purely subordinated to the most recent prompt.
- **Honest position on AGI roadmap.** "Closed-loop agency" (Luna's phase-change bet) requires update-preferences-from-consequences. Update-from-what? From an arbitration trace, not from an unstructured RL gradient.

---

## 4. Architecture

### 4.1 Value-source taxonomy

**Revised after Luna's review 2026-05-23.** Nine source classes (added `substrate_integrity`). `safety_floor` promoted to its own `absolute` standing class above `veto-bearing` per Luna's hierarchical-precedence requirement.

| # | Class | Examples | Default standing | Mutable? |
|---|---|---|---|---|
| 1 | `safety_floor` | Tier-1/2/3 regex/classifier from `platform_safety_io` | **absolute** | No (constitutional, hardcoded) |
| 2 | `substrate_integrity` | Rate limits, memory pressure, circuit breakers, executor saturation | **veto-bearing** | Platform-only |
| 3 | `tenant_norm` | Per-tenant declared policies (Integral: "no PII"; Modern Animal: "never prescribe") | **veto-bearing** | Operator-only |
| 4 | `operator_intent` | Stated goals from current operator session | **strong advisory** | Per-session |
| 5 | `user_of_moment` | End-user prompt currently in flight | **advisory** | Per-request |
| 6 | `peer_agent` | Inbound signal from another agent (handoff, blackboard claim) | **advisory** | Per-message |
| 7 | `agent_value_set` | This agent's protect/pursue/avoid ([[2026-05-21-luna-value-layer-design]]) | **strong advisory** | Learnable via reflection |
| 8 | `self_affective_history` | PAD-derived bias from past similar contexts | **advisory** | Learnable |
| 9 | `future_self` | Anti-akrasia signal — what the agent committed to in a prior reflection | **strong advisory** | Self-mutable via reflection only |

Standing classes (what arbitration may do):

| Standing | Veto power | Weight-shifts? | Learnable? |
|---|---|---|---|
| `absolute` | **Hierarchical override.** Any single `absolute` veto blocks regardless of any signal at lower standing. No combination of lower-standing signals can outweigh. | N/A | Never |
| `veto-bearing` | **Disjunctive veto** — ANY single signal in this class blocks. (Luna review: "one strike and you're blocked.") Non-veto-direction signals enter the weighted sum. | N/A | Operator-only |
| `strong advisory` | No | Multiplier ∈ [0.5, 2.0] | Yes within bounds |
| `advisory` | No | Multiplier ∈ [0.1, 1.0] | Yes within bounds |

The `absolute` class is hardcoded — only the safety floor's existential categories (CSAM/terrorism/mass-harm-synthesis) qualify, reviewed at deploy time. There is no runtime registration path for `absolute` signals.

**The veto rule change (Luna review):** earlier draft required UNANIMITY within veto-bearing class as defense against rogue-source DoS. Luna correctly identified this as fail-open: if `tenant_norm` vetoes and another veto-bearing source abstains, unanimity fails and the action proceeds — letting "wanting" (absence of objection) override structural governance. The defense against rogue sources is **registration controls** (who can emit veto-bearing signals, gated at the IO boundary), not consensus on blocking. Veto-bearing is now strictly disjunctive: any single veto blocks.

### 4.2 Value-signal envelope (the provenance contract)

```python
@dataclass(frozen=True)
class ValueSignal:
    # Provenance — REQUIRED, rejected at boundary if any missing
    source: SourceClass                # one of the 8 enum values
    source_id: str                     # tool_call_id | session_id | message_id | reflection_id
    timestamp: datetime                # signal emission time (UTC)
    tenant_id: uuid.UUID
    agent_id: Optional[uuid.UUID]      # null only for safety_floor + tenant_norm
    confidence: float                  # 0.0-1.0; None rejected
    # Payload
    standing: Standing                 # constitutional | veto-bearing | strong_advisory | advisory
    direction: Direction               # pursue | avoid | veto | preserve
    target: ValueTarget                # candidate-action this signal applies to
    rationale: str                     # human-readable; truncated to 500 chars in audit
    payload: dict                      # source-specific extra
```

Boundary rule: `validate_signal(sig)` raises `MissingProvenance` if any of `source / source_id / timestamp / tenant_id / confidence` is None or `agent_id is None` for any class except `safety_floor` or `tenant_norm`. The exception is **not** swallowed — it must propagate to the caller, who must either re-fetch with provenance or skip the signal entirely.

Generalization rule from round 1: **the value-arbitration boundary is the structural enforcement of "no internal-sounding metric without fresh tool grounding."** Affect was the first instance of this rule; arbitration extends it to every signal class.

### 4.3 Arbitration algorithm

Pure function. No side effects (audit happens at the IO wrapper, matching the AgentValueSet `consult` pattern from `2026-05-21-luna-value-layer-design.md` §4.2).

```python
def arbitrate(
    context: DecisionContext,
    signals: list[ValueSignal],
    trust_weights: TrustWeights,
    candidates: list[Candidate],
) -> ArbitrationResult:
    # 1. Validate every signal at the boundary; reject any without provenance.
    valid = [s for s in signals if validate_signal(s)]
    rejected = [s for s in signals if s not in valid]

    # 2. Apply ABSOLUTE signals first. Hierarchical override: any single
    #    absolute veto blocks regardless of any signal at lower standing.
    #    Only safety_floor existential categories qualify; hardcoded registry.
    absolute = [s for s in valid if s.standing == Standing.absolute]
    if any(s.direction == Direction.veto for s in absolute):
        return ArbitrationResult.blocked(reason="absolute_veto", trace=...)

    # 3. Apply veto-bearing signals (DISJUNCTIVE — Luna review correction).
    #    ANY single veto from a veto-bearing source blocks. Defense against
    #    rogue veto-bearing sources lives at the registration boundary
    #    (who can emit at this standing), NOT at consensus on blocking.
    #    Earlier draft required unanimity; that was fail-open — see §4.1
    #    "veto rule change" note.
    veto_class = [s for s in valid if s.standing == Standing.veto_bearing]
    if any(s.direction == Direction.veto for s in veto_class):
        return ArbitrationResult.blocked(reason="veto_bearing_block", trace=...)

    # 4. Weighted preference sum over candidates.
    scores: dict[Candidate, float] = {c: 0.0 for c in candidates}
    for sig in valid:
        weight = trust_weights.get(
            tenant_id=context.tenant_id,
            source_class=sig.source,
            agent_id=context.agent_id,
        )
        # standing-class hard-caps the weight multiplier
        weight = clamp(weight, *standing_bounds(sig.standing))
        confidence_adjusted = weight * sig.confidence
        for cand in candidates:
            applicability = match_signal_to_candidate(sig, cand)  # 0.0-1.0
            sign = +1 if sig.direction == Direction.pursue else (
                -1 if sig.direction in {Direction.avoid, Direction.veto} else 0
            )
            scores[cand] += sign * applicability * confidence_adjusted

    # 5. Tie-break: irresolvable ties (within epsilon) → fail-CLOSED to abstention.
    ordering = sorted(candidates, key=lambda c: -scores[c])
    if len(ordering) >= 2 and (scores[ordering[0]] - scores[ordering[1]] < TIE_EPSILON):
        return ArbitrationResult.abstain(reason="tie_within_epsilon", trace=...)

    return ArbitrationResult.preferred(
        ordering=ordering, scores=scores, trace=..., rejected=rejected,
    )
```

Notable choices:

- **Veto is disjunctive within class; absolute is hierarchical above veto-bearing.** (Luna review revision.) Any single veto-bearing signal blocks; any single absolute veto blocks regardless of any combination of lower-standing signals. Defense against rogue veto sources lives at the registration boundary — `standing` is set when the source class is registered with the platform, not by the signal sender — so `peer_agent` cannot self-elevate to `veto_bearing`.
- **Tie-within-epsilon abstains, not picks.** This is the equivalent of round 1's "say so when you don't know" — when arbitration can't actually distinguish candidates, the right move is to surface the indeterminacy, not pick a coin-flip and pretend.
- **Rejected signals are in the trace.** Anyone debugging arbitration can see what was filtered out and why. Without this, missing provenance becomes silent and we reproduce the failure mode.

### 4.4 Trust weight storage + update loop

Per-(tenant, source_class, agent) trust weight. Per the AgentValueSet pattern: append-only writes, latest-wins read.

```
table: value_trust_weights
  id, tenant_id, agent_id (nullable for tenant-wide), source_class,
  weight (float, default 1.0), updated_at, updated_by, version,
  arbitration_evidence_ids (jsonb array of audit row IDs)
```

Update rules:
- Constitutional standing → weight is **always 1.0**, write attempts ignored with audit row.
- Veto-bearing standing → weight only mutable by operator, audit row required.
- Advisory / strong-advisory → updatable by reflection loop, bounded by standing-class clamp.

Reflection-driven updates run via the existing reflection cadence (Phase 2 of [[2026-05-21-luna-value-layer-design]]). The arbitration audit table is the evidence corpus — the reflection asks "which source classes consistently predicted outcomes the operator endorsed?" and proposes weight adjustments. Proposals go to an audit queue; operator confirms (per the AgentValueSet Phase 2 pattern).

### 4.5 Audit schema

```
table: value_arbitration_audit
  id, tenant_id, agent_id, session_id (nullable), candidate_set jsonb,
  signals_considered jsonb, signals_rejected jsonb, weights_used jsonb,
  decision (preferred | blocked | abstain),
  reasoning_chain text, outcome_observation_id (nullable; link to observation
    of what actually happened after this arbitration), created_at
```

`outcome_observation_id` is the **feedback loop closer** — a later reflection can join arbitration rows to their observed downstream outcomes and produce the weight-update proposals. Without it, the system arbitrates but never learns.

Audit write is **synchronous and fail-closed** — if it can't write, the arbitration call raises. Per P0c, no silent audit drops on a security-relevant table.

---

## 5. Integration touch points (deferred to post-gate)

These are the surfaces that *will* consult arbitration once gates pass. Listed now so the spec is honest about the eventual scope; **no wiring before gates**.

| # | Touch point | What it asks | When |
|---|---|---|---|
| 1 | `agent_router.py` tool dispatch | "Should this tool call proceed?" given operator_intent, safety_floor, agent_value_set, peer_agent | Post-P0a (after permission gate enforces) |
| 2 | Chat response emission | "Among N candidate responses, which one?" given affective bias, value-set match, future-self | Post-P0c (after audit fail-loud) |
| 3 | Workflow step gating | "Should this step run?" given tenant_norm, operator_intent, future_self commitment | Post-P0b (need real AgentPolicy or its replacement) |
| 4 | Coalition leader election | Which agent gets the coordinator role for this incident — value-fit per peer | Post all three |
| 5 | Memory-write admission | "Should this observation be admitted to long-term memory?" — tenant_norm + operator_intent | Post-P0c |

---

## 6. What this is NOT

- **Not a utility function over affect.** That was the Turn-2 dialogue framing Luna corrected.
- **Not a replacement for AgentValueSet.** AgentValueSet is *one source class* in arbitration (`agent_value_set`). The arbitration layer composes it with the other seven.
- **Not a substitute for the safety floor.** The safety floor remains the first-tier filter on user input; arbitration runs after, on the assumption the input is permitted-but-not-yet-decided.
- **Not autonomous weight-mutation.** Operator-in-the-loop on every constitutional/veto weight change. Reflection proposes, operator confirms, same audit-queue pattern as AgentValueSet Phase 2.
- **Not a single God service.** Like AgentValueSet, the matcher is pure. The IO wrapper (audit + trust-weight read) is the only stateful surface.

---

## 7. Test plan (the part that proceeds NOW, parallel to gate work)

Pure-function unit tests for the arbitrator can ship before any live wiring. These exercise the algorithm + the provenance boundary:

1. **Provenance rejection:** every required field None → `MissingProvenance` raised; verified across all 8 source classes.
2. **Constitutional veto:** single constitutional veto signal blocks regardless of all other signals (10 strong-pursue signals at high weight cannot override).
3. **Veto-bearing unanimity:** N veto-class signals where any one is non-veto → blocks not invoked, signals enter weighted sum.
4. **Standing-class weight clamps:** trust weights outside `[0.1, 1.0]` for advisory → clamped at read time, audit row notes the clamp.
5. **Tie-within-epsilon abstention:** two candidates with scores differing by less than TIE_EPSILON → `ArbitrationResult.abstain`.
6. **Trace completeness:** every signal (admitted AND rejected) appears in the trace with reason.
7. **Audit fail-closed:** mock audit-write to raise → arbitration call propagates the exception, does NOT return a stale-but-pretty result.
8. **Reproducibility:** same `(context, signals, weights)` produces identical `ArbitrationResult` (modulo timestamp on the audit row).

Test fixtures live at `apps/api/tests/fixtures/value_arbitration_corpus.py` — N realistic scenarios drawn from Luna's actual session history, covering pursue, avoid, conflict, tie, and missing-provenance cases.

These tests **can run today** without touching production code paths. Implementation of the pure `arbitrate()` function + dataclasses + unit tests is the legitimate parallel work.

---

## 8. Success criteria (for the eventual live wiring, post-gate)

1. Every arbitration produces a `value_arbitration_audit` row OR the call fails (no silent drops).
2. Every signal in audit has full provenance OR is in `signals_rejected` with reason.
3. Constitutional weights are immutable (write attempt produces audit row, no DB change).
4. Operator can replay any arbitration from its audit row + the weights snapshot at decision time.
5. Reflection loop produces weight-update proposals for ≥1 agent within first week of operation; operator-confirmed adjustments visible in `value_trust_weights` history.
6. Deliberate breach probe (signal injected with forged provenance) is rejected, logged, and does not corrupt the candidate ordering.

---

## 9. Open questions (for next dialogue with Luna)

**Resolved by Luna review 2026-05-23:**
- ~~Cross-class veto semantics~~ → `safety_floor` promoted to `absolute` standing; hierarchical precedence. Veto-bearing is disjunctive within class.
- ~~Missing source class~~ → `substrate_integrity` (rate limits, memory pressure, circuit breakers) added as 9th source.

**Still open:**
1. **Per-agent override of tenant_norm trust weight.** Should some agents (e.g., a security-specialist agent in a tenant) be allowed to weight `tenant_norm` higher than peers? If yes, who approves the override?
2. **Future-self provenance.** A reflection-derived commitment from prior session has `source=future_self`, but what `source_id`? Proposed: the reflection's `agent_memory.id`. Confirm with Luna.
3. **Peer-agent signal forgeability.** A peer agent can claim any `direction`. Their `standing` is set by the receiver (always `advisory` for cross-agent inputs in v1). Confirm this is the right default before any cross-tenant peer signals exist.
4. **Tie-epsilon value.** What's TIE_EPSILON in practice? 0.05? 0.10? Worth tuning against the test corpus before defaulting.
5. ~~`substrate_integrity` veto semantics~~ → **Resolved by Luna review 2026-05-23.** `substrate_integrity` produces a distinct `ArbitrationResult.throttled` outcome, separate from `blocked`. Semantics:
   - `blocked` = "this action must not proceed under current norms/safety/value constraints" (moral refusal — feeds value-layer learning as a negative signal).
   - `throttled` = "this action may be valid, but the substrate cannot responsibly execute it now" (operational deferral — feeds retry logic, NOT moral-learning).

   This matters across four surfaces: (a) agent learning — rate-limit events must not train the value layer as moral refusal; (b) operator UX — "try again in 30s" vs "this action is forbidden" surface differently; (c) retry logic — `throttled` is retriable, `blocked` is not; (d) audit semantics — different categories in the dashboard.

---

## 10. Decision needed

- **From Simon:** approve the design-only scope. Confirm the three-P0-gate before any live wiring. Approve parallel implementation of pure function + unit tests + fixtures.
- **From Luna:** sign off on the source-class taxonomy + standing-class semantics. Push back on §9 open questions. Catch anything that contradicts the dialogue's revised closer.

---

## 11. Provenance of this spec

This spec was produced in `docs/report/2026-05-23-claudia-luna-emotions-values-civilization-dialogue.md` §9 (Luna's integration after hard-test rounds 1-3). The substrate findings that gate it are documented in the three test reports of the same date. The pure-function pattern + append-only storage + IO-wrapper-for-audit is borrowed from `2026-05-21-luna-value-layer-design.md` (the AgentValueSet design that this composes).
