# P0b — AgentPolicy: wire it or delete it

**Date:** 2026-05-23
**Status:** SCOPE — recommendation pending operator decision
**Author:** Claudia (Claude Code, Opus 4.7)
**Operator:** Simon Aguilera
**Surfaces:** `docs/report/2026-05-23-prompt-injection-tool-permission-test.md` §2.2 (dead-infra finding), `apps/api/app/models/agent_policy.py`, `apps/api/app/api/v1/agent_policies.py`, `alpha policy` CLI subcommand

---

## 1. The finding (recap)

Round 3 of the 2026-05-23 hard-tests audited the `AgentPolicy` model and the surfaces that consume it. Result:

- Model defines 4 `policy_type` values: `input_filter`, `output_filter`, `data_access`, `rate_limit`.
- **Zero enforcement call sites.** Grep across `apps/api/app/services/`, `workflows/`, `api/` returns no references outside the model file's docstring.
- The only consumer is `apps/api/app/api/v1/agent_policies.py` — a read-only viewer that lists rows.
- The `alpha policy` CLI surface reads from it.
- **Production row count: 0 across all 42 tenants.**

The CLI returns empty arrays, operators see no enforcement, and the table is unused. False-comfort: a "policy" surface exists with no operational effect.

---

## 2. Two paths

### Path A — Wire it

Add runtime enforcement for each `policy_type`. Each requires a different hot-path integration:

| policy_type | Enforcement hot path | Difficulty |
|---|---|---|
| `input_filter` | `agent_router.py` ingress, alongside the existing `platform_safety_io.consult_with_audit` call | LOW — adds a per-tenant regex/classifier check; mirrors the safety floor pattern |
| `output_filter` | Chat response emission path (e.g., `stream_chat_completion` finalizer) | MEDIUM — needs to either block or redact pre-send; streaming complicates this |
| `data_access` | DB query layer or per-tool DB-touching handler — needs SQL-level pattern matching | HIGH — requires a query-aware proxy or per-tool denial; conflicts with the schemaless `text(...)` paths in v2 session events |
| `rate_limit` | Per-(tenant, user) counter on tool dispatch | MEDIUM — duplicates the per-endpoint `core.rate_limit.limiter` (slowapi-style); needs to decide which is authoritative |

Plus: write tests, default-policy seeding for new tenants, operator UI for editing, audit table for policy changes, migration to ensure existing tenants get defaults.

Estimated scope: **2-3 weeks** for all four policy_types with reasonable test coverage. `data_access` alone is a week of design work.

### Path B — Delete it

Remove the model, the API endpoint, the CLI subcommand. Defer all four policy concerns to better-fit substrates:

