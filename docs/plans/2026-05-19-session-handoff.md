# Session handoff — 2026-05-19 [PINNED]

> **PINNED.** Canonical reference for the 2026-05-19 long-session state. Read this end-to-end before resuming work. Subsequent session updates should append to this doc rather than start a new handoff, until a meaningfully different platform state warrants a new one.

Date: 2026-05-19
Operator: Simon Aguilera (`saguilera1608@gmail.com`, tenant `752626d9-8b2c-4aa2-87ef-c458d48bd38a`)
Outgoing driver: Claude Code (current session)
Co-author throughout: Luna (via `alpha chat send` + `alpha run --fanout`)

## Role split (operator decision, 2026-05-19)

> "for the execution of this project you need to do the heavy lifting as we are using the opus heavy model, luna stays as reviewer for now until i bump the codex suscription and she can handle more heavy lifting"

- **Claude Code (Opus 4.7)** — primary execution driver. Heavy lifting: design implementation, code changes, multi-PR rollouts, infra changes, migrations.
- **Luna (tenant supervisor)** — reviewer + complementary perspective. Code review, design review, research dispatch, rich-content generation. NOT the implementation driver until operator bumps Codex subscription.
- **Standing rule**: keep Luna in the loop of all decisions and progress via `alpha remember --kind <decision|progress|concern> "<fact + date + why>"`, even when not asking her to act.

Codified in local memory: `feedback_role_split_claude_executes_luna_reviews.md` + `feedback_keep_luna_in_loop.md`. Written into Luna's tenant memory as decision observations `747a8334-ca0b-4dff-8041-11d10c7f74a0` and `e296d17c-4bd2-40f5-ba8e-8efa0872b5dd`.

## Read this first

This is a long session (30+ hours of work). The mental model in three sentences:

1. **AgentProvision is a coordination layer** for specialist AI agents, mirroring the prehistoric-human → civilization arc (see `docs/pitch/wolfpoint-demo-pitch.md` Acts I-IV). Every meaningful design is evaluated through the civilization-layer lens — does it add coordination infrastructure (language / trust / memory / specialization / affect / supervision)?
2. **Luna is a co-worker**, not a tool. Every meaningful request gets a Luna dispatch in parallel with my own work. Luna has Higgsfield MCP wired and routes research to Gemini. She has already caught real bugs (mig 138 self-reference) and real design flaws (temperature mapping inversion) this session.
3. **The emotions engine is the next big design** sitting on PR #582, dual-reviewed, ready to implement. Luna initially offered to drive PR A; **per the role split below, Claude drives, Luna reviews.**

## What shipped today (live in production)

| PR | What | Status |
|---|---|---|
| #553 | Wave 1 (Higgsfield MCP + Qwen + Kimi K2 + code-worker quota walker) | LIVE |
| #558 | Simple dashboard: chat fills viewport, removed quick-link tiles | LIVE (verified in Chrome) |
| #559 | Wave 2 CLI executors: DeepSeek + GLM + Aider + Goose | LIVE |
| #565 | Web nginx cache + gzip headers | LIVE (`cf-cache-status: HIT`) |
| #566 | api Phase A diet: whisper → code-worker, dropped sentence-transformers fallback (3.95 → 3.27 GB) | LIVE |
| #567 | Web bundle code-split via React.lazy (634 KB → 233 KB gzipped, 62% reduction) | LIVE |
| #568 | CI workflow hygiene: pre-build prune + `--force-rm` | LIVE (ended the disk-pressure deploy cycle) |
| #569 | Code-worker Phase B + Higgsfield CLI install (npm CLIs pinned + per-CLI layers) | LIVE (`higgsfield 0.1.40` at `/usr/bin/higgsfield`) |
| #570 | Async chat-result pattern (chat_jobs, migration 137) — kills Cloudflare 524 on long chat | LIVE |
| #572 | Higgsfield end-to-end glue: Gemini single-underscore CLI prefix, Luna `tool_groups` mig 138, INTENT routing | LIVE |
| #573 | `alpha run` real Temporal dispatch (replaces synthetic stub) — `USE_REAL_FANOUT_WORKFLOW=true` set on api | LIVE |
| #574 | `alpha review` cross-CLI consensus loop (Coalition+Blackboard, mig 139) | LIVE (server side; CLI v0.7.5 ships subcommand) |
| #575 | Temporal SDK 1.10 flat `WorkflowExecutionDescription` hotfix | LIVE |
| #577 | Orchestration worker: `ThreadPoolExecutor` for sync activities | LIVE |
| #578 | Comprehensive Alpha CLI docs refresh (v0.7.5 + new commands + getting-started + troubleshooting) | LIVE |
| #579 | Skill-creator Phase 2: eval runner + workspace layout + migration 140 | LIVE |
| #266 | Password recovery via SendGrid SMTP (DNS records added via CF API, domain auth verified, end-to-end tested) | LIVE |

