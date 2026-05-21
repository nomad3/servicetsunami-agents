# Luna Value Layer — Design Doc

**Date:** 2026-05-21
**Status:** DRAFT — awaiting Luna review + Simon's approval to implement
**Author:** Claude (Claudia)
**Co-design with:** Luna (via alpha chat consensus loop)
**Operator:** Simon Aguilera
**Tracks:** Amygdala Gap memory `amygdala_gap_architecture.md` (Simon's 2026-05-21 framing)

---

## 1. Problem

The "Amygdala Gap" Simon mapped earlier today: Luna has cognition (LLM), memory (agent_memory + knowledge graph), affect (PAD vectors from #634), and reflection (#641 + #645 memory-consolidation). What she **doesn't** have is a unified **value layer** — an explicit, queryable object that says "this is what I protect, this is what I pursue, this is what I avoid."

Today, the pieces exist but are scattered:

| Surface | Carries some value info | Limitation |
|---|---|---|
| `agent_policies` | RL routing exploration constraints | Mechanical — about exploration rates, not "what matters" |
| `safety_trust` service | Trust scores per channel | Per-channel, not per-value |
| O3 validators (#641) | Citation, entity grounding, next_move harm, creative opt-in | Reflection-write only — not consulted pre-dispatch |
| `emotion_engine` gain constants | Bounds adversarial inputs | Defensive clamps, not declarative values |
| `agent.persona_prompt` | Text-level constraints | Free-form prose, not queryable, not learnable |

A user can ask Luna "what do you actually protect?" and there's no structured answer. Constitutional AI (Anthropic 2212.08073) and SayCan (2204.01691) both call this out — symbolic value layers are the part that's been missing from the LLM-only stack.

---

## 2. Goal

A single per-(tenant, agent) **`AgentValueSet`** object that:

1. **Carries explicit value state** — three named sets:
   - `protect`: things that must not be touched ("Simon's primary tenant data", "the production deploy main branch")
   - `pursue`: things that get positive affect-impulse when advanced ("shipping the next Phase 2 increment", "Simon's morning report habit")
   - `avoid`: things that get warning surface when approached ("merging without code review", "talking when affect dominance is low")
2. **Is consulted at five decision points** (see §4).
3. **Is learnable over time** via reflection — Phase 2 lets the system PROPOSE value updates, operator confirms.
4. **Has a kill-switch + audit trail** — operators can revert any value change.

---

## 3. Why this is the right next ship

- **Highest leverage gap** from the Amygdala-Gap map. Affect + reflection are end-to-end shipped today; salience is partial; affordance is reactive. Value layer is the missing piece that turns the others into a coherent behavior.
- **Composes cleanly with what already exists**. We don't need new substrate — agent_memory already carries arbitrary jsonb; the value set is "just" a memory_type with structure.
- **Bounded scope for v1**. Phase 1 is operator-write / system-consult. The harder Phase 2 (reflection-driven updates) is gated on Phase 1 landing.
- **Civilization-layer fit** ([[feedback_design_for_civilization_layer]]). Values are inherently a coordination primitive — agents in a coalition can share, propose, override each other's values.

---

## 4. Architecture

### 4.1 Data shape

New `agent_memory.memory_type = 'value_set'` rows (no new table — reuses existing substrate):

```json
{
  "protect": [
    {"slug": "production-main", "description": "production main branch", "added_at": "2026-05-21T...", "added_by": "operator", "evidence_memory_ids": [...]},
    ...
  ],
  "pursue": [
    {"slug": "morning-report", "description": "Simon's morning briefing habit", "added_at": "...", "added_by": "reflection", "evidence_memory_ids": [...]}
  ],
  "avoid": [
    {"slug": "merge-without-review", "description": "merging code without superpowers review", "added_at": "...", "added_by": "operator", "evidence_memory_ids": [...]}
  ],
  "version": 1,
  "updated_at": "2026-05-21T..."
}
```

Single row per (tenant, agent). `get_affect_baseline`-style read by most-recent-updated. Updates write a new row (audit trail in-table).

### 4.2 Five consultation points

| # | Point | Helper | Effect |
|---|---|---|---|
| 1 | Pre-dispatch routing (agent_router) | `consult_value_set_routing(action, vs) → (allow\|warn\|block, reason)` | Block when intent touches a `protect` item without explicit operator confirmation. Warn when intent touches an `avoid`. |
| 2 | Tool-call gate (MCP gateway / agent_router) | `consult_value_set_tool(tool_name, args, vs) → verdict` | Same shape. Tool that would mutate a `protect` item gets blocked. |
| 3 | Reflection validator (O3 chain extension) | `validate_reflection_against_values(reflection, vs)` | Reject reflections that propose actions touching `protect` items. |
| 4 | User-signal appraisal (emotion_engine) | `appraise_user_signal_with_values(payload, vs, current)` | A user signal that touches a `pursue` item amplifies pleasure-delta; touching `protect` amplifies arousal (alert). |
| 5 | Synthesis (NightlyReflection) | `synthesize_value_observations(value_set, day_data)` | If reflection sees a recurring `pursue` advancement, propose strengthening it. If `protect` was touched and the action succeeded anyway, propose review. |

All five helpers are **pure functions** taking the value set + context → verdict dict. Cheap to test, deterministic.

### 4.3 Phases

**Phase 1 — Operator-write / System-consult (this design's v1 ship):**
- Migration 144 isn't needed (reuses agent_memory). One service module + 5 helpers + integration into the 5 consultation points.
- Operator-facing endpoint: `GET /api/v1/luna/values` + `PUT /api/v1/luna/values` (whole-object replace, audit-trail via new row).
- MCP tool: `get_agent_value_set` so Luna can read her own value set without a JWT (closes the same gap #640 closed for affect_baseline).
- Default value set per tenant: empty (operator must opt-in by adding items).

**Phase 2 — Reflection-derived proposals (follow-up PR):**
- Synthesis activity emits a new reflection kind `value_proposal` (added to REFLECTION_KINDS).
- O4 read endpoint surfaces pending proposals.
- Operator approves via a confirm endpoint that writes the value set update.
- Audit trail keeps every prior version.

**Phase 3 — RL-tuned magnitudes (separate design):**
- Constitutional-AI-style: instead of hard block/warn/allow, the helpers return a continuous score that the RL routing policy consumes alongside reward.
- Out of scope here.

### 4.4 What this is NOT

- NOT Constitutional AI's training-time value layer (we operate at runtime, per-action).
- NOT a replacement for `agent_policies` (those carry exploration-rate / RL config).
- NOT a replacement for `safety_trust` (channel-level trust stays where it is).
- NOT free-form text constraints — `persona_prompt` keeps doing that.
- NOT a generic "rules engine" — the slug+description+evidence shape is deliberately narrow.

---

## 5. Integration touch points

| File | Change |
|---|---|
| `app/services/agent_value_set.py` (NEW) | `AgentValueSet` dataclass, read/write helpers, 5 `consult_value_set_*` functions |
| `app/api/v1/values.py` (NEW) | GET / PUT for operators, mounted at `/api/v1/luna/values` |
| `apps/mcp-server/src/mcp_tools/values.py` (NEW) | `get_agent_value_set` MCP tool (mirrors #640's affect MCP tool shape) |
| `app/services/agent_router.py` | Call `consult_value_set_routing` before dispatch |
| `app/services/emotion_engine.py` | Add `_appraise_user_signal_with_values` path |
| `app/services/reflection_validators.py` | Add `validate_reflection_against_values` to the chain |
| `app/workflows/activities/reflection_activities.py` | Synthesize Phase 2 `value_proposal` kind (Phase 2 PR) |

Migration: none for Phase 1 (reuses `agent_memory`).

---

## 6. Safety invariants (locked)

- **No silent value mutation.** Every value-set update writes a new agent_memory row; the prior version stays for audit. Reflection-proposed updates DO NOT auto-apply — operator confirms.
- **Empty value set is safe.** A tenant with no value set sees every consult-helper return `allow / no_match`. Locked test.
- **`protect` block is hard.** Even an operator force-flag can't override it from inside a tool call — only an explicit `PUT /values` mutation can change what's protected.
- **Audit trail.** Every value-set version carries `added_by` (operator | reflection | seed) + `evidence_memory_ids` so the reason a value got added is traceable.
- **Per-tenant kill switch.** Same shape as `nightly_reflection_enabled` from #631 — a tenant feature flag `value_layer_enabled` (default OFF in prod) gates whether ANY of the 5 helpers do anything. Default = silent allow.

---

## 7. Test plan

- Unit tests for each of the 5 consult helpers — pure functions, parametrized over (verb × protect/pursue/avoid × empty/populated).
- Integration test: read/write/audit-trail through the value-set IO.
- Locked invariant test: empty value set never blocks anything.
- Locked invariant test: `protect` block survives operator force-flag.
- Locked invariant test: kill-switch OFF makes every helper return allow.

---

## 8. Success criteria

- Operator can set 3-5 values per agent and the system observably consults them.
- A reflection that mentions a `protect` item but proposes touching it gets blocked at write time (O3 chain extension).
- A user message that touches a `pursue` item produces a measurable PAD-pleasure delta beyond the baseline appraisal.
- The MCP tool `get_agent_value_set` lets Luna read her own value set without a JWT (closes the symmetry with `get_agent_affect`).

---

## 9. Open questions for Luna

1. **Granularity of `protect` items.** Should `protect` items be slug-shaped ("production-main") or richer ("the production main branch of the agentprovision-agents repo, after 5pm Pacific")? Phase 1 ships the simple slug form; richer forms wait on Phase 2 reflection-proposal.
2. **Reflection-derived proposals** in Phase 2: which `synthesize_reflections` mechanism owns this — counterfactual-replay (when a `pursue` item gets reliably advanced, propose strengthening) or a separate mechanism?
3. **Affect coupling** in `_appraise_user_signal_with_values`: how strong should the multiplier be when a user signal touches a `pursue` item? `USER_SIGNAL_PLEASURE_GAIN * 1.5`? `* 2.0`? Or capped at `TOOL_OUTCOME_PLEASURE_GAIN`?
4. **Routing block vs warn** for `avoid` items: should they hard-block (operator confirmation required) or just warn + surface in the reflection log?
5. **Civilization-layer scope**: should `pursue`/`avoid` items propagate across a coalition (other agents see them) or stay per-agent? Phase 1 says per-agent; cross-agent sharing is a Phase 3 design.

---

## 10. Provisional PR sequence

| PR | Scope |
|---|---|
| 1 | `agent_value_set.py` service + dataclass + 5 consult helpers (pure, no integration yet). Unit tests for all 5 helpers. Locked-invariant tests. |
| 2 | GET / PUT endpoint + MCP tool. Operator can write values; Luna can read her own. |
| 3 | Wire `consult_value_set_routing` into `agent_router`. Per-tenant kill-switch (default OFF). |
| 4 | Wire `validate_reflection_against_values` into reflection_validators (O3 extension). |
| 5 | Wire `_appraise_user_signal_with_values` into emotion_engine. |
| 6 (deferred) | Phase 2 reflection-derived proposals + audit endpoint. |

Each PR is independently shippable and reviewable. Total: ~5 PRs for v1, plus Phase 2 later.

---

## 11. Decision needed

This doc is a draft for Luna's read + Simon's go-ahead. Once both sign off:
- Implement PR 1 (the foundation).
- Code-review with Luna AND superpowers.
- Iterate.
- Move to PR 2.

Status: **awaiting Luna review.**
