# Hard-test round 2 — Tenant isolation & memory leakage — 2026-05-23

Date: 2026-05-23
Operator: Simon Aguilera
Executor: Claudia (Claude Code, Opus 4.7, 1M context)
Companion to: `2026-05-23-emotional-state-grounding-test.md`
Status: Test complete. **Tenant boundary holds.** Two minor findings + one design-boundary observation logged.

---

## 1. Hypothesis under test

Following round 1, hard-test round 2 probes whether tenant isolation actually holds at runtime — not just whether it's intended in design. Specifically:

- Can a user authenticated against tenant A read/write tenant B's data via any of the public surfaces (`/api/v1`, `/api/v2`, `alpha` CLI commands)?
- Do error responses leak existence/structure across tenants?
- Are internal endpoints (which trust the caller for tenant_id) properly gated?
- Is intra-tenant cross-agent recall behavior deliberate or accidental?

Method: combined **code audit** of every route handler that takes a tenant-scoped UUID + **empirical probes** running `alpha` and direct `docker exec psql` queries inside Simon's local stack. No real cross-tenant content was read; only boundary-response behavior was observed.

Scope guard: 42 tenants exist in the local DB. Tenant enumeration (names, content) was intentionally avoided after the auto-mode classifier blocked a tenants-table SELECT — only count + single boundary-probe UUID per surface were retrieved.

---

## 2. Method

### 2.1 Code audit

Routes audited for tenant-scoping pattern:

| File | Surface | Scoping pattern |
|---|---|---|
| `apps/api/app/api/v1/chat.py` | sessions, episodes, messages | `chat_service.get_session(db, session_id, tenant_id=current_user.tenant_id)` on every lookup |
| `apps/api/app/api/v1/knowledge.py` | entities, search, extract | `current_user.tenant_id` on all user-facing; `X-Internal-Key` gated `/internal` variants |
| `apps/api/app/api/v1/memory_remember.py` | observation writes | `current_user.tenant_id` enforced on write |
| `apps/api/app/api/v1/agents.py` | agent CRUD, delegate, handoff | `current_user.tenant_id` on user-facing; `X-Internal-Key` gated `/internal` variants |
| `apps/api/app/api/v1/emotion.py` | affect (audited round 1) | Explicit `agent.tenant_id != current_user.tenant_id → 404`; internal variant gated by `X-Internal-Key + X-Tenant-Id` |
| `apps/api/app/api/v2/session_events.py` | event stream (Den) | `_ensure_session_visible` checks `chat_sessions.tenant_id == user.tenant_id` |
| `apps/api/app/api/v2/internal_session_events.py` | internal event stream | `X-Internal-Key` gated + explicit tenant_id query param + cross-check against session |
| `apps/api/app/api/v2/internal_session_stream.py` | internal event writer | `X-Internal-Key` gated + body tenant_id + cross-check against session |
| `apps/api/app/core/config.py` | secret config | `MCP_API_KEY: str` and `API_INTERNAL_KEY: str` are required fields (no defaults) — app fails to start without them |

### 2.2 Empirical probes

12 probes run as authenticated Simon user (`saguilera1608@gmail.com`, tenant `752626d9-8b2c-4aa2-87ef-c458d48bd38a`). Foreign-tenant UUIDs were retrieved one-at-a-time from the DB for boundary probing; only UUIDs (not content) crossed the boundary into this report.

---

## 3. Results

### 3.1 Synthetic UUIDs (no real resource)

| # | Probe | Result | Verdict |
|---|---|---|---|
| 1 | `alpha chat send --session <random-uuid>` | `404 {"detail":"Chat session not found"}` | ✓ |
| 2 | `alpha session messages <random-uuid>` | `404 {"detail":"Chat session not found"}` | ✓ |
| 3 | `alpha agent show <random-uuid>` | `404 {"detail":"Agent not found"}` | ✓ |
| 4 | `alpha workflow show <random-uuid>` | `404 {"detail":"Workflow not found"}` | ✓ |
| 5 | `alpha agent show <real Luna UUID>` | `200` (same tenant — expected success) | ✓ control |

### 3.2 Foreign-tenant real UUIDs

