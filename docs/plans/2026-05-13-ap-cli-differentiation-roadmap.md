# `alpha` CLI differentiation roadmap

**Date:** 2026-05-13 (status updated 2026-05-18)
**Author:** Claude (sonnet) at user request
**Status:** Phase 1 shipped. **Phase 2 partially shipped** as of
2026-05-18: durable `alpha run --fanout <cli>` real dispatch (PR #573),
multi-LLM fanout with raw-list aggregation (council/all adjudication
queued), and cross-CLI consensus reviews via `alpha review` (PR #574).
See [`2026-05-18-alpha-cli-delegation-pattern.md`](2026-05-18-alpha-cli-delegation-pattern.md)
for the consolidated shipped-vs-queued matrix.
**Branch:** `docs/cli-differentiation-roadmap`

## TL;DR

`alpha` is not competing with `claude` / `codex` / `gemini` / `gh copilot`. It
orchestrates them. The analogy is `kubectl` vs `docker run`: kubectl doesn't
try to be a better Docker; it makes containers operable at fleet scale. `alpha`
should make Claude Code + Codex + Gemini CLI + Copilot CLI + OpenCode operable
at *team and tenant scale*.

This doc lays out **eight CLI surfaces** that the leaf CLIs cannot offer —
each maps to a feature the platform has already shipped — and a four-phase,
~six-week rollout to make `alpha` the agent control plane CLI without peer.

The headline single-command demo is the [`alpha run --fanout --background`](#1-durable-runs-ap-run--ap-watch--ap-cancel)
flow that lands in Phase 1 and combines durability, multi-provider parallel
execution, background continuation, and cost attribution into one terminal
interaction no leaf CLI can replicate.

## Strategic framing

The four major coding CLIs (Claude Code, Codex, Gemini CLI, GitHub Copilot
CLI) and OpenCode all share five structural limits:

| Limit | Consequence |
|---|---|
| **Single-vendor LLM** | No quota fallback, no consensus review, no per-task model routing |
| **Ephemeral session** | Closes the terminal → task dies; no resume from a different machine |
| **Single-user, single-machine** | No tenant model, no team RBAC, no audit log, no cost attribution |
| **No durable orchestration** | A long-running task is a foreground process — fragile under network drops, sleep, restart |
| **No memory across sessions** | Each invocation starts amnesic |

`alpha` already has the platform plumbing for all five gaps:

- **Multi-LLM:** ProviderAdapter pattern with autodetect + quota fallback (#245)
- **Durability:** Temporal workflows on `agentprovision-code` queue
- **Multi-tenancy:** every model has `tenant_id`, JWT-scoped requests, Fernet credential vault
- **Governance:** Agent Lifecycle Management (#153, 2026-04-18) — versioning, RBAC, audit, policies
- **Memory:** Memory-First Phase 1 shipped; `recall.py` + PostChatMemoryWorkflow
- **A2A:** CoalitionWorkflow + Blackboard (2026-04-12) — multi-agent coalitions on shared substrate
- **Cost attribution:** cost_usd + input/output token split per task (#174)
- **Workflow templates:** 26 dynamic workflow recipes
- **RL learning:** Auto-quality scoring on every response, RL policy updates

What's missing is the CLI surface that exposes those features to a human at a
terminal. Today `alpha` is a basic chat client + login/status/upgrade/quickstart
toolbox. This doc fills the gap.

## The eight differentiators

Each section: user story, CLI UX, backend dependencies (what exists vs what
needs new endpoints), acceptance criteria, effort.

### 1. Durable runs (`alpha run`, `alpha watch`, `alpha cancel`)

**The wedge feature.** Tasks survive terminal close, network drop, even a
laptop reboot — and resume from any other machine on the same account.

**User story:** "I want to kick off a long refactor, close my laptop, and
finish on the desktop after dinner."

**CLI UX:**

```
$ alpha run "refactor the auth module to use FastAPI Depends" \
       --provider best \
       --background
[alpha] task t_a4f3b2 dispatched → claude-code (tenant=integral, agent=code-agent)
[alpha] estimated 8m, $0.42 via Claude Sonnet
[alpha] close this terminal any time — resume with: alpha watch t_a4f3b2

# later, from a different host:
$ alpha watch t_a4f3b2
[alpha] t_a4f3b2 — running, 3m elapsed
> opened PR #432: refactor(auth): adopt FastAPI Depends pattern
> committed 4 files (...)
> [STILL RUNNING] applying tests…
^C  to detach (task continues), or: alpha cancel t_a4f3b2 to stop
```

**Backend deps:**

| Need | Status |
|---|---|
| Temporal workflow that runs a single CLI invocation | ✅ `CodeTaskWorkflow` |
| Persistent task ID with status | ✅ `agent_tasks` table + workflow run ID |
| `GET /api/v1/tasks/{id}` polling | ⚠️ Partial — exists for some flows; needs unification |
| SSE `/api/v1/tasks/{id}/events/stream` for live tailing | ⚠️ Exists for chat sessions; reuse pattern |
| Auth attaches to any machine with same JWT | ✅ — JWT in `~/.config/agentprovision/config.toml` |

**Acceptance:**
- `alpha run "<prompt>"` returns within 2s with a task ID.
- `--background` exits immediately; foreground tails SSE until completion.
- `alpha watch <id>` from a different host shows live event stream.
- `alpha cancel <id>` issues `RequestCancelWorkflowExecution` to Temporal.

**Effort:** 1.5 weeks (largely new `commands/run.rs` + `commands/watch.rs` +
SSE consumer + minor backend SSE alignment).

### 2. Multi-provider fanout (`alpha run --fanout`)

**User story:** "I want three different LLMs to review the same PR and merge
their findings into one report."

**CLI UX:**

```
$ alpha run "audit this codebase for unparameterized SQL queries" \
       --fanout claude,codex,gemini \
       --merge council \
       --background
[alpha] dispatched 3 parallel reviews → t_x91 (parent)
[alpha] children: t_x91a (claude), t_x91b (codex), t_x91c (gemini)
[alpha] meta-adjudicator will merge when last child completes
[alpha] notify when done via Slack? (y/n)

$ alpha watch t_x91
[alpha] t_x91 COMPLETE — 7 findings, 2 blockers
[alpha] consensus: all three reviewers converged on:
     • app/services/datasets.py:182 — f-string SQL interpolation (BLOCKER)
     • app/api/v1/reports.py:94 — user-supplied ORDER BY (BLOCKER)
[alpha] disagreements: 1 finding flagged only by Gemini (P3)
[alpha] full report: alpha show t_x91
```

**Backend deps:**

| Need | Status |
|---|---|
| Parallel child workflows | ✅ Temporal supports `start_child_workflow` |
| Meta-adjudicator for multi-LLM consensus | ✅ `ProviderReviewWorkflow` already merges Claude+Codex+Gemma reviews |
| New parent workflow: `FanoutChatCliWorkflow` | ❌ Net-new (~150 LOC Python) |
| Result aggregation schema | ✅ `RLExperience` already stores per-provider scores |

**Acceptance:**
- `--fanout` with N providers spawns N child tasks.
- `--merge` modes: `council` (consensus + disagreement), `first-wins`, `all`.
- Cost attribution rolls up per-provider and total at parent task level.

**Effort:** 1 week (new workflow + CLI flag + result-formatting).

### 3. Quota fallback chains (`alpha run --providers a,b,c`)

**User story:** "Just complete the task — I don't care which LLM. If Claude
hits quota, fall over to Codex automatically."

**CLI UX:**

```
$ alpha run "scaffold the orders service" --providers claude,codex,opencode
[alpha] claude → quota_exceeded after 12 tool calls
[alpha] failing over to codex…
[alpha] codex completed in 4m12s
```

**Backend deps:**

| Need | Status |
|---|---|
| Quota detection + classify per provider | ✅ `_classify_error` in cli_executors |
| Fallback chain in `CodeTaskWorkflow` | ✅ #245 shipped — autodetect + quota_fallback |
| CLI flag to override chain | ❌ Just expose the existing primitive |

**Acceptance:**
- `--providers claude,codex,opencode` overrides tenant-default chain.
- Each fallback emits an SSE event so `alpha watch` shows the cascade.

**Effort:** 2 days (pure CLI plumbing; backend already does it).

### 4. Memory-first primitives (`alpha recall`, `alpha remember`)

**User story:** "I want to query everything my team has ever taught the
agents — code patterns, decisions, runbooks — from the terminal."

**CLI UX:**

```
$ alpha recall "what's our standard FastAPI error handler pattern?"
> Pattern (from 4 past chats, entity error_handler_pattern):
>   We use a custom APIError exception with error_code, http_status,
>   detail fields. Raised in services, caught by FastAPI exception
>   handler in app/main.py:188.
>
>   Related: error_codes_enum (entity), api_error_response_schema (entity)
>
>   Source: chat session s_a92 (2026-04-18, user=simon, agent=code-agent)

$ alpha remember "we standardized on httpx for outbound HTTP — never requests"
[alpha] saved as observation against entity http_client_policy (created)
[alpha] embedded for semantic recall
```

**Backend deps:**

| Need | Status |
|---|---|
| Semantic recall over knowledge graph + memory | ✅ `recall.py` (Phase 1 shipped) |
| Memory ingest / observation write | ✅ `record.py` (Phase 1 shipped) |
| Internal API endpoint | ✅ exists, called by chat path |
| CLI subcommand | ❌ thin wrapper around existing endpoints |

**Acceptance:**
- `alpha recall "<query>"` returns top-K observations + related entities with
  source attribution.
- `alpha remember "<fact>"` writes an observation; backfills embedding via
  Rust embedding-service.
- `--entity <id>` and `--scope tenant|user|agent` flags for scoping.

**Effort:** 2 days (wrapper + result formatting).

### 5. Multi-agent coalitions (`alpha coalition`)

**User story:** "We have a P1 incident — spin up an investigation team
(commander + forensics + comms drafter + postmortem writer) and stream their
collaboration live."

**CLI UX:**

```
$ alpha coalition incident-investigation --severity P1 --service orders-api
[alpha] dispatched coalition c_x9k2 with 4 agents:
       • Incident Commander
       • Forensics Analyst
       • Comms Drafter
       • Postmortem Writer
[alpha] live blackboard: https://agentprovision.com/collab/c_x9k2
[alpha] tail in terminal: alpha coalition watch c_x9k2

$ alpha coalition watch c_x9k2
[forensics] checking orders-api logs (last 30m)…
[forensics] error rate jumped 12x at 14:42 UTC, correlates with deploy v2.31
[commander] decision: rollback v2.31, notify on-call SRE
[comms] drafting status page update…
[postmortem] template seeded from incident timeline
```

**Backend deps:**

| Need | Status |
|---|---|
| CoalitionWorkflow on `agentprovision-orchestration` | ✅ shipped 2026-04-12 |
| Blackboard model + SSE event stream | ✅ Redis pub/sub → `/collaborations/stream` |
| Coalition patterns library | ✅ `incident_investigation`, `deal_brief`, `cardiology_case_review` |
| CLI dispatch + browser-open shortcut | ❌ thin wrapper |
| CLI live tail (consume same SSE) | ❌ new |

**Acceptance:**
- `alpha coalition <pattern>` dispatches with optional `--severity`, `--service`,
  custom blackboard seed.
- `alpha coalition list` shows available patterns.
- `alpha coalition watch <id>` tails the SSE event feed in the terminal.

**Effort:** 4 days (CLI + SSE consumer; no new backend).

### 6. Governance & policy gates — DEFERRED to Value Arbitration

> **Status update (2026-05-23, P0b):** the original §6 proposed an
> `alpha policy` CLI surface backed by the `agent_policies` table
> (migration 097). That table shipped with no enforcement call sites
> and recorded zero rows across 42 tenants over ~1 year of production.
> P0b deleted the model, the API endpoint, the `alpha policy`
> subcommand, and the table itself.
>
> Governance is moving to the Value Arbitration layer designed in
> `docs/plans/2026-05-23-value-arbitration-design.md`. Tenant norms
> ("never prescribe", "destructive actions require approval", etc.)
> are expressed as `standing=tenant_norm` signals with `direction`
> ∈ {`veto`, `avoid`, `pursue`} against typed `target` actions. The
> arbitration layer reconciles conflicts and emits a reasoned audit
> trace — something the old `agent_policies` schema could not do.
>
> The future CLI surface for governance will read arbitration
> outcomes, not policies — definition TBD with the ValueArbitration
> rollout. Rate limiting remains on `core.rate_limit.limiter` (per
> endpoint, already operational). Allowed-tools gating remains on
> `agent.tool_groups` (hardened in P0a).

### 7. Cost & usage attribution (`alpha usage`, `alpha costs`)

**User story:** "How much did my team spend on AI this month, broken down
by provider and by agent?"

**CLI UX:**

```
$ alpha usage --team backend --period mtd
provider          tokens_in    tokens_out   cost_usd
─────────────────────────────────────────────────────
claude            1.2M         180K         $14.20
codex             890K         95K          $8.40
gemini            2.1M         210K         $3.10
opencode (local)  4.6M         1.1M         $0.00
copilot           —            —             subscription
─────────────────────────────────────────────────────
total             8.8M         1.6M         $25.70

$ alpha costs --agent code-agent --period 7d
day        tasks  cost_usd  p95_latency_ms
2026-05-06    18  $1.24     12,400
2026-05-07    22  $1.81     11,800
...
```

**Backend deps:**

| Need | Status |
|---|---|
| `cost_usd` + `input_tokens` + `output_tokens` per task | ✅ #174 just shipped |
| Aggregation endpoints (per-team, per-agent, per-provider, per-period) | ⚠️ partial — `agent_performance_snapshots` has hourly rollups but no team aggregation |
| Per-team grouping requires `team_id` join | ⚠️ `agent.team_id` exists (ALM) but tasks aren't team-tagged yet |
| CLI table renderer | ❌ trivial |

**Acceptance:**
- `alpha usage` defaults to current tenant, current month-to-date.
- `--team`, `--agent`, `--provider`, `--period`, `--group-by` flags.
- `--json` for machine-readable output.

**Effort:** 4 days (1d CLI, 3d backend aggregation endpoints).

### 8. Recipes — Helm charts for AI workflows (`alpha recipes`)

**User story:** "I want to install + run + schedule pre-built AI workflows
(daily briefings, competitor watch, code reviews) without writing JSON."

**CLI UX:**

```
$ alpha recipes
daily-briefing       Calendar+inbox+monitors → Slack DM
competitor-watch     Scrape competitors, news, ads → notifications
cardiac-report       Echo PDF → DACVIM report → Google Doc
code-review          Multi-LLM PR review with memory
deal-pipeline        Discover → score → research → outreach → advance → sync

$ alpha recipes describe daily-briefing
daily-briefing (template_id=tpl_db1, native, installs=147)
  Pulls calendar + inbox + monitor alerts each morning, drafts a
  prioritized briefing, sends as DM via Slack.

  Required integrations: google_calendar, gmail, slack
  Optional: custom_prompts/morning_briefing.md

$ alpha recipes run daily-briefing --schedule "0 8 * * 1-5"
[alpha] installed recipe daily-briefing → dynamic_workflow w_d1
[alpha] cron schedule active: 0 8 * * 1-5 (weekdays 8am)
[alpha] first run preview: alpha recipes run daily-briefing --dry-run
```

**Backend deps:**

| Need | Status |
|---|---|
| `dynamic_workflows` table + 26 native templates | ✅ shipped |
| `install_template` MCP tool / API endpoint | ✅ |
| Cron trigger support | ✅ |
| CLI subcommand | ❌ thin wrapper |
| `--dry-run` validation | ✅ test-console pattern exists; expose via CLI |

**Acceptance:**
- `alpha recipes` lists native + community recipes.
- `alpha recipes describe <id>` shows required integrations + sample output.
- `alpha recipes run <id>` installs + dispatches (optionally with `--schedule`).
- `alpha recipes uninstall <id>` removes the dynamic workflow.

**Effort:** 5 days (CLI + integration awareness check before activation).

## Phased rollout (six weeks)

The design optimizes for *one demo-able win per phase*, so we can show
progress without waiting for the full eight-command surface.

### Phase 1 (Weeks 1–2) — The wedge demo

- **Ship:** `alpha run`, `alpha watch`, `alpha cancel`, `alpha run --providers`, `alpha run --fanout`
- **Backend:** `FanoutChatCliWorkflow` (new), unify task-status endpoint, SSE
  consumer in CLI
- **Demo:** the headline `alpha run --fanout --background` flow

This phase alone is **the entire competitive moat** vs leaf CLIs. Everything
after is depth.

**Phase 1 status (2026-05-18):** SHIPPED. CLI surfaces all live; real
Temporal dispatch wired for the `--fanout <cli>` path in PR #573.
Plain `alpha run "..."` and `alpha run --providers a,b,c` still hit
the Phase-1 synthetic stub — closing those out is the top of Phase 3
in the delegation-pattern doc.

### Phase 2 (Weeks 3–4) — Memory + governance

- **Ship:** `alpha recall`, `alpha remember`, governance via Value Arbitration
  (replaced the original `alpha policy show` proposal — see §6 for context)
- **Backend:** None new for `alpha recall`/`alpha remember` — wrappers around
  existing endpoints. Governance routes through the ValueArbitration layer
  whose CLI surface ships with that rollout.
- **Demo:** "I asked the agent to drop the prod DB. It blocked me, asked
  for approval, and gave me a task ID to resume from when approved."
  *(Will land on ValueArbitration `tenant_norm` signals rather than the
  deprecated `agent_policies` table.)*

**Phase 2 status (2026-05-23):** PARTIALLY SHIPPED. `alpha recall`
and `alpha remember` are live in v0.7.5. `alpha policy show` and the
backing `agent_policies` table were removed in P0b (2026-05-23) after
production audit found zero rows across 42 tenants and zero
enforcement call sites. Governance was reassigned to the
ValueArbitration design (`docs/plans/2026-05-23-value-arbitration-design.md`).
Cross-CLI consensus review (`alpha review`) shipped 2026-05-18 as
PR #574 — wasn't in the original Phase 2 list but slots here because
it's a governance-shaped surface (consensus before reporting).

### Phase 3 (Weeks 5–6) — Coalitions + recipes

- **Ship:** `alpha coalition list/run/watch`, `alpha recipes list/describe/run/uninstall`
- **Backend:** None new — both features fully shipped
- **Demo:** "We had a P1 incident. One command spun up four collaborating
  agents and streamed their blackboard live."

### Phase 4 (Week 7+) — Cost surfaces

- **Ship:** `alpha usage`, `alpha costs`, `alpha costs --by team`
- **Backend:** Team-tagged tasks + aggregation endpoints
- **Demo:** "Here's your team's monthly AI bill, broken down by provider,
  agent, and tenant — pulled from one CLI command."

## Out of scope (deliberately)

| Item | Why |
|---|---|
| `alpha policy` (any form) | Removed in P0b 2026-05-23. Governance lives in ValueArbitration (`docs/plans/2026-05-23-value-arbitration-design.md`); rate limiting in `core.rate_limit.limiter`; tool gating in `agent.tool_groups`. |
| `alpha agent create` interactively | Already exists; web wizard is the canonical creation flow |
| Voice-driven `alpha` | Luna desktop client owns voice; CLI is keyboard-first |
| `alpha recipes publish` (community contribution) | Phase 5 — needs review/moderation pipeline |
| Inbox-monitor-style `--watch-folder` polling | Use existing CompetitorMonitorWorkflow + recipe install |

## Open questions

1. **`alpha run` default provider** — auto-detect from tenant, or require explicit?
   *Recommend:* auto-detect with `--provider best` as the default; tenant
   admins set `default_cli_platform` in tenant_features.
2. **Cancellation semantics on fanout** — if I cancel the parent, do children
   cancel too?
   *Recommend:* yes, propagate cancel; document `--no-cascade-cancel` as opt-out.
3. **CLI streaming protocol** — SSE over HTTPS via Cloudflare tunnel hits
   the 524 timeout known from chat. The same constraint is discussed in
   `apps/api/app/services/cli_session_manager.py` and
   `docs/plans/2026-05-09-resilient-cli-orchestrator-design.md`; the
   async chat-result pattern from that work should be the backbone, and
   `alpha watch` ultimately polls + Server-Sent-Event hybrid.
4. **Tenant context switching** — `alpha --tenant prod run …` requires the
   user to have a token for that tenant. Need `alpha tenants list` + `alpha tenant use`
   ergonomics. *Not blocking Phase 1.*
5. **Recipe parameter prompts** — when a recipe needs config (e.g. Slack
   channel), should `alpha recipes run` open an interactive prompt or require
   `--param channel=#sre`? *Recommend:* both — interactive by default,
   `--param k=v` for scripting.

## Acceptance for the doc itself

- [ ] Each command has a real backend route or a labeled gap.
- [ ] No design item depends on unbuilt platform features.
- [ ] Effort estimates sum to ≈6 calendar weeks for Phases 1–3, plus
      Phase 4 (cost surfaces) in week 7+. Total: ≈7 weeks for one engineer.
- [ ] Headline demo is reproducible end-to-end after Phase 1.

## Companion work

- **Prototype PR** (branched off this design): Phase 1 `alpha run --fanout`
  end-to-end. Lands shortly after this doc.
- **Memory note**: `feedback_cli_differentiation_eight.md` — pin the
  positioning frame ("alpha is kubectl for agents, not a better claude").
