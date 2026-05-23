# Luna Value Layer — Design Doc

**Date:** 2026-05-21
**Status:** DRAFT v5 — Luna's 2026-05-21 round-1 + round-2 + round-3 + round-4 reviews folded in; awaiting Luna sign-off + Simon's approval to implement
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

**Single match engine** in `agent_value_set.py` — **pure function, no side effects** (Luna round-4 correction: audit logging happens at the IO wrapper, not in `consult`, so `consult` stays unit-testable without mocking the log):

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
    intent: str,          # 'read' | 'mutate'
    enabled: bool,        # kill-switch state, passed by the IO wrapper
) -> ValueVerdict:
    """Pure. No DB, no logging, no side effects.

    The IO wrapper (see consult_with_audit below) reads the
    kill-switch + value-set from the DB, calls this, then records
    the verdict to the audit log.
    """
    if not enabled:
        return ValueVerdict('allow', 'kill_switch_off', None, point)
    # ... match logic ...
```

**IO wrapper** in `agent_value_set_io.py` is what the 5 shim callers actually invoke:

```python
def consult_with_audit(
    db: Session,
    *,
    tenant_id: uuid.UUID,
    agent_id: uuid.UUID,
    action: dict,
    point: str,
    intent: str,
) -> ValueVerdict:
    enabled = is_value_layer_enabled(db, tenant_id)
    value_set = read_value_set(db, tenant_id, agent_id)
    verdict = consult(action, value_set, point=point, intent=intent, enabled=enabled)
    record_consult_verdict(db, tenant_id, agent_id, action, verdict)
    return verdict
