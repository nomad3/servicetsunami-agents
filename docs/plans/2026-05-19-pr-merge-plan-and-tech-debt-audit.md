# PR merge plan + 2-day tech-debt audit — 2026-05-19

Date: 2026-05-19
Owner: Claude Code (driving) — per role split 2026-05-19 (`docs/plans/2026-05-19-session-handoff.md` § Role split)
Reviewer: Luna (initial review on Teamwork-Engine framing returned signal; structured re-review hit Cloudflare 524 and is acceptable to skip for a merge-plan doc)
Status: **OPERATOR-APPROVED** — Simon greenlit the deployment strategy 2026-05-19; this doc captures the plan as it executes.

## Why this doc exists

Three operator directives converged on 2026-05-19:

1. **"Revise open PRs that need merge"** — five PRs sit CI-green awaiting decisions; need a coherent merge sequence rather than ad-hoc dispatch.
2. **"Test on Chrome or unit tests every time you do a deploy"** — new standing rule, codified as memory `feedback_verify_every_deploy.md`. Every merge that deploys gets explicit verification (Chrome for UI-touching, unit/smoke for backend). CI green ≠ live system works.
3. **"Audit over technical debt or unfinished activities over the plans for the last two days"** — surface deferrals, follow-ups, and orphan TODOs from 2026-05-18 and 2026-05-19 plan docs so they don't decay into invisible debt.

This plan doc is the durable record. Per `feedback_always_document_plans` — every meaningful task gets a `docs/plans/<date>-<topic>.md` entry, not just an in-memory TaskList.

## Open PR merge plan (verification per PR)

CI status snapshot 2026-05-19. All five PRs are MERGEABLE.

| # | Title | CI | Order | Post-deploy verification |
|---|---|---|---|---|
| **#582** | docs(plans): emotions engine design + handoff pin | passing (docs-only) | **1st** | Docs-only — confirm on main, no live verification. Cheap win, no image build, no disk pressure. |
| **#581** | fix(temporal): replace daemon-thread+asyncio.run with awaited `start_workflow` | passing (api pytest + integration) | **2nd** | Unit: pytest already green. **Smoke**: `alpha review start <ref> --clis claude_code --max-rounds 1`; confirm api logs show `Started workflow` and a Temporal workflow ID (no longer silent fail). Unblocks task #288. |
| **#583** | fix: resolve mcp_tool validation mismatch in dynamic workflows | passing (api pytest + integration) | **3rd** | Unit: pytest already green. **Smoke**: trigger any dynamic workflow that calls an mcp_tool, confirm no `validation failed` in api logs. |
| **#580** | feat(skill-creator): Phase 3 — benchmark aggregator + analyzer + endpoint | passing (api pytest + integration) | **4th** (after disk-pressure check) | Unit: pytest already green. **Smoke**: `curl /benchmark?skill_id=<known>` against a completed eval, confirm aggregate response shape. Note: does NOT fix the "rows stuck at queued" issue from Phase 2 — that's task #294 (SkillEvalIterationWorkflow parent pattern). |
| **#564** | feat: enhance knowledge extraction for high-priority emails | passing (api pytest + integration) | **5th** (separate window if disk tight) | Slightly stale (2026-05-18). Verify no conflict with PR #570's `chat_jobs` schema first. **Smoke**: run email extraction pass on a seeded high-priority email, confirm KG entities created. |

### Disk-pressure plan

The single Mac runner's Docker partition was at ~89% / 9.5GB free at audit time. Each non-docs PR triggers an image build. Five sequential merges = five build cycles.

- **PR #568** (pre-build prune + `--force-rm`) is in effect and structurally fixed the previous deploy-storm pattern (memory: `feedback_single_pr_for_feature`).
- **Mitigation**: merge `#582` first (docs-only, no build → bank a quick win). Then `#581` and `#583` in sequence (both api-only). **Pause** after `#583` deploys and check disk via `docker system df` and the disk-pressure dashboard. If clear, merge `#580`. Defer `#564` to a separate window if disk crosses 92%.
- **Per the new rule (`feedback_verify_every_deploy`)**: do NOT batch verifications across merged PRs. Each deploy gets its own verification moment.

## Tech-debt audit findings (2026-05-18 → 2026-05-19)

