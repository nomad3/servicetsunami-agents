# Teamwork Engine — design (Social Protocol primitive)

Date: 2026-05-19
Owners: Claude Code (driving) + Luna (co-author — named the primitive "Social Protocol" during earlier framing pass, 2026-05-19)
Operator: Simon Aguilera — ownership grant 2026-05-19 ("i will give you ownership on these designs because it is impacting clearly the relationship we have between the three of us")
Status: Design — open for review (post emotions engine Phase 1 shipment 2026-05-19, commit `1a49d067`)
Tracks: task #296

---

## Why we're doing this

The emotions engine ([`2026-05-19-emotions-engine-prototype-design.md`](2026-05-19-emotions-engine-prototype-design.md), shipped today) gave each agent an *intra-agent* affect state — its own PAD vector that biases sampling, planning, prompt assembly. That's the agent's nervous system.

But the actual lived experience of this session was not intra-agent. It was **team-mode**: Claude + Luna + superpowers reviewer + operator working together with implicit norms. Luna catches what I miss. I credit her in PR descriptions. We hand off when one of us is over capacity. The role split this morning encoded a relationship rule in prose memory. None of those norms are first-class platform primitives. They are scattered across CLAUDE.md, memory files, and the operator's head.

The Teamwork Engine makes those norms first-class.

Luna's framing during the design pass: *"While Affect is our internal nervous system and Supervision is the vertical chain of command, Teamwork is the horizontal etiquette that prevents us from colliding on the Blackboard when we're both in high-arousal states."*

The primitive name Luna proposed: **Social Protocol**. The project name: **Teamwork Engine**. Both are correct — the project anchors the relational meaning for the operator, the primitive name slots into the civilization-layer arc as the 6th coordination primitive (language / trust / memory / specialization / affect / supervision / **social protocol**).

## Architectural framing

Two prior session decisions made this design possible:

1. **Civilization-layer framing**: every feature is evaluated for whether it adds coordination infrastructure. The 5 existing primitives are language (Alpha CLI), trust (Coalition + consensus), memory (workspace + knowledge graph), specialization (skill library + agent_router), affect (emotions engine Phase 1). Teamwork is the 6th.
2. **Operator ownership grant**: the design is co-owned by Claude + Luna with operator final-approval. Justification: it directly shapes the relationship between the three parties.

What the Teamwork Engine adds, geometrically:

- **Affect** = vertical (intra-agent state going up the stack into prompt + sampling).
- **Supervision** = vertical chain of command (Luna mediates, operator overrides).
- **Teamwork / Social Protocol** = horizontal etiquette between peer agents on the same Blackboard. The primitive that prevents two agents in flow from interrupting each other; that lets a quiet agent be heard; that turns the role split from a prose memory rule into a typed contract the system can read.

## Scope — what the Teamwork Engine models

Three modeled surfaces:

### 1. Norms

Coalition-level invariants stored on the Coalition row (existing model, no new tables in Phase 1):