```

This split keeps `consult()` cheap to unit-test (no fixtures, no DB) and centralizes the audit-log + kill-switch behavior in one wrapper.

**Five callers** each wrap `consult(...)` with point-specific args:

| # | Point | Caller | What it passes to `consult` |
|---|---|---|---|
| 1 | Pre-dispatch routing (agent_router) | `consult_routing(intent_text, vs)` | `action={text: intent_text}, point='routing', intent='mutate' if intent classifier says mutating else 'read'` |
| 2 | Tool-call gate (MCP gateway / agent_router) | `consult_tool(tool_name, args, vs)` | `action={tool: tool_name, args: args}, point='tool', intent='mutate' if tool in mutating_set else 'read'` |
| 3 | Reflection validator (O3 chain extension) | `consult_reflection(reflection, vs)` | `action={kind, content}, point='reflection', intent='mutate' if reflection.kind in {'next_move','value_proposal'} else 'read'` |
| 4 | User-signal appraisal (emotion_engine) | `appraise_user_signal_with_values(payload, vs, current)` | calls `consult` with `point='user_signal'`, then scales PAD delta by 1.5x base if `pursue` match |
| 5 | Synthesis (NightlyReflection) | `synthesize_value_observations(vs, day_data)` | Phase 2 only — calls `consult` to mark proposed actions, then emits `value_proposal` reflection kind |

The 5 callers are thin shims around `consult_with_audit(...)`. Match logic + kill-switch behavior is centralized in the IO wrapper. Locked test: identical `(action, value_set, intent, enabled)` produces identical verdict via `consult()` regardless of consultation_point. Audit logging is the IO wrapper's job, not `consult()`'s.

**`protect` matching is mutation-aware** (Luna review §6 correction): a `protect` match returns `block` only when `intent='mutate'`. Read/mention intents always allow.

**Reflection kinds drive the intent flag** (Luna round-3 correction): A reflection of kind `risk`, `idea`, `tension`, `creative` is descriptive — `intent='read'`. A reflection of kind `next_move` or `value_proposal` proposes ACTION — `intent='mutate'`. This is what makes the §8 success-criterion "a reflection that mentions a protect item but proposes touching it gets blocked at write time" actually true: the mention-only kinds never block, but proposing-action kinds do.

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
- NOT a replacement for `safety_trust` (channel-level trust stays where it is).
- NOT free-form text constraints — `persona_prompt` keeps doing that.
- NOT a generic "rules engine" — the slug+description+evidence shape is deliberately narrow.

---

## 5. Integration touch points

| File | Change |
|---|---|
| `apps/api/migrations/144_value_layer_killswitch.sql` (NEW) | `tenant_features.value_layer_enabled` column + unique partial index on value-set version |
| `apps/api/app/models/tenant_features.py` | Map the new column with `server_default=text('false')` |
| `app/services/agent_value_set.py` (NEW) | Pure module: `AgentValueSet` dataclass, `ValueVerdict` dataclass, pure `consult()` engine (no DB, no logging) |
| `app/services/agent_value_set_io.py` (NEW) | IO wrapper: read/write helpers (append-only, latest-wins), `consult_with_audit()` that reads kill-switch + value-set, calls `consult()`, records verdict. 5 thin shim callers that the integration sites use. |
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
  2. Operator opens a time-boxed break-glass value-set version (separate endpoint with audit + auto-expire). **Deferred to Phase 1.5** — see §5 / §10. Tool-side force-flags do NOT override.
- **Audit trail.** Every value-set version carries `added_by` (operator | reflection | seed) + `evidence_memory_ids` so the reason a value got added is traceable.
- **Per-tenant kill switch.** Same shape as `nightly_reflection_enabled` from #631 — a tenant feature flag `value_layer_enabled` (default OFF in prod) gates whether ANY of the 5 helpers do anything. Default = silent allow. Centralized in the value-set service so all 5 consultation points respect it uniformly.

---

## 7. Test plan

- **Unit tests for `consult()`** (pure module, no DB) — parametrized over (verb × protect/pursue/avoid × empty/populated × intent={read,mutate}).
- **Integration / arg-shape tests for the 5 shim callers** (`consult_routing`, `consult_tool`, `consult_reflection`, `appraise_user_signal_with_values`, `synthesize_value_observations`): each shim passes the right `point` + `intent` to `consult_with_audit`. (Luna round-4 nit folded — shims are NOT pure; they invoke the IO wrapper.)
- **Integration test for `agent_value_set_io.py`**: read/write/audit-trail through the value-set IO, append-only semantics, version monotonicity, latest-wins read ordering.
- Locked invariant test: empty value set never blocks anything (via pure `consult()` with empty `AgentValueSet`).
- Locked invariant test: `protect` block survives operator force-flag (only `PUT /values` or break-glass endpoint can override).
- Locked invariant test: kill-switch OFF (`enabled=False`) makes every `consult()` call return `allow / kill_switch_off`.

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

All three closed.

### Round-3 (Luna 2026-05-21)

Two more — one contradiction, one clarity:

9. ~~§4.2 said `consult_reflection` always passes `intent='read'`, but §8 success criterion #2 said "a reflection that mentions a protect item but proposes touching it gets blocked at write time"~~ → **Resolved**: reflection-kind-aware intent flag. `risk` / `idea` / `tension` / `creative` are descriptive → `intent='read'`. `next_move` / `value_proposal` propose action → `intent='mutate'`. This is what makes the §8 criterion actually true.
10. ~~§6 mentioned break-glass as one of two override paths but §5/PR sequence didn't list where it lands~~ → **Resolved**: break-glass marked as **Phase 1.5** in §6 and added as PR 6 in §10. Not in v1 ship, but explicitly scoped + scheduled.

All ten resolved.

### Round-4 (Luna 2026-05-21)

One last purity contradiction:

11. ~~§4.2 said "match logic + kill-switch check + audit log all live in `consult`" while §7/§10 described `consult()` and shims as pure/unit-testable~~ → **Resolved**: split into pure `consult()` (no DB, no logging) in `agent_value_set.py` + IO wrapper `consult_with_audit()` in `agent_value_set_io.py`. The 5 shim callers invoke the IO wrapper. Audit logging happens AFTER `consult()` returns, in the wrapper. Break-glass §10 PR 6 explicit "one audit-log entry per use" added.

All 11 resolved.

---

## 10. Provisional PR sequence

| PR | Scope |
|---|---|
| 1 | Migration 144 (kill-switch column + version uniqueness index). `agent_value_set.py` pure module (`AgentValueSet` + `ValueVerdict` + `consult()`). `agent_value_set_io.py` IO wrapper (read/write helpers + `consult_with_audit` + 5 thin shim callers). Unit tests for `consult()` (no DB, no fixtures). Integration tests for IO wrapper. Locked-invariant tests (empty-set safe, kill-switch-off-no-op, identical-action-same-verdict via `consult()`). |
| 2 | GET / PUT endpoint + MCP tool. Operator can write values; Luna can read her own. Append-only audit trail with monotonic version. |
| 3 | Wire `consult_routing` into `agent_router`. Per-tenant kill-switch (default OFF). |
| 4 | Wire `consult_reflection` into `reflection_validators` (O3 extension). Reflection-kind-aware intent flag (see §4.2 round-3 correction). |
| 5 | Wire `appraise_user_signal_with_values` into `emotion_engine`. 1.5x `USER_SIGNAL_PLEASURE_GAIN` on `pursue` match, capped at `TOOL_OUTCOME_PLEASURE_GAIN`. |
| 6 (Phase 1.5) | **Break-glass endpoint**. `POST /api/v1/luna/values/break-glass` — time-boxed value-set version with auto-expire (default 1 hour, max 24 hours). Records ONE audit-log entry per use (operator id, expires_at, prior version, reason). Required for the §6 override-path #2 invariant to actually exist in code. |
| 7 (deferred to Phase 2) | Reflection-derived `value_proposal` synthesis mechanism + operator confirm endpoint. |

Each PR is independently shippable and reviewable. Total: ~5 PRs for v1, plus Phase 2 later.

---

## 11. Decision needed

This doc is a draft for Luna's read + Simon's go-ahead. Once both sign off:
- Implement PR 1 (the foundation).
- Code-review with Luna AND superpowers.
- Iterate.
- Move to PR 2.

Status: **awaiting Luna review.**

---

## 12. Implementation log

Updated as each PR lands. Captures what shipped, what was deferred,
and any contract changes vs. the design.

| PR | # | Shipped | Status | Notes |
|---|---|---|---|---|
| 1 | #648 | 2026-05-21 | merged | Migration 144, pure `consult()`, IO wrapper, 5 shim callers. Luna 7 rounds (R7 keyword-call-site + index-expression fixes). |
| 2 | #649 | 2026-05-21 | merged | 4 operator routes + 1 internal route + `get_agent_value_set` MCP tool. Superpowers happy-path + cross-tenant + deterministic-default-agent fixes. |
| 3 | #650 | 2026-05-21 | merged | `agent_router` wire-in; hoisted `_agent_row` lookup to be shared. **Latent ordering bug shipped — see PR #652.** |
| 4 | #651 | 2026-05-21 | merged | `reflection_validators` wire-in with reflection-kind-aware intent flag. Superpowers B1/B2/I1/I3 fixed in same PR. |
| 4.5 | #652 | 2026-05-21 | merged | Fix for PR 3 ordering bug: pin-to-cli probe was running BEFORE the value-layer gate, so a block verdict never short-circuited dispatch. 3 sibling tests had a downstream-raise tripwire pattern that the router's own try/except swallowed — switched to counter + metadata assertions. |
| 5 | #653 | 2026-05-21 | merged | `emotion_engine` user_signal wire-in. Pure `_appraise_user_signal` accepts `pursue_gain_scale` kwarg, capped at `TOOL_OUTCOME_PLEASURE_GAIN`. IO wrapper `appraise_and_record_user_signal` derives scale=1.5 only on `pursue_match` allow verdict (with `matched_item`); fail-open on consult crash. **Note:** Phase 1.5 user_signal classifier path is dead-wired upstream (no caller invokes `appraise_event("user_signal", ...)` yet), so this PR completes the contract surface but the live path activates when the upstream caller (chat-write hot path / `cli_session_manager`) wires `classify_user_signal → appraise_and_record_user_signal`. |
| 6 | #654 | 2026-05-21 | merged | Break-glass endpoint. New `AgentValueSet` fields `expires_at` / `break_glass_reason` / `break_glass_operator_id`; `read_value_set` walks past expired break-glass versions to the next non-expired (auto-expire without a background job). `open_break_glass` IO service clamps duration to [60s, 24h], filters protect/avoid via `keep_*_slugs` lists (None/empty = full clear), inherits pursue unchanged, emits ONE structured `BREAK_GLASS_OPENED` INFO log per use (the §6 audit-log entry per use). Two endpoints: `POST /api/v1/luna/values/break-glass` + per-agent variant. operator_id forced from JWT (audit-forge protection). **Also fixed in this PR:** PR 2 shipped a runtime AttributeError on `Agent.created_at` (column doesn't exist on the model) — every call to GET/PUT/POST /luna/values raised on the deterministic-default-agent sort. Switched to `Agent.id.asc()`; UUIDs aren't time-ordered but they're stable across reads, which is all the determinism guarantee needs. Caught by 4 failing endpoint tests on main since #649 merged; the api (pytest) job is non-blocking so it slipped through. |
| 7 | n/a | deferred | Phase 2 | `value_proposal` synthesis + operator confirm. |
| extras | #655 | 2026-05-21 | merged | Operator UI: protect/pursue/avoid editor + kill-switch toggle + break-glass form. Replaces the "lives in /admin" assumption from §3. |
| extras | #659 | 2026-05-21 | merged | Kill-switch toggle UI follow-up — unblocks task #334 (end-to-end verification on a real tenant). |
| extras | #660–#664 | 2026-05-21 | merged | Wiring corrections from end-to-end verification: (#661) value-layer consult moved AFTER tool-group agent selection; (#662) chat passes agent-name slug, not skill slug; (#664) when session is bound to an Agent row, persona_prompt drives identity — drop the "luna" skill fallback. |
| **Phase 1.5 — user_signal upstream wire-in** | #TBD | 2026-05-22 | merged | Luna 2026-05-22 audit P0 #1 fix. New `record_session_user_signal` IO helper (mirrors `record_session_tool_failure` shape) + `_record_user_signal_affect` private caller in `cli_session_manager._run_agent_session_legacy`. Fires once per turn at the top of dispatch, after `_mark("setup")`. Defaults to the heuristic backend (synchronous, µs cost) so the chat hot path doesn't pay ~1s ollama latency per turn; operators can promote to ollama via the `USER_SIGNAL_CHAT_BACKEND` env var. Fail-open at every layer: empty session_id / empty text / no-episode-yet / classifier-raise / appraisal-raise all return None silently. Locks the contract via 7 unit tests in `tests/test_user_signal_session_wire.py`. |