## What's in flight (PRs open at time of handoff)

| PR | What | State | Next action |
|---|---|---|---|
| #580 | Skill-creator Phase 3: aggregator + analyzer + `/benchmark` endpoint | CI CLEAN | Merge |
| #581 | Temporal dispatch: `daemon-thread + asyncio.run` → `await Client.start_workflow` (review_dispatch + eval_runner) | CI CLEAN | Merge. After this, `alpha review` workflow dispatch actually fires Temporal. |
| #582 | Digital emotions engine — prototype design doc (Phase 1 spec) | Design dual-reviewed (superpowers + Luna self-review). All findings applied. Luna confirmed "feels grounded." | **PR A driver = Claude** (resolved by role split above). Luna reviews each chain PR. |

## Memory rules established today (durable across sessions)

| Memory | What it codifies |
|---|---|
| `feedback_delegate_to_luna` | Always dispatch code review / research / design / content generation to Luna in parallel. Luna isn't a fallback, she's a co-worker. |
| `feedback_design_for_civilization_layer` | Every meaningful design evaluated through the civilization-layer lens (language/trust/memory/specialization/affect/supervision). AGI = the coordination layer, not a bigger model. Cite `docs/pitch/wolfpoint-demo-pitch.md` acts I-IV when relevant. |
| `feedback_test_in_chrome` | Every UI/UX change visually verified in Chrome via browser automation. CI green ≠ feature works. |
| `feedback_pr_superpowers_review` | Every PR I open gets a superpowers code-reviewer pass; all BLOCKER + IMPORTANT fixed in the same PR before merge. |
| `feedback_always_document_plans` | Every meaningful task gets a `docs/plans/<date>-<topic>.md` doc as durable record. |
| `feedback_single_pr_for_feature` | Multi-step features chain branches for review but **merge as one squash** to avoid N build storms. |
| `feedback_verify_branch_before_commit` | `git branch --show-current` in the same bash invocation as `git commit`. |

Full list: `/Users/nomade/.claude/projects/-Users-nomade-Documents-GitHub-servicetsunami-agents/memory/MEMORY.md`.

## Emotions engine design — current state