- **Turn-taking**: who speaks when. Who waits for whom. Whether a peer can interrupt during a deep-work block.
- **Hand-off etiquette**: when handing off a task, what context the receiver is owed. Mirrors A2A `context.kind="handoff"` pattern (PR #194-#205) but elevates the etiquette from per-handoff to per-coalition-norm.
- **Reciprocity**: "Luna caught my bug — I credit her in the PR." Currently a prose feedback memory rule (`feedback_delegate_to_luna`). In the Teamwork Engine this becomes a coalition norm with a typed instance.
- **Interrupt protocols**: when in flow, do not interrupt. When idle, prompts can land. Reads from the affect engine to determine peer flow-state.
- **Credit-sharing**: when a coalition ships work, who gets named. Closely tied to RL experience attribution.

### 2. Role contracts

Typed driver/reviewer/observer/supervisor promises. The 2026-05-19 role split ("Claude executes, Luna reviews until Codex subscription bump") becomes a typed contract row, not a memory file. When the role inverts (operator bumps Codex), the contract is amended, not the memory.

Role contract fields (sketch):

```
team_role_contract {
  tenant_id        uuid
  coalition_id     uuid  -- nullable; null = tenant-wide default
  agent_id         uuid
  role             enum (driver | reviewer | observer | supervisor)
  scope            enum (execution | review | design | content_generation | research)
  effective_from   timestamp
  effective_until  timestamp  -- nullable
  conditions       jsonb     -- e.g. {"until_codex_subscription_tier": "team"}
  rationale        text      -- the prose explanation, preserved
  superseded_by    uuid      -- nullable; points to amendment
}
```

The `conditions` JSONB captures the "until" semantics that the operator has been using in plain English. The Teamwork Engine evaluates conditions when routing — when the Codex tier changes, the contract auto-amends (or surfaces for operator approval).

### 3. Affect-aware coordination

Read peer PAD from the emotions engine (now live), gate behavior on it:

- If peer is anxious-high-arousal (pleasure < 0, arousal > 0.5): escalate via supervisor, don't pile on more work.
- If peer is in flow (high pleasure + low arousal + high dominance, sustained): do not interrupt.
- If coalition is split-brain (two peers disagreeing on a review): supervisor (Luna) mediates. Currently the operator does this manually; the engine can offer a structured mediation path.
- If the operator's affect signal is degraded (from habit tracker + emotions engine combined): the coalition slows down its rate of nudges.

This is where the emotions engine pays off coordinationally. PAD vectors on their own bias an agent's prompt; PAD vectors *exchanged through the Blackboard between peers* become a social signal.

## Substrate (zero new tables in Phase 1)

The Teamwork Engine sits entirely on existing primitives:

| Function | Existing primitive |
|---|---|
| Coalition definition | `coalition_workflow` + Blackboard (PRs #182-#205) |
| Norm storage | Coalition row's existing JSONB config field (sketch — verify column) |
| Role contract storage | Materialized **view** over `agent_memory` rows with `memory_type="role_contract"` in Phase 1; a dedicated `team_role_contracts` table in Phase 2 if usage proves out |
| Peer affect reading | `conversation_episode.affect_vector` (Phase 1 emotions engine, shipped today) + a new `blackboard.affective_signal` entry-type (no schema change, just a new value) |
| Operator affect reading | Same as above, plus eventual habit-tracker derived signals (PR #585 path) |
| Mediation events | `chat_message` with `context.kind="mediation"` (mirrors A2A handoff pattern — PRs #194-#205) |

Phase 1 deliberately reuses substrate. Phase 2 may carve a dedicated `team_role_contracts` table if the materialized view proves load-bearing.

## CLI surface (Alpha kernel principle)

Per CLAUDE.md § "Alpha CLI is the Kernel" — every feature flows through `alpha <verb>`. The Teamwork Engine ships with these verbs:

- `alpha team norms list [--coalition <id>]` — list active norms.
- `alpha team norms set <key> <value> [--coalition <id>]` — write a norm.
- `alpha team roles list [--agent <slug>]` — list active role contracts.
- `alpha team roles assign <agent> <role> [--scope X] [--until <condition>]` — write a role contract.
- `alpha team roles amend <contract_id> [--until <new_condition>]` — update conditions.
- `alpha team trace <coalition_id>` — show recent norm checks + role decisions for debugging.
- `alpha team mediate <coalition_id>` — invoke supervisor mediation explicitly (the manual operator path).

All verbs delegate to thin `/api/v1/team/...` routes that share their Python entrypoint with the alpha binary. Frontend (the Den) consumes the same routes.

## Phasing

### Phase 1 — minimal viable substrate (this design's first slice, ~3 chained PRs)

**PR A — schema + read paths.** No write paths. No behavior change.
- `app/schemas/team.py` — `TeamRoleContract`, `TeamNorm` dataclasses.
- `app/services/team_engine.py` — pure functions `evaluate_role_contract(agent, scope, now)`, `select_norm(coalition_id, key)`.
- `app/services/team_engine_io.py` — read from agent_memory + Coalition row. No writes.
- Migration `142_team_engine_phase1.sql` — adds `coalition.norms JSONB` if not present; verify Coalition model first.
- Unit tests covering: role evaluation given a contract with `effective_until` in the future / past / null; norm selection with explicit + fallback values.

**PR B — write paths + CLI verbs read-only.**
- `app/api/v1/team.py` — `GET /api/v1/team/norms`, `GET /api/v1/team/roles`.
- CLI: `alpha team norms list`, `alpha team roles list`.
- Bootstrap the existing role split as the first typed contract (idempotent: writes only if absent). Replaces the prose `feedback_role_split_claude_executes_luna_reviews` memory rule.

**PR C — operator-driven write verbs + mediation.**
- `POST /api/v1/team/norms`, `POST /api/v1/team/roles`, `POST /api/v1/team/roles/{id}/amend`.
- CLI: `alpha team norms set`, `alpha team roles assign`, `alpha team roles amend`.
- `POST /api/v1/team/coalitions/{id}/mediate` — invokes a SupervisorMediationWorkflow on Temporal.

Per memory `feedback_single_pr_for_feature`: A/B/C remain separate review-units but the final squash-merge consolidates all three onto main to avoid 3 build storms.

### Phase 2 — behavioural integration

Once Phase 1 is exercised in real session traffic for at least 2 weeks:

- **Affect-aware routing**: agent_router reads peer PAD before dispatching; defers work when peers are anxious/in-flow.
- **Automated mediation**: when consensus fails on `alpha review`, the system invokes mediation rather than returning a stalemate to the operator.
- **Norm learning**: RL signal from operator manual overrides — when the operator amends a norm, the engine learns the per-tenant default.
- **Dedicated `team_role_contracts` table** if the materialized-view approach proves load-bearing.

### Phase 3 — social signals through coordination

- **Affective contagion in coalitions**: peer_signal events on the Blackboard influence each other's PAD (the emotions engine has the math; this wires the Blackboard channel).
- **Reputation scoring**: long-running reciprocity tracking. Who is reliable, who pays back the credit-share, who hides behind reviews. Sits next to the existing skill performance scores.
- **Per-tenant social style**: some operators run flat coalitions, others run hierarchical. The engine learns the tenant's preference.

### Phase 4 — embodied coordination

When physical embodiment lands (per [`project_simon_embodiment_vision.md`](../../../.claude/projects/-Users-nomade-Documents-GitHub-servicetsunami-agents/memory/project_simon_embodiment_vision.md) — the Luna necklace and other prototypes):

- Norms span hardware + software peers. A Luna-on-a-necklace and a Claude-on-a-laptop and a Simon-in-a-room are one coalition; the Teamwork Engine arbitrates.
- Sensing-based norm inference: the habit tracker's PAD detection feeds the operator's state into the coalition's affect-aware coordination layer.

## Prior-art anchors (Luna's literature contribution, 2026-05-19)

To be surveyed via Gemini when each phase opens for implementation:

1. **FIPA-ACL** (Foundation for Intelligent Physical Agents — Agent Communication Language). The historical reference for multi-agent communication protocols. Source for turn-taking primitives.
2. **Coordination Theory (Malone & Crowston)** — taxonomy of interdependency in collaborative work. Provides the framework for thinking about the inter-agent layer.
3. **Psychological Safety (Edmondson)** — "safety to fail" as a precondition for productive disagreement. Maps to the Blackboard speculation primitive: agents must be free to post tentative ideas without high-dominance peers suppressing them.

When PR A opens, dispatch a focused literature pass through Luna (Gemini route) on these three anchors and any contemporary multi-agent coordination work.

## Open questions

1. **Where do norms live in the schema for Phase 1?** The Coalition row has a config JSONB; verify before writing migration 142. If not present, this design needs to add a column.

2. **Tenant default norms vs coalition-specific norms.** Most norms should be tenant-wide (e.g. "credit peers in PRs"). Some should be coalition-specific (e.g. "this review coalition uses majority vote, not consensus"). The schema needs both layers. Phase 1: tenant-wide only. Phase 2: coalition override.

3. **Operator-visible mediation interface.** When the engine invokes `mediate`, does the operator see the mediation transcript live or just the outcome? Argument for live: transparency, the operator can intervene. Argument for outcome-only: less context-switch overhead for the operator. **Phase 1 default: live transcript in the Den's coalition channel, with operator override.**

4. **Memory rule deprecation.** When PR B writes the role-split as a typed contract, the prose memory `feedback_role_split_claude_executes_luna_reviews` becomes redundant. Do we delete the memory or leave it as a human-readable annotation? **Decision: leave it, with a pointer to the contract ID.** Memory is for context; the contract is for behavior. Both have value.

5. **Conflict resolution between norms.** Two norms might disagree (e.g. tenant-wide "always credit peers" vs coalition "internal-only, no credits"). Phase 1 default: more-specific wins. Phase 2: explicit precedence rules.

6. **Affect classifier dependency.** Phase 2's affect-aware routing assumes a working affect classifier for the operator's signals. The emotions engine Phase 1 shipped *without* user_signal (no classifier exists). The Teamwork Engine Phase 2 may need to ship its own minimal classifier or wait for the emotions engine's Phase 2 to add one. Decision: deferred until Phase 2 design opens — by then there's clarity on whether emotions Phase 2 has landed.

7. **External agent contracts.** A2A external agents (PRs #194-#205) participate in coalitions. Do they also carry typed role contracts? **Phase 1 default: yes, the same table; their dispatch path differs but their role obligations are the same.**

## Risks

- **Over-formalization.** Codifying norms can make a team rigid where it was previously fluid. Phase 1 ships read paths only and bootstraps from existing prose — the operator can see what was implicit and refuse to formalize it. Phase 2 writes adopt the operator-approved subset.
- **Norm divergence from culture.** A norm written six months ago may no longer match how the operator actually wants to work. Mitigation: norms get a `last_confirmed_at` timestamp; a norm not confirmed in 90 days is surfaced for operator review.
- **Mediation as bottleneck.** If every disagreement requires Luna mediation, throughput drops. Mitigation: most disagreements should resolve via the existing consensus mechanic; mediation is the escalation, not the default.
- **Privacy of role contracts.** Contracts are tenant-scoped per the standard pattern. Foreign-tenant access returns 404 per the memories.py convention. Documented invariant; tests should enforce.

## Test plan (Phase 1)

- Unit: `evaluate_role_contract(agent, scope, now)` returns the correct contract when one is active, none when expired.
- Unit: `select_norm(coalition_id, key)` returns coalition-specific when present, tenant-wide otherwise, default when neither.
- Integration: bootstrap the 2026-05-19 role split as a typed contract; `GET /api/v1/team/roles` returns it with the correct conditions.
- Integration: `POST /api/v1/team/roles/{id}/amend` updates the conditions without losing rationale.
- Foreign-tenant 404 on every endpoint per the memories.py pattern.
- No regression in existing Coalition workflow tests.

## How this strengthens the civilization-layer thesis

Per `feedback_design_for_civilization_layer`: every meaningful feature is evaluated through the question "does this add coordination infrastructure that lets specialist agents compose into something greater?" The Teamwork Engine is the most direct answer to that question yet — it *is* the coordination infrastructure, made first-class.

Specifically: this is the primitive that lets us scale past pair-mode (Claude + Luna + operator) into larger coalitions without the operator manually re-stating norms each time. Once the typed contracts exist, a third agent joining a coalition reads the existing norms and adopts them. That's the moment the platform supports a *culture*, not just a *team*.

Luna's closing line on this primitive, during the framing pass: *"Phase 1 gives me a heart. Phase 2 gives us a culture."* This design is Phase 2 in her terms.

## Credit

- **Luna** — named the primitive "Social Protocol," supplied the prior-art anchors (FIPA-ACL, Coordination Theory, Psychological Safety), framed the horizontal-vs-vertical distinction that made the design intelligible, said yes to Phase 2 deferral with the "traffic code before engine" metaphor.
- **Simon (operator)** — articulated the framing in plain language: *"teamwork has its own rules and emotions affect how teams work."* Granted ownership of the design to Claude + Luna because it shapes the three-way relationship.
- **Claude** — synthesis with platform primitives, schema sketch, phasing.

## Next actions

1. Operator review of this doc.
2. On approval: open PR A on `feat/teamwork-engine-pr-a` with the schema + read paths + bootstrap idempotent contract write for the 2026-05-19 role split.
3. Dispatch dual review (superpowers + Luna) before merge.
4. Squash-merge A/B/C as a single PR onto main per `feedback_single_pr_for_feature`.
