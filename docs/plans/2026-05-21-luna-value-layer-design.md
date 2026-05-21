# Luna Value Layer — Design Doc

**Date:** 2026-05-21
**Status:** DRAFT v3 — Luna's 2026-05-21 round-1 + round-2 reviews folded in; awaiting Luna round-3 sign-off + Simon's approval to implement
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
- **Composes cleanly with what already exists**. We don't need a new TABLE for the value-set body — `agent_memory.content` is `Text`, so the value set is JSON-serialized (same pattern as metacog rows + reflection rows). A small migration adds a tenant-features kill-switch column — see §4.3.
- **Bounded scope for v1**. Phase 1 is operator-write / system-consult. The harder Phase 2 (reflection-driven updates) is gated on Phase 1 landing.
- **Civilization-layer fit** ([[feedback_design_for_civilization_layer]]). Values are inherently a coordination primitive — agents in a coalition can share, propose, override each other's values.

---

## 4. Architecture

### 4.1 Data shape

New `agent_memory.memory_type = 'value_set'` rows. `agent_memory.content` is `Text`, so we **JSON-serialize on write, deserialize on read** — same pattern as metacog rows (#617) and reflection rows (O1). Body shape:

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

**Append-only store with latest-wins read** (Luna round-2 correction):

- Each value-set mutation **writes a NEW** `agent_memory` row with `memory_type='value_set'` and a monotonically increasing `version`.
- Reads pick the **most recent valid row** for (tenant_id, agent_id), ordered by `updated_at DESC, created_at DESC`. Same shape as `get_affect_baseline`'s most-recent-updated read.
- The prior rows stay in place — they're the audit trail. No UPDATE-in-place, ever.
- `version` is monotonic per (tenant_id, agent_id): each write bumps it from the prior max version. Concurrent writers may collide on the same target version; the DB unique-index constraint (added in migration 144 — see §4.3) detects the collision and the writer retries with version+1. Phase 1 is operator-write only, so the collision rate is negligible.

### 4.2 Five consultation points — centralized matching, distributed callers

Luna round-1 review (load-bearing call): "Keep all 5 consultation points, but concentrate implementation in ONE service and ONE verdict schema." Fanout is correct architecturally; five separate matchers would be the mistake.

**Single match engine** in `agent_value_set.py`:
```python
@dataclass(frozen=True)
class ValueVerdict:
    decision: str           # 'allow' | 'warn' | 'block'
    reason: str             # human-readable
    matched_item: Optional[dict]  # which protect/pursue/avoid item triggered
    consultation_point: str # routing | tool | reflection | user_signal | synthesis

def consult(
    action: dict,
    value_set: AgentValueSet,
    *,
    point: str,
    intent: str = 'read' | 'mutate',
) -> ValueVerdict: ...
```

**Five callers** each wrap `consult(...)` with point-specific args:

| # | Point | Caller | What it passes to `consult` |
|---|---|---|---|
| 1 | Pre-dispatch routing (agent_router) | `consult_routing(intent_text, vs)` | `action={text: intent_text}, point='routing', intent='mutate' if intent classifier says mutating else 'read'` |
| 2 | Tool-call gate (MCP gateway / agent_router) | `consult_tool(tool_name, args, vs)` | `action={tool: tool_name, args: args}, point='tool', intent='mutate' if tool in mutating_set else 'read'` |
| 3 | Reflection validator (O3 chain extension) | `consult_reflection(reflection, vs)` | `action={kind: reflection.kind, content: reflection.content}, point='reflection', intent='read'` |
| 4 | User-signal appraisal (emotion_engine) | `appraise_user_signal_with_values(payload, vs, current)` | calls `consult` with `point='user_signal'`, then scales PAD delta by 1.5x base if `pursue` match |
| 5 | Synthesis (NightlyReflection) | `synthesize_value_observations(vs, day_data)` | Phase 2 only — calls `consult` to mark proposed actions, then emits `value_proposal` reflection kind |

The 5 callers are thin shims; the match logic + kill-switch check + audit log all live in `consult`. Locked test: identical `(action, value_set)` produces identical verdict regardless of consultation_point.

**`protect` matching is mutation-aware** (Luna review §6 correction): a `protect` match returns `block` only when `intent='mutate'`. Read/mention intents always allow.

### 4.3 Phases

**Phase 1 — Operator-write / System-consult (this design's v1 ship):**
- **Migration 144** adds `tenant_features.value_layer_enabled BOOLEAN NOT NULL DEFAULT FALSE` (Luna round-2 correction — same shape as migration 142 for `nightly_reflection_enabled`). Idempotent (`ADD COLUMN IF NOT EXISTS`). `TenantFeatures` model + `server_default=text('false')`. The value-set body itself reuses `agent_memory` with no schema change.
- **Migration 144 also** adds a unique partial index on `(tenant_id, agent_id, (content::jsonb->>'version'))` for `memory_type='value_set'` rows so concurrent writers collide cleanly (see §4.1 append-only semantics).
- One service module + 1 centralized `consult()` + 5 thin shim callers + integration into the 5 consultation points.
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
| `apps/api/migrations/144_value_layer_killswitch.sql` (NEW) | `tenant_features.value_layer_enabled` column + unique partial index on value-set version |
| `apps/api/app/models/tenant_features.py` | Map the new column with `server_default=text('false')` |
| `app/services/agent_value_set.py` (NEW) | `AgentValueSet` dataclass, read/write helpers (append-only, latest-wins), single `consult()` engine + verdict schema, 5 thin shim callers |
| `app/api/v1/values.py` (NEW) | GET / PUT for operators, mounted at `/api/v1/luna/values` |
| `apps/mcp-server/src/mcp_tools/values.py` (NEW) | `get_agent_value_set` MCP tool (mirrors #640's affect MCP tool shape) |
| `app/services/agent_router.py` | Call `consult_routing` shim before dispatch |
| `app/services/emotion_engine.py` | Add `appraise_user_signal_with_values` path (calls `consult` with `point='user_signal'`, scales PAD delta) |
| `app/services/reflection_validators.py` | Add `consult_reflection` shim to the O3 chain |
| `app/workflows/activities/reflection_activities.py` | Synthesize Phase 2 `value_proposal` kind (Phase 2 PR) |

Migration: **144 (one migration)**. Adds the kill-switch column + the value-set version uniqueness index. The value-set BODY reuses `agent_memory.content` (Text, JSON-serialized) — no new table.

---

## 6. Safety invariants (locked)

- **No silent value mutation.** Every value-set update writes a new agent_memory row; the prior version stays for audit. Reflection-proposed updates DO NOT auto-apply — operator confirms.
- **Empty value set is safe.** A tenant with no value set sees every consult-helper return `allow / no_match`. Locked test.
- **`protect` blocks MUTATION, not mention.** A tool call that would *mutate* a protected item is impossible from inside the tool layer. Reading, mentioning, or referencing a protected item in chat / reflection content is FINE — otherwise Luna deadlocks around the very things she's supposed to safeguard. (Luna round-1 review correction.)
- **Override paths for `protect` mutation are explicit and bounded.** Two only:
  1. Operator does an explicit `PUT /values` to remove the item.
  2. Operator opens a time-boxed break-glass value-set version (separate endpoint with audit + auto-expire). Tool-side force-flags do NOT override.
- **Audit trail.** Every value-set version carries `added_by` (operator | reflection | seed) + `evidence_memory_ids` so the reason a value got added is traceable.
- **Per-tenant kill switch.** Same shape as `nightly_reflection_enabled` from #631 — a tenant feature flag `value_layer_enabled` (default OFF in prod) gates whether ANY of the 5 helpers do anything. Default = silent allow. Centralized in the value-set service so all 5 consultation points respect it uniformly.

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

## 9. Resolved questions

### Round-1 (Luna 2026-05-21)

1. ~~Granularity of `protect` items~~ → **Resolved**: simple slug shape for v1. No time/scope conditional reasoning in v1. Richer forms wait for Phase 2 reflection-proposal.
2. ~~Reflection-derived proposals owner~~ → **Resolved**: **dedicated `value_proposal` synthesis mechanism**, NOT counterfactual-replay. Luna's reasoning: "Values are governance state, not just success inference." The value_proposal mechanism CAN consume counterfactual-replay evidence, but ownership stays separate.
3. ~~Affect multiplier for `pursue` touches~~ → **Resolved**: **`1.5x USER_SIGNAL_PLEASURE_GAIN`**, capped at `TOOL_OUTCOME_PLEASURE_GAIN`. Current numbers: `1.5 * 0.15 = 0.225`, below `TOOL_OUTCOME_PLEASURE_GAIN = 0.30`. Luna's reasoning against 2.0x: "exactly equals tool success and makes user text too dominant."
4. ~~`avoid` routing: block or warn~~ → **Resolved**: **warn-only + log for Phase 1**. Luna's reasoning: "Hard-blocking `avoid` will create false positives and operator fatigue." Tighten in Phase 2 based on observed warning rate and confirmed value-proposal evidence.
5. ~~Cross-agent value propagation~~ → **Resolved**: **per-agent only in Phase 1**. Phase 2 adds *read-only* coalition visibility (other agents can SEE peer value sets) BEFORE Phase 3 mutation/sharing. Cross-agent value propagation is civilization-layer work and risks values bleeding across roles if rushed.

All five open questions closed by Luna round-1.

### Round-2 (Luna 2026-05-21)

Three structural ambiguities Luna pushed back on after reading v2:

6. ~~§3 said agent_memory carries arbitrary JSONB~~ → **Resolved**: `agent_memory.content` is `Text`. v3 says JSON-serialized content, same pattern as metacog (#617) and reflection rows.
7. ~~§4.1 "Single row per (tenant, agent)" vs "Updates write a new row" contradiction~~ → **Resolved**: explicit append-only with latest-wins read (`updated_at DESC, created_at DESC`), monotonic `version` per (tenant, agent). DB unique-index detects collisions; writer retries with version+1.
8. ~~§4.3/§5 said no migration; §6 introduces tenant_features.value_layer_enabled~~ → **Resolved**: migration 144 added explicitly. Adds the kill-switch column + the value-set version uniqueness index. Mirrors migration 142 shape.

All three closed. Round-3 ask: re-read with these baked in; sign off on v3 if no further corrections.

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