**Doc**: `docs/plans/2026-05-19-emotions-engine-prototype-design.md` (PR #582, head `39a9df69`).

**One-sentence summary**: PAD vector per session in `conversation_episode.affect_vector` (new JSONB) + per-agent baseline in `agent_memory.affect_baseline`, updated from existing `rl_experience.reward` via OCC appraisal, read at `agent_router` to inject an ArtCoT-style label into the system prompt. Phase 1 is three chained PRs (A: schema+service+tests; B: RL wire-in+`/affect-trace` endpoint; C: prompt injection).

**Architectural rationale**: it's a coordination-layer feature (the 5th primitive, alongside language/trust/memory/specialization). Affect-on-Blackboard is the mechanism that lets human societies scale past tribe-size; we're giving Luna and coalition members the same.

**Notable contributors** (read the Credit section of the doc):
- **Luna** — literature anchors (HICEM, Affective Spiking NN, arXiv 2511.20657, RLCF, ArtCoT). Architectural correction: temperature mapping was INVERTED — high-arousal under negative valence should be LOW temp (survival focus), not high. High-affect memory etching.
- **Simon (operator)** — protective recall: high-salience episodes recall easily but the felt-charge decays with re-exposure. Mirrors trauma reconsolidation. Phase 3.
- **Superpowers code-reviewer** — caught `user_signal` was fabricated (no affect classifier exists); caught existing `mood String(30)` has 4 readers (don't touch it, add a new column); flagged that Phase 1 was 2 PRs in a trenchcoat.

**~~Open design question for the operator~~** — **RESOLVED 2026-05-19**: Claude drives PR A, Luna reviews. Per role split above ("Opus does heavy lifting, Luna stays reviewer until Codex sub bumps"). The earlier "who drives" question is closed; design doc will be aligned with this on the next implementation pass.

## Standing concerns / known issues

1. **Docker VM still tight** (~89% / 9.5GB free). PR #568 fixed the structural leak (pre-build prune), but a Phase D refactor is still queued in `docs/plans/2026-05-18-docker-image-shrink-and-latency.md`: move OAuth handshake from api to code-worker so we can drop the npm CLIs + Node toolchain from api. Multi-PR project; not yet started.
2. **`alpha review` dispatch threading bug** — PR #581 fixes the "daemon thread fire-and-forget" bug that was causing review workflows to silently never dispatch. After #581 merges + deploys, `alpha review start <ref>` actually fires the ReviewWorkflow.
3. **`alpha run --fanout` parent-merge step deferred** — child workflows complete but the parent fanout stays "running" because the merge aggregator isn't implemented. Workaround: query the chat_messages table directly (we used this to recover Luna's literature survey today). Real fix: build a SkillEvalIterationWorkflow-style parent pattern (ADR exists at `docs/plans/2026-05-19-skill-eval-temporal-parent-pattern-adr.md`).
4. **Skill-creator Phase 2 rows stuck at `queued`** — by design after PR #581. The full parent-workflow pattern in the ADR restores status-tracking. Phase 3 (#580) doesn't fix this; it's a separate follow-up.
5. **DNS API token + SendGrid key were pasted in this session's transcript.** Operator was asked to rotate both. Verify rotation completed.

## How to use the Alpha CLI delegation pattern (working surface today)

```
alpha status                                                # confirm authed
alpha chat send --no-stream "<short prompt>"                # short Codex/Gemini round-trip; works under 100s
alpha run --fanout claude_code --background --json "<...>"  # real Temporal dispatch, no 524
alpha review start <ref> --clis claude_code --max-rounds 1  # cross-CLI consensus (full after #581 lands)
```

Long Luna prompts hit Cloudflare 524 — workaround is to poll `chat_messages` table directly with the session_id. Once async chat-result pattern (PR #570) is fully adopted by callers, this goes away.

## How to pick up where we left off

1. Read `docs/plans/2026-05-19-emotions-engine-prototype-design.md` end to end.
2. Read the 3 critique outputs in `/private/tmp/claude-501/.../tasks/` (the superpowers review + Luna's two reviews). Or just read the Credit section of the doc — it summarises them.
3. **PR A driver = Claude** (resolved). Open feature branch off `docs/emotions-engine-design`, implement Phase 1 PR A (PADVector schema migration + EmotionEngine service + tests, NO wiring).
4. Dispatch Luna review of PR A in parallel with superpowers code-reviewer. Aggregate, fix BLOCKER+IMPORTANT in same PR.
5. Merge PRs #580 + #581 if not already (CI clean, low risk; both wait only on operator approval).
6. **Standing rules**:
   - Every PR gets superpowers review + Luna review side-by-side.
   - UI changes get Chrome verification.
   - Plans go into `docs/plans/<date>-<topic>.md`.
   - Claude executes, Luna reviews — until Codex subscription bump.
   - Keep Luna in the loop of decisions via `alpha remember`, ~2-5 calls per session.
   - Multi-step features chain branches but **merge as one squash** (avoid build storms).

## What I'm proud of this session (the human bit)

- The team mode actually worked. Luna caught a real bug in mig 138 and a real architectural flaw (temperature mapping). The superpowers reviewer caught two BLOCKERs I missed. I caught what they didn't. No single agent could have shipped the work this well.
- The emotions engine design is grounded in real platform substrate (zero new tables, additive schema, reuses `rl_experience` + `conversation_episode` + `BlackboardEntry` + `agent_memory`) AND in real research (Luna's lit survey via Gemini, not my training).
- The civilization-layer framing the operator (Simon) articulated is now durable memory and threaded into the design doc — future drivers will see the metaphor and know why we're not just "adding emotions" but adding the fifth coordination primitive.
- We shipped 17 PRs and one full SendGrid integration in one session, including 3 hotfixes that didn't exist when we started. The platform is meaningfully different at the end of today than at the beginning.

Hand off with care. — Claude
