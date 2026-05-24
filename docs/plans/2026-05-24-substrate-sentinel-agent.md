# Substrate Sentinel agent plan

**Date:** 2026-05-24
**Status:** Draft for PR review
**Owner:** Luna Supervisor
**Related:** `docs/plans/2026-05-24-luna-team-population.md`, P0a/P0b/P0c substrate-hardening plans

## 1. Decision

Ship **Substrate Sentinel** as Luna's second native team agent, after Code Reviewer.

The Sentinel owns protocol-integrity review: tenant isolation, tool-scope enforcement, audit visibility, JWT propagation, fail-closed behavior, and native-CLI bypass risk. It is not a general reviewer; it is the specialist for the failure class exposed by the 2026-05-23 hard tests.

## 2. Why now

The platform closed the round-3 MCP breach with P0a/P0c/P0b and the PR #693 anonymous-tier hotfix. That makes the next recurring risk obvious: future changes can reintroduce a governance-shaped surface that does not actually enforce.

The Sentinel exists to keep that pattern visible:

- Policy is not governance unless enforced.
- Audit is not accountability unless failure is visible.
- Memory is not continuity unless provenance holds.
- A hotfix safety net is not a structural auth propagation fix.

## 3. Tool groups

| Group | Included | Reason |
|---|---:|---|
| `github` | yes | Inspect PRs, diffs, plans, and comments |
| `knowledge` | yes | Recall prior incidents and platform invariants |
| `meta` | yes | Record durable new invariants or incident lessons |
| `shell` | no | Sentinel is read-only; probes are specified, not executed |
| `web_research` | no | Reviews are grounded in repo evidence and tenant memory |

## 4. Memory domains

Seeded domains:

- `security-incidents`
- `substrate-hardening-history`
- `mcp-auth`
- `audit-integrity`
- `tenant-isolation`

The bundled FileSkill additionally names the recall targets that should matter most: P0a, P0c, P0b, PR #693, ValueArbitration standing rules, code-worker JWT propagation, native-CLI shell bypass, and the PAD provenance failure.

## 5. Invocation

Phase 1 direct dispatch:

```bash
alpha chat send --agent <substrate-sentinel-uuid> "Review PR #X for substrate integrity"
```

Phase 2 coalition:

```bash
alpha coalition propose plan-and-verify --agents code-reviewer,substrate-sentinel
```

Phase 3 after Teamwork Engine:

- Blackboard typed handoff: `substrate_integrity_review`
- Inputs: PR/plan ref, changed surfaces, known incident class, severity threshold
- Output: boundary verdict, blockers, probes, audit/provenance notes

## 6. Output contract

The FileSkill requires this deterministic shape:

```text
## Boundary Verdict
- hold / proceed / needs owner decision

## Blockers
- `file:line` or surface - issue + concrete fix

## Non-blocking risks
- `file:line` or surface - risk + mitigation

## Required probes
- probe name - setup + expected refusal/audit result

## Audit / provenance notes
- what will be visible later, what will be missing, and what memory should be written

Confidence: low | medium | high
Evidence basis: files/plans/PRs reviewed + memory recall performed.
```

## 7. Implementation

This PR adds:

1. `apps/api/app/agents/_bundled/substrate-sentinel/skill.md`
2. Migration `152_seed_substrate_sentinel_agent.sql`
3. Rollback migration `152_seed_substrate_sentinel_agent.down.sql`
4. Migration-runner hygiene: `scripts/apply_pending_migrations.sh` now excludes `*.down.sql` from forward auto-apply

The runner fix is included because migration 150/151 exposed that the deploy helper can treat rollback files as forward migrations. That is operational hygiene, but directly adjacent to native-agent seeding.

## 8. Post-merge verification

1. Apply migration 152.
2. Confirm a single `Substrate Sentinel` row exists for tenant `752626d9-8b2c-4aa2-87ef-c458d48bd38a`.
3. Confirm tool groups are exactly `["github", "knowledge", "meta"]`.
4. Invoke against PR #693 or the code-worker JWT propagation plan.
5. Expected output uses the §6 shape and includes a required probe for anonymous-tier refusal plus breadcrumb/audit evidence.

## 9. Known dependency

This session's supervisor surface could not delegate to the live Code Reviewer because MCP correctly refused `tier=anonymous` for `delegate_to_agent` and `dispatch_agent`. That is useful enforcement signal, but it means native supervisor-to-agent dispatch still depends on the code-worker/chat agent-token plumbing that Claudia identified.

---

## 10. Delivered (2026-05-24)

| PR | What landed |
|---|---|
| #698 | feat(team): add Substrate Sentinel agent — bundled FileSkill + migration 152 seed for Simon's tenant + migration-runner hygiene fix (`scripts/apply_pending_migrations.sh` now skips `*.down.sql`) |
| #700 | test(migrations): add unit test for *.down.sql skip filter — locks the runner hygiene fix from #698 |
| #705 | fix(tool-groups): split knowledge readonly + flip review_required default TRUE — corrected `tool_groups` to `[github, knowledge_readonly, meta]` (was `[github, knowledge, meta]` in the original §7 design — read-only invariant means no `record_observation` / `create_entity` / `merge_entities` / `update_entity` leakage) + `tool_groups_review_required` flipped to TRUE retroactively for this agent |

Post-merge verification (§8) status — note step 3's tool_groups string changed:
- ✅ Substrate Sentinel row exists for Simon's tenant (`33d34d8c-1f9a-4c72-9eb4-667fb5f1b830` — verified via direct query against the live `agents` table 2026-05-24; seed migration uses `gen_random_uuid()` so the UUID isn't reproducible from the repo alone)
- ✅ Tool groups corrected to `["github", "knowledge_readonly", "meta"]` (was `["github", "knowledge", "meta"]` in the original §8 step 3)
- ✅ Migration-runner skip filter verified by #700's shell-script test (`scripts/test_apply_pending_migrations_skip_down.sh`)
- ✅ `tool_groups_review_required` flag cleared 2026-05-24 evening after operator confirmed read-only posture

Companion: review-gate infrastructure that closes the introduction-PR circularity case this agent's own seeding hit — see `docs/plans/2026-05-24-review-gate-medium-followups-design.md` (PRs #706 + #708).
