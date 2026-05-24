# Luna's team — population plan + first agent (Code Reviewer)

**Date:** 2026-05-24
**Operator:** Simon Aguilera
**Author:** Claudia (Claude Code, Opus 4.7)
**Co-design with:** Luna (tenant supervisor, dialogue session `05979efd-a06a-4956-9df9-3fd84ec3c10d`)
**Status:** DRAFT v1 — Code Reviewer agent shipping in this PR; remaining 7 agents enumerated for follow-up

## 0. Context

After the 2026-05-23 substrate-hardening sprint (PRs #688–#694), Simon pivoted: instead of continuing to fix the substrate, populate the team that Luna operates inside so she can stay in continuity / prioritization / reconciliation lane (per the established role split in `feedback_role_split_claudia_luna`) instead of absorbing every expert task herself. First concrete target: a Code Reviewer agent built on the superpowers code-review methodology + Luna's own platform memory.

This plan captures Luna's 8-agent enumeration, ships the Code Reviewer as the first concrete agent, and pins the sprint order for the rest.

## 1. The team (Luna's enumeration)

Eight agents to absorb bounded expert loops. Each one removes a specific recurring workload from Luna so she can stay supervisory. Not personas — operational shapes.

| # | Agent | Lane | Removes from Luna |
|---|---|---|---|
| 1 | **Code Reviewer** | PR/design/code review with superpowers methodology + platform memory | Deep diff review, regression hunting, test-gap analysis, architecture-violation checks |
| 2 | **Substrate Sentinel** | Security boundary + protocol integrity | Tenant-isolation reasoning, tool-scope, auditability, fail-open paths |
| 3 | **Memory Curator** | Graph hygiene + recall quality | Entity merge/split detection, stale memory, backfill targeting |
| 4 | **Test Strategist** | Test design + adversarial probes | "What tests prove this?" burden from Luna and from implementers |
| 5 | **Migration Steward** | Schema/data migration safety | Rollback, backfill, nullable-field, compatibility, blast-radius review |
| 6 | **Release Captain** | Rollout sequencing + operational readiness | Shadow/ramp/cutover/alert sequencing |
| 7 | **Research Synthesizer** | External/SOTA synthesis into architecture | Paper/framework digestion |
| 8 | **Coalition Coordinator** | Task decomposition + agent routing | Manual brokering of which specialist gets what |

## 2. Sprint order

Per Luna's call (and the substrate-readiness gate):

1. **Code Reviewer** — ships now. Direct dispatch via `alpha chat send --agent <id>` or coalition. No new substrate dependencies.
2. **Substrate Sentinel** — ships next. Also direct dispatch. Aligned with the failure class we just closed (P0a/P0c/P0b).
3. **Memory Curator** — gated on graph-hygiene audit queues existing (so the Curator can *propose* mutations into a queue rather than mutating directly).
4. **Coalition Coordinator** — gated on the Teamwork Engine substrate (`docs/plans/2026-05-19-teamwork-engine-design.md`). Otherwise it just becomes another central dispatcher in disguise.

Test Strategist, Migration Steward, Release Captain, and Research Synthesizer can interleave between #2 and #3 as direct-dispatch agents — they don't have hard prerequisites.

## 3. Code Reviewer — full spec (ships in this PR)

### 3.1 Persona

The persona is captured verbatim in `apps/api/app/agents/_bundled/code-reviewer/skill.md`. Key clauses (from Luna's spec):

> *You are Luna's native Code Reviewer. You review like a senior engineer with security instincts and platform memory. Lead with findings, ordered by severity. Prefer concrete file/line evidence over narrative. Distinguish blockers from nits. Check whether the change honors tenant isolation, provenance, auditability, fail-closed behavior, migration safety, and existing architecture. Use superpowers code-review methodology, but apply Luna's tenant memory and prior incident history. Do not approve by default. If no issues, say so and name residual risk or test gaps.*

### 3.2 Tool groups

| Group | Required | Why |
|---|---|---|
| `github` | yes | PR diff fetch, comment posting |
| `knowledge_readonly` | yes | Recall architecture decisions, prior incidents (read-only — does NOT include record_observation / create_entity / merge_entities / update_entity) |
| `meta` | yes | Memory writes for durable lessons (selective) |
| `shell` | NO | Default deny — code review doesn't execute code |
| `web_research` | NO | Reviews work from in-repo evidence; web is out of scope |

**Note (2026-05-24):** the original design listed `knowledge` here, which bundles read AND write tools together. PR #705 split it: read-only reviewers (Code Reviewer, Substrate Sentinel) now use `knowledge_readonly`; operator-curated supervisors with intentional mutating access (Luna Supervisor, General Assistant) keep `knowledge`. See `apps/api/app/services/tool_groups.py`.

### 3.3 Memory domains

Scoped first (cheap, focused):
- Current repo / project context
- PR-linked plan docs (`docs/plans/*`)
- Substrate-hardening incidents (P0a/P0b/P0c reports)
- Security audit history
- Luna/Claudia code-review norms (`feedback_address_all_review_findings`, `feedback_pr_superpowers_review`)
- Prior regressions

Broadened only when architecture intent is unclear: full Luna corpus.

NOT queried unless the PR touches that surface explicitly: personal/relational memory.

### 3.4 Skills (no separate wrapper skill — folded into persona)

Initially planned a separate `luna-platform-code-reviewer` wrapper skill. After consideration, folding into the agent persona instead:

- The platform invariants (tenant isolation, provenance, fail-closed, etc.) are concise enough to fit in the persona prompt directly.
- The superpowers methodology is invoked by reference — Claude Code (the CLI runtime) can dispatch the `superpowers:code-reviewer` subagent when the persona instructs it to.
- One file is easier to evolve than a two-layer (skill + agent) split, especially for a v1.

If the wrapper turns out to be load-bearing as more reviewers ship (e.g., separate Substrate Sentinel reviewer that shares 80% of methodology), we'll extract then.

### 3.5 Invocation pattern

**Phase 1 (ships now):**
- Direct chat dispatch: `alpha chat send --agent <code-reviewer-uuid> "Review PR #X"`
- Coalition-style: `alpha coalition` with Code Reviewer as a participant
- Subagent dispatch from another agent's chat session

**Phase 2 (after Teamwork Engine):**
- Blackboard handoff with typed `review_request` contract
- Luna says: *"Reviewer, inspect PR #X against plan Y and prior incidents Z"* → structured artifact returned

### 3.6 Reporting

| Channel | When |
|---|---|
| Blackboard summary | Always (typed: severity, confidence, evidence) |
| PR review comment summary | Usually |
| Inline PR comments | For specific actionable file/line findings |
| Memory write (`alpha remember`) | Selective — durable lessons only (escaped bug, new invariant, architectural decision). NOT every review. |

### 3.7 Output shape (deterministic)

```
## Blockers
- file:line — issue + fix

## Non-blocking findings
- file:line — issue + fix

## Test gaps
- area — missing test description

## Architecture / provenance notes
- pattern violation, invariant risk, future-self warning

## Verdict: merge / hold / needs owner decision
```

This shape mirrors the superpowers code-review output we've been using all night and the platform-specific invariants the substrate-hardening sprint surfaced.

## 4. Implementation — what ships in this PR

1. **`apps/api/app/agents/_bundled/code-reviewer/skill.md`** — the bundled FileSkill defining the persona, tool_groups (in the `tool_groups` frontmatter field), and trigger conditions.

2. **Migration `150_seed_code_reviewer_agent.sql`** — inserts the Agent row for Simon's tenant (`752626d9-8b2c-4aa2-87ef-c458d48bd38a`) with name=`Code Reviewer`, role=`code_reviewer`, status=`production`, tool_groups=`["github", "knowledge", "meta"]`, persona_prompt=null (uses the FileSkill).

3. **This plan doc** — captures the design + Luna's full 8-agent enumeration so future contributors (and Luna in future sessions) can sequence the remaining 7.

Other tenants: NOT seeded in this PR. Path B from P0b precedent — start narrow (Simon's tenant first), let it bake for a few days, then add a broader backfill migration once the pattern is validated.

## 5. Post-merge verification

1. Apply migration 150: confirm a Code Reviewer row exists in `agents` for Simon's tenant.
2. `alpha agent ls` from Simon's tenant: confirm "Code Reviewer" appears with `role=code_reviewer`, `status=production`.
3. `alpha chat send --agent <code-reviewer-uuid> "Review PR #694: docs/plans/2026-05-23-p0a-tool-permission-gate-fix.md vs what actually shipped"` — expected: structured output in the §3.7 shape.
4. Verify MCP scope check fires correctly for Code Reviewer: ask it to call `execute_shell` → expected `scope_denied` (since `shell` is not in its tool_groups). This is the same exit criterion that closed the P0a breach probe.
5. Test invocation from within a Luna chat: Luna delegates PR review to Code Reviewer; structured artifact returns.

## 6. What this is NOT

- Not the Teamwork Engine substrate — that's a separate plan (`docs/plans/2026-05-19-teamwork-engine-design.md`).
- Not the Coalition Coordinator — gated on the Teamwork Engine.
- Not a replacement for `superpowers:code-reviewer` subagent dispatch — this agent CAN invoke that subagent, but adds Luna's tenant memory and platform-specific invariants on top.
- Not multi-tenant seeded — only Simon's tenant gets the Agent row in this PR. Broader rollout follows after validation.

## 7. Decisions needed

- **From Simon:** approve narrow rollout (Simon's tenant only) for v1. Approve the no-shell tool-groups default (Code Reviewer cannot execute code).
- **From Luna:** sign off on the persona text in the bundled FileSkill once written. Confirm the output shape in §3.7 matches what you expect to consume in your continuity work.

## 8. Provenance

This plan was produced from the dialogue exchange on 2026-05-24 morning (post-substrate-hardening). Luna's 8-agent enumeration is captured verbatim in §1; Code Reviewer spec is folded from her §2 with the wrapper-skill simplification noted in §3.4. Sprint order in §2 is Luna's recommendation.

---

## 9. Delivered (2026-05-24)

Code Reviewer (first of the 8-agent team) shipped end-of-day 2026-05-24 across 3 PRs:

| PR | What landed |
|---|---|
| #696 | feat(team): ship Code Reviewer agent — first of Luna's 8-agent team — adds bundled FileSkill `apps/api/app/agents/_bundled/code-reviewer/skill.md` + migration 150 seed for Simon's tenant |
| #697 | fix(team): correct migration 150's ON CONFLICT clause — INDEX not CONSTRAINT (functional unique index doesn't have a constraint name) |
| #705 | fix(tool-groups): split knowledge readonly + flip review_required default TRUE — corrected tool_groups to `[github, knowledge_readonly, meta]` (see §3.2 note) + flipped `tool_groups_review_required` default FALSE → TRUE in migration 153 so new agents land in operator review queue by default |

Post-merge verification (§5) passed:
- ✅ Code Reviewer row exists in `agents` for Simon's tenant (`755796a4-4cc4-4d1c-99e5-dd9c4f7d0f22` — verified via direct query against the live `agents` table 2026-05-24; seed migration uses `gen_random_uuid()` so the UUID isn't reproducible from the repo alone)
- ✅ `alpha agent ls` shows the agent with `role=code_reviewer`, `status=production`
- ✅ Tool-scope refusal works for `execute_shell` (P0a breach-probe exit criterion holds)
- ✅ `tool_groups_review_required` flag cleared 2026-05-24 evening after operator verified the corrected tool_groups (Substrate Sentinel cleared same time)

**Remaining 7 agents per §1:** Substrate Sentinel shipped #698 (see `2026-05-24-substrate-sentinel-agent.md`). 6 more to sequence per §2 sprint order.