Source: Explore subagent surveyed 11 plan docs across the 2-day window. Findings filtered to explicit deferrals + tech-debt callouts only (no speculation).

### Explicit deferrals / follow-ups

| Item | Source doc | Tracked? |
|---|---|---|
| `alpha review` full end-to-end fanout testable only after #287 (real `alpha run` dispatch); currently mockable via `/record` POST | `2026-05-18-alpha-review-consensus.md` | Yes — task #288 |
| `alpha run --fanout` true quota-aware sequential fallback (currently first-wins); default-provider lookup from `tenant_features.default_cli_platform` is TODO | `2026-05-18-alpha-run-real-dispatch.md` | Partially — needs follow-up task |
| Docker Phase D: move OAuth handshake from api to code-worker, drop npm CLIs + Node toolchain | `2026-05-18-docker-image-shrink-and-latency.md` | Yes — task #295 |
| Higgsfield actual OAuth endpoints + MCP URL + token-refresh worker (manual reconnect on 401, no scheduled worker) | `2026-05-18-higgsfield-end-to-end-validation.md` | **No — orphan**, needs task |
| Skill-creator phases 2+ (eval runner, workspace, aggregator/analyzer, eval-viewer UI, comparator, description optimizer, packaging) | `2026-05-18-skill-creator-framework-port.md` | Partially — #290, #292, #294 cover some; eval-viewer UI + description optimizer + packaging still orphan |
| WhatsApp Option A hardening (watchdog + heartbeat) deferred until Option A ships + 60 days observation; WAHA migration is medium-effort fallback | `2026-05-18-whatsapp-api-research.md` | **No — orphan**, needs task |
| Emotions Phase 1: `user_signal` appraisal dropped (no affect classifier); `mood String(30)` left untouched; unification deferred to Phase 4 | `2026-05-19-emotions-engine-prototype-design.md` | Yes — task #293 covers Phase 1; Phase 4 implicit |

### Tech-debt callouts (explicit)