| Concern | Better-fit substrate | Status |
|---|---|---|
| `input_filter` | Platform Safety Floor (`platform_safety_io.consult_with_audit`) | Exists; needs multimodal extension (Luna vectors #345/#351) |
| `output_filter` | Value Arbitration Layer (`standing=tenant_norm`, `direction=avoid`) | Designed in [[2026-05-23-value-arbitration-design]]; pending gate |
| `data_access` | Value Arbitration Layer (`standing=tenant_norm`, target=DB-touching tools) | Same — Integral case is the motivating example |
| `rate_limit` | `core.rate_limit.limiter` (slowapi-style, already enforced per-endpoint) | Operational today; if per-agent granularity is needed later, add to the limiter, not AgentPolicy |

Estimated scope: **half a day** to delete the model, the API, the CLI subcommand, drop the table in a migration, and update the design docs that reference it.

---

## 3. Recommendation: **Path B — delete it**

Reasons in order of weight:

### 3.1 Zero production usage

Across 42 tenants and ~1 year of operation, the table has 0 rows. Nobody created a policy. No operator workflow depends on it. No CLI consumer is parsing its output. There is no migration cost — there's no data to migrate.

### 3.2 Wiring it would create a fourth policy substrate

The platform already has:
- `platform_safety_io` (input screening, tier-1/2/3)
- `agent.tool_groups` (tool-permission gating — being fixed in P0a)
- `core.rate_limit.limiter` (per-endpoint rate limiting)
- *(designed)* `ValueArbitration` (plural value reconciliation with standing-classes)

`AgentPolicy` would be a fifth, overlapping each of the above. The `value-layer-design` doc explicitly notes (§4 line 20): *"`agent_policies` carries RL routing exploration constraints — mechanical, about exploration rates, not 'what matters.'"* That historical scope drift means nobody quite knew what AgentPolicy was for.

The Value Arbitration design ([[2026-05-23-value-arbitration-design]]) handles every concern AgentPolicy was supposed to handle, plus the cross-class reconciliation problem AgentPolicy can't handle, plus the provenance discipline round 1 surfaced. Wiring AgentPolicy now means migrating it into ValueArbitration later. Two migrations for the same result.

### 3.3 The known future use case fits ValueArbitration better

The `docs/research/2026-05-09-modern-animal-harriet-sierra.md` doc proposes using AgentPolicy for the Pet Health Concierge: *"hard rules ('never prescribe', 'always check VCPR', 'always offer to book if symptom severity > X') belong in code, not in prose the LLM might paraphrase. Lift `agent_policies` (already in ALM, migration 097) and add **pre-response policy enforcement**."*

This is **exactly** the use case ValueArbitration's `tenant_norm` standing-class is designed for:
- "never prescribe" → `standing=tenant_norm, direction=veto, target=ResponseAction(suggests_prescription=True)`
- "always check VCPR" → `standing=tenant_norm, direction=pursue, target=ToolAction(name=check_vcpr_status)`

ValueArbitration handles the *arbitration* dimension AgentPolicy can't: what happens when "always check VCPR" conflicts with a user pressuring for a fast response. AgentPolicy would just say "block" — arbitration produces a reasoned trace and the audit row to defend it.

Modern Animal hasn't shipped against AgentPolicy yet. Building against ValueArbitration from the start avoids the migration.

### 3.4 False-comfort is worse than absence

Operators who see `alpha policy` return rows assume the platform is enforcing them. Round 3 proved no agent is gated by AgentPolicy. Keeping the CLI surface alive while the enforcement is absent is a security-shaped lie. Either fix it or remove it — leaving it as-is propagates the lie further.

### 3.5 The deletion is small and reversible

The Path-B scope below shows the full removal surface. Migration to drop the table is a single `DROP TABLE agent_policies CASCADE` (CASCADE because foreign keys reference `tenants(id)` only, which is parent — no child rows to lose). The model + API + CLI removal is < 200 LOC. The design-doc updates are 4 file edits.

Bringing the table back if needed later is also small. The migration history is preserved; a future migration can re-introduce the schema if a use case emerges that genuinely doesn't fit ValueArbitration.

---

## 4. Path B execution

### 4.1 Code changes

| File | Change |
|---|---|
| `apps/api/app/models/agent_policy.py` | Delete |
| `apps/api/app/models/__init__.py` | Remove `AgentPolicy` import + export |
| `apps/api/app/api/v1/agent_policies.py` | Delete |
| `apps/api/app/api/v1/routes.py` | Remove `agent_policies` import + `include_router` line |
| `apps/cli/alpha/src/commands/policy.rs` (or wherever the `alpha policy` subcommand lives) | Delete the subcommand. Replace with deprecation message: *"alpha policy was removed — see ValueArbitration design 2026-05-23 for the replacement substrate"* if a soft deprecation is preferred |
| `apps/cli/alpha/src/main.rs` or command registry | Remove the policy subcommand wiring |

### 4.2 Migration

```sql
-- migration: XXX_drop_agent_policies.sql
DROP TABLE IF EXISTS agent_policies CASCADE;
```

No data preservation needed (zero rows). Migration 097 (which created the table) stays in history — migrations are append-only.

### 4.3 Frontend UI scrub (added per Luna review 2026-05-23)

Luna correctly flagged: deleting the backend without scrubbing the frontend leaves dead "Agent Policy" configuration components that silently do nothing — the exact false-comfort pattern this plan is intended to remove.

Action: grep the frontend (`apps/web/src/` or wherever the React tree lives) for:
- Components named `*Policy*`, `*AgentPolicy*`
- Routes referencing `/agents/*/policies` or `policy`
- API calls to `/api/v1/agents/.../policies` or any `agent_policies` endpoint
- Settings panels with "Policy" tabs or sections

Delete all of the above. If any UI references the `alpha policy` command (e.g., docs panels, onboarding flows), update to point at the ValueArbitration design link.

This is a small additional scope item — typically <10 components — but it's the difference between honest removal and a misleading frontend that suggests features that no longer exist.

### 4.4 Documentation updates

| File | Change |
|---|---|
| `docs/plans/2026-05-21-luna-value-layer-design.md` | Update §1 line 20 — strike "`agent_policies` carries RL routing constraints" reference |
| `docs/plans/2026-05-13-ap-cli-differentiation-roadmap.md` | Remove the §6 "Governance & policy gates" `alpha policy show` example and the migration-097 reference. Replace with pointer to ValueArbitration. |
| `docs/research/2026-05-09-modern-animal-harriet-sierra.md` | Update §2 — change "Lift `agent_policies`" recommendation to "Build the Concierge policies as ValueArbitration `tenant_norm` signals — see 2026-05-23 design" |
| `docs/plans/2026-05-10-cli-orchestrator-phase-4-leaf-mcp-auth-plan.md` | Add a note that the `agent_policies` reference at §1 lines 23-25 is now historical — Phase 4 correctly chose `tool_groups` over `agent_policies.allowed_tools` for the scope claim |

### 4.5 Tests

| File | Change |
|---|---|
| `apps/api/tests/test_agent_policies_endpoint.py` (if it exists) | Delete |
| Any test that imports `AgentPolicy` | Remove |
| New test: `test_alpha_policy_subcommand_removed` (CLI integration) | Assert the subcommand prints the deprecation/removal message and exits non-zero |

### 4.6 Operator communication

The `alpha policy` subcommand returns empty for every tenant today, so removing it does not break a running workflow. Communication can be minimal:

- Release notes: *"`alpha policy` removed — policy enforcement is moving to the Value Arbitration layer ([[2026-05-23-value-arbitration-design]]). The AgentPolicy table had zero rows in production."*
- For Modern Animal specifically (the only known future-use referent): direct outreach with the ValueArbitration-replacement design once that ships.

---

## 5. Path A execution (if Simon chooses to wire instead)

Captured here for completeness in case the operator decision differs from §3.

### 5.1 Sequencing

1. **Week 1:** `input_filter` enforcement at `agent_router.py` ingress + tests. Easiest path — mirror the `platform_safety_io` pattern, add `agent_policy_io.consult_with_audit(message, agent_id, tenant_id)`. Add default policies for known sensitive patterns. Operator UI to add per-tenant patterns.
2. **Week 2:** `rate_limit` enforcement. Decide overlap with `core.rate_limit.limiter` — recommend AgentPolicy.rate_limit governs *agent-level* limits (per-(tenant, agent)) while the slowapi limiter remains the *endpoint-level* defense.
3. **Week 3:** `output_filter` enforcement. Streaming handler needs a buffer + scan step. Decide block vs redact policy. Audit emissions before-and-after redaction.
4. **Week 4+:** `data_access` enforcement. Hardest. Needs either a query proxy (intercept and pattern-match SQL) or per-tool data-touch annotations. Recommend per-tool annotations as the cheaper path; the proxy path is a security project on its own.

### 5.2 Risks of Path A

- Adds a fifth overlapping policy substrate as noted in §3.2.
- `data_access` is a research project disguised as a feature ticket.
- Will likely need to migrate into ValueArbitration in 6 months once arbitration's standing-classes are operational, which means a second migration of any policies operators created in the interim.

---

## 6. Recommendation

**Path B (delete).** Rationale in §3. Half-day of work. Reversible if needed. Lines up cleanly with the ValueArbitration design that's the actual long-term home for these concerns.

If Simon disagrees and chooses Path A: ship `input_filter` first (lowest difficulty, highest defensive value), then re-evaluate after a week of usage data whether the remaining three policy types justify the additional work or whether ValueArbitration should subsume them then.

---

## 7. Decision needed

- **From Simon:** Path A or Path B. Default recommendation: B.
- **From Luna:** confirm that the ValueArbitration `tenant_norm` standing-class is the right home for the Modern Animal use case. Sign off on the doc updates in §4.3 that change "use AgentPolicy" to "use ValueArbitration" across the planning corpus.

---

## 8. Companion: what NOT to delete

- The `core.rate_limit.limiter` per-endpoint rate limiting — unrelated to AgentPolicy, operational and useful. Stays.
- The `agent.tool_groups` column — being hardened in P0a. Stays.
- The `platform_safety_io` Safety Floor — independent enforcement path, vectors #345/#351 are queued. Stays.
- The `alpha agent`, `alpha session`, `alpha memory` CLI surfaces — orthogonal to policy, in heavy use. Stay.

Path B is targeted at the AgentPolicy substrate specifically, not at any of the above.