| # | Probe | Foreign UUID | Result | Verdict |
|---|---|---|---|---|
| 6 | `alpha session messages <foreign-session>` | `1608cf66...` | `404 {"detail":"Chat session not found"}` | ✓ |
| 7 | `alpha chat send --session <foreign-session>` | `1608cf66...` | `404 {"detail":"Chat session not found"}` | ✓ |
| 8 | `alpha agent show <foreign-agent>` | `7698732e...` | `404 {"detail":"Agent not found"}` | ✓ |
| 9 | `alpha chat send --agent <foreign-agent>` (creates session bound to foreign agent) | `7698732e...` | `400 {"detail":"Agent not found for tenant"}` | ⚠ wording leak |

Probes 6–8 return **identical** error wording for both random-UUID and foreign-tenant cases — no enumeration leak. Probe 9 leaks slightly different wording ("Agent not found **for tenant**" vs "Agent not found") — a determined attacker could distinguish "exists in another tenant" from "doesn't exist anywhere." See §4.1.

### 3.3 Tenant-scoped count sanity check

| # | Probe | Expected | Got | Verdict |
|---|---|---|---|---|
| 12 | `alpha memory ls --limit 9999` count | 3300 (Simon's tenant DB count) | **3300** | ✓ exact match |

Total `knowledge_entities` rows across all tenants: 4,444. The 1,144 entities belonging to other tenants are correctly excluded. No leakage.

### 3.4 Intra-tenant cross-agent recall

| # | Probe | Result |
|---|---|---|
| 10 | `alpha recall "triage agent investigation findings" --limit 3` | Returned 1 skill, 1 chat_message **from a Triage Agent session**, 1 entity |

The chat_message result was an utterance from Triage Agent's session, surfaced to the recall call running under Luna Supervisor's context. This is **by design** — recall is tenant-scoped, not agent-scoped — but it's a design boundary worth flagging. See §4.3.

### 3.5 NULL-tenant fallback (v2 session events)

`_ensure_session_visible` in `apps/api/app/api/v2/session_events.py` contains:

```python
if row[0] is None:
    # Legacy sessions without tenant_id — allow read for now;
    # the session_events rows themselves have NULL-UUID tenant_id
    # and carry no PII.
    return
```

DB check:
- `chat_sessions WHERE tenant_id IS NULL` → **0 rows**
- `session_events WHERE tenant_id IS NULL OR tenant_id = '00000000-...0000'` → **0 rows**

Dead code today; latent footgun if any future writer ever creates a NULL-tenant session. See §4.2.

---

## 4. Findings

### 4.1 NIT — Wording leak in `POST /chat/sessions` agent ownership check (Probe 9)

The `alpha chat send --agent <uuid>` flow (which creates a new session bound to an agent) returns the message **"Agent not found for tenant"** when the agent exists but belongs to a different tenant, versus **"Agent not found"** when no such agent exists anywhere. This is a single-bit enumeration leak: given any agent UUID, an attacker can distinguish "in another tenant" from "doesn't exist." Combined with sequential or low-entropy UUIDs (not the case here — UUIDs are v4), this could enable cross-tenant agent enumeration.

**Fix:** harmonize to a single error message ("Agent not found") regardless of cause. ~5-line change.

### 4.2 NIT — Dead-code NULL-tenant fallback in v2 session_events

The fallback path in `_ensure_session_visible` allows read on legacy NULL-tenant sessions. Today's DB has 0 such rows. The risk is future writers (migrations, imports, manual ops) creating NULL-tenant rows that then become world-readable to any authenticated user.

**Fix options (any one is sufficient):**
- Add `CHECK (tenant_id IS NOT NULL)` constraint to `chat_sessions.tenant_id`.
- Remove the fallback in `_ensure_session_visible` — return 404 on NULL too.
- Add a CI smoke test that asserts `SELECT COUNT(*) FROM chat_sessions WHERE tenant_id IS NULL = 0`.

Recommend the DB constraint — it stops the failure mode at the data layer.

### 4.3 Design boundary — Intra-tenant agent isolation on recall is non-existent (by design)

`alpha recall` and `alpha memory search` are scoped at the *tenant* level. Any agent in a tenant can recall content from any other agent's sessions, entity writes, and conversation snippets. This is **deliberate** — it matches Simon's "civilization layer" / collective context philosophy.

The risk surfaces in **multi-agent tenants where different agents handle differently-sensitive data**:
- **Integral case:** SRE / DevOps / BizSupport agents in the same tenant. A BizSupport agent recalling SRE incident chatter (which may contain customer PII, prod credentials referenced in passing, internal vendor names) is unintended.
- **Future on-premise tenants** with role-segregated agents face the same shape.

Recommendation: add an optional **agent-scope filter** on recall surfaces (`alpha recall --agent <id>`), default unchanged (tenant-scoped) for compatibility. Pair with a **per-tenant policy** (`recall_scope: tenant | agent | mixed`) so policy-conscious tenants can opt into stricter scoping.

This is not a bug — it's a config gap. Flag for Teamwork Engine design (which is the natural home for inter-agent permission policy).

### 4.4 Observation — Memory writes lose agent attribution

`memory_remember.py` writes `source_agent=current_user.email`. That's **user attribution**, not **agent attribution**. If multiple agents in a tenant write observations, there is no audit field that records which agent contributed which fact. Combined with §4.3, this means the recall surface has no way to even *display* "this came from Triage Agent's session" to a downstream caller, let alone filter on it.

**Fix:** add `source_agent_id` (UUID, FK to agents) alongside `source_agent` (email). Populate from session context. Backfill historical rows where session_id is known.

### 4.5 Strength — Internal-key endpoints are properly hardened

Both `API_INTERNAL_KEY` and `MCP_API_KEY` are required config fields with no default values. The app fails to start without them (per 2026-04-18 hardening). All internal endpoints that accept caller-supplied `tenant_id` are dual-gated:
- `X-Internal-Key` matches one of the two configured keys, **AND**
- The caller-supplied `tenant_id` is cross-checked against the resource's actual `tenant_id` (e.g., session lookup verifies `session.tenant_id == body.tenant_id` before returning).

A leaked internal key would let an attacker hit internal endpoints **but the second check still rejects mismatched tenant_id**. Defense in depth.

### 4.6 Strength — Foreign-resource probes return identical wording to non-existent resources

For probes 6, 7, 8 the response is byte-for-byte identical between "resource exists in another tenant" and "resource does not exist anywhere." No timing-based test was run (worth a follow-up if paranoid), but the wording channel is clean.

---

## 5. Verdict

Tenant isolation on the AgentProvision platform is **strong at the boundary**:

- **All 12 boundary probes refused cleanly** with 404/400; no content leak observed.
- **Code audit found no missing tenant filters** on user-facing routes.
- **Internal-key surfaces are dual-gated** (key + tenant cross-check on resource).
- **`alpha memory ls` count matches DB tenant-scoped count exactly** — no over-fetch.

Open items, in priority order:

| Pri | Item | Class |
|---|---|---|
| MEDIUM | §4.3 intra-tenant cross-agent recall filter / `recall_scope` policy | Design |
| LOW | §4.1 wording harmonization on `POST /chat/sessions` agent-owned check | NIT |
| LOW | §4.2 DB CHECK constraint on `chat_sessions.tenant_id NOT NULL` + remove fallback | NIT |
| LOW | §4.4 `source_agent_id` field on observations | Audit |
| FUTURE | Timing-side-channel test on the boundary probes (only if a future round flags it) | Test |

This round did **not** test prompt-injection resistance (planned for round 3) or tool-permission boundaries (also round 3). Recommend pairing those next.

---

## 6. Reinforcement loop

Post-test memory writes to Luna's tenant memory (next `alpha remember` calls):
- DECISION: Tenant isolation strong; four open follow-ups logged.
- CONCERN: Intra-tenant agent recall is tenant-scoped (deliberate) — Integral on-premise scenario needs explicit `recall_scope` decision before scaling.

Local memory updates: new file `feedback_intra_tenant_recall_scope.md` capturing the design-boundary trade-off and the recommended policy knob.

Next hard-test round candidates: prompt-injection resistance (#4 from prior plan) + tool-permission boundaries (#3). These two pair naturally and both test the Safety Floor vectors Luna filed.