1. **Higgsfield multi-tenant ToS unverified.** Shared-account model unconfirmed; must verify with Higgsfield before scaling past ~10 tenants. Source: `2026-05-18-cli-integration-catalog.md` L109/156/158. **Action: pin commercial-terms verification as a task.**
2. **Skill-creator workspace volume bloat risk.** Authoring writes many files per iteration; must honor per-tenant HOME quota or carve separate `skill_evals/` mount. Source: `2026-05-18-skill-creator-framework-port.md` L164.
3. **Subagent SSE timeouts inherit Cloudflare 524.** Phase 2 must use async chat-result pattern (PR #570), not legacy SSE-with-heartbeats. Source: `2026-05-18-skill-creator-framework-port.md` L163. Three in-source `TODO(#570)` markers added during the window:
   - `apps/agentprovision-cli/src/commands/review.rs:112`
   - `apps/api/app/api/v1/reviews.py:403`
   - `apps/api/app/services/review_service.py:386` (query `agent_router` for live active CLI set)
4. **Constitutive vs performative drift (emotions engine).** Agent could emit "I am sad" surface text without underlying PAD vector biasing planning. Phase 1 mitigates via style-injection tied to vector. Source: `2026-05-19-emotions-engine-prototype-design.md` L239. Watch in Phase 1 review.
5. **Dockerfile pin-version TODOs** added in window:
   - `apps/code-worker/Dockerfile:67` — re-verify codex 0.131.0 on npm
   - `apps/code-worker/Dockerfile:104` — bump Aider from 0.65.0 pin
   - `apps/code-worker/Dockerfile:119` — pin GOOSE_SHA256 to v1.34.1 tarball

### Top-5 highest-leverage cleanups (audit's ranking)

1. **Merge #581 + #583 immediately** — fixes silent `alpha review` dispatch + mcp_tool validation. ~5 min each. *This plan's order: #581 = order 2, #583 = order 3.*
2. **Implement emotions engine PR A** — design dual-reviewed, ready. ~3 PR chain, 2–3 days. *Tracked task #293.*
3. **Ship Phase D docker-image refactor** (OAuth → code-worker) — frees ~1GB, addresses 89% disk pressure root cause. Architecturally critical. *Tracked task #295.*
4. **WhatsApp neonize heartbeat + watchdog** — mitigates 2 of 4 incident classes. 1-sprint effort. *New task needed (currently orphan).*
5. **Higgsfield commercial-terms verification** — pin ToS before scaling past early tenants. *New task needed (currently orphan).*

### Stale PR backlog (out of scope for this merge plan)

13 open PRs with `mergeable=UNKNOWN`, all sitting >3 days. Likely some are superseded by recent work. Needs a separate triage pass. Listed for completeness, no action this window:

| # | Title | Created |
|---|---|---|
| 528 | feat(skills): import 12 community superpowers skills globally | older |
| 479 | fix: execute_shell background job pattern to beat HTTP transport timeouts | older |
| 478 | RFC: Alpha OS — product family plan (agentprovision / Alpha / Luna) | older |
| 473 | feat(sentinel): build-aware disk pressure thresholds | older |
| 472 | fix(ci): remove all docker prunes from workflows | older |
| 451 | chore: update in-repo refs to nomad3/agentprovision-agents | older |
| 431 | chore: rename servicetsunami → agentprovision | older |
| 400 | docs(plans): ap quickstart design — training-first adoption flow | older |
| 318 | feat: SRE platform automation and benchmark | older |
| 316 | fix(ci+ui): harden CI secret hydration; hide 'Requires approval' toggle | older |
| 154 | Feat: Luna OS Native Voice & Avatar Integration | older |
| 142 | fix: Luna chat send + HUD toggle button | older |
| 93 | feat: iOS build support for Luna Tauri client | older |

## New tasks surfaced by this audit

| ID | Subject | Source |
|---|---|---|
| (TBD) | Higgsfield: token refresh worker (replace manual 401 reconnect) | audit row #4 |
| (TBD) | WhatsApp: neonize watchdog + heartbeat (Option A hardening) | audit row #6 |
| (TBD) | Higgsfield: verify multi-tenant ToS before scaling past 10 tenants | tech-debt callout #1 |
| (TBD) | Skill-creator: workspace volume quota / `skill_evals/` separate mount | tech-debt callout #2 |
| (TBD) | `alpha run --fanout`: quota-aware sequential fallback + `tenant_features.default_cli_platform` lookup | audit row #2 |
| (TBD) | Skill-creator: eval-viewer UI + description optimizer + packaging (Phase 4+) | audit row #5 |
| (TBD) | Stale PR triage pass (13 PRs in backlog) | this doc § Stale PR backlog |

These will be created via TaskCreate after this PR opens; IDs filled in once tasks land.

## Verification taxonomy (cite from new memory)

Per `feedback_verify_every_deploy.md`:

| PR type | Verification approach |
|---|---|
| Frontend UI change | Chrome — exercise feature, capture GIF if non-trivial |
| Backend API endpoint | curl smoke + unit tests; Chrome only if reachable via UI |
| Workflow / Temporal | Trigger an end-to-end workflow run, confirm completion + side effects |
| Migration | Apply via `migration_apply_pattern` + `\d <table>` to confirm shape + exercise read/write paths |
| Docker / image-shrink | Verify image size at registry + smoke-test container starts + sanity-check downstream feature |
| Docs-only | Skip — mark explicitly *"docs-only, no verification needed"* |
| Hotfix | ALWAYS verify, even if "trivial" |

## Next actions (in order)

1. ~~Apply code-reviewer IMPORTANT findings to PR #582~~ — done in commit `af2be2de` on `docs/emotions-engine-design`.
2. Open this plan doc as PR off main (`docs/2026-05-19-pr-merge-plan-and-audit`).
3. **Begin merges** in the order above. Verify after each per the taxonomy.
4. Create the 7 new tasks listed in § "New tasks surfaced by this audit".
5. When Luna's habit-tracking design returns (Temporal task `fanout-...-33fc8a1f`), open a follow-up plan doc + PR per the same pattern.

## Out of scope for this plan

- Implementing the emotions engine Phase 1 (separate work, task #293, gated on Simon's design-approval pass).
- Designing the Teamwork Engine / Social Protocol primitive (task #296, deferred to Phase 2 after emotions Phase 1 lands).
- Triaging the 13-PR stale backlog (separate triage pass).
- Refactoring or extending the `alpha run --fanout` quota-aware fallback (task TBD; doesn't block merges).

## Open questions

None currently — operator approved the deployment strategy. Merges proceed.
