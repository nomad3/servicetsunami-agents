# Alpha CLI delegation pattern — three primitives, one mental model

**Date:** 2026-05-18
**Status:** Phase 2 mostly shipped; Phase 3 extensions queued (see end of doc).
**Predecessors:**
- [`2026-05-13-ap-cli-differentiation-roadmap.md`](2026-05-13-ap-cli-differentiation-roadmap.md)
- [`2026-05-18-alpha-run-real-dispatch.md`](2026-05-18-alpha-run-real-dispatch.md)
- [`2026-05-18-alpha-review-consensus.md`](2026-05-18-alpha-review-consensus.md)

## What this doc is for

`alpha` now exposes three distinct delegation patterns at the terminal.
Each maps to a different latency profile and a different "do the LLMs
have to agree?" answer. This doc is the canonical place to look up
which pattern fits which task — and to track which pieces are wired
end-to-end vs. queued.

| Pattern | CLI command | When to use | Backed by |
|---|---|---|---|
| **A. Short turn** | `alpha chat send "..."` | Single-shot Q&A, < 100s of model time | SSE over HTTPS, default agent |
| **B. Long turn** | `alpha run --fanout <cli> "..." --background` | Multi-minute work, must survive disconnect | Temporal `FanoutChatCliWorkflow` on `agentprovision-code` queue |
| **C. Consensus turn** | `alpha review start "<ref>" --clis a,b,c` | Code review / due-diligence — ≥ 2 CLIs must agree before reporting | `ReviewWorkflow` + Blackboard + `reviews_coalitions` table |

All three flow through the same agentprovision.com control plane;
they differ in **how the platform shapes the call** and **what gets
returned to the operator**.

## What shipped today (2026-05-18)

### PR #573 — `alpha run --fanout <cli>` real Temporal dispatch

- Single-provider real dispatch (the 90% case) is **LIVE** behind
  `USE_REAL_FANOUT_WORKFLOW=true` (set in `apps/api/.env`).
- `alpha run --fanout claude_code "<prompt>"` end-to-end works:
  CLI → `POST /api/v1/tasks-fanout/run` → `FanoutChatCliWorkflow`
  with N=1 → child `ChatCliWorkflow` → real CLI subprocess → result
  visible via `alpha watch <task_id>`.
- `--background` returns `{task_id, status:queued}` immediately.

**Not yet wired through to real dispatch:**

| Surface | Status | Workaround |
|---|---|---|
| Plain `alpha run "..."` (no `--fanout`) | Synthetic Phase-1 stub | Use `--fanout <cli>` |
| `alpha run --providers a,b,c` (fallback chain) | Synthetic Phase-1 stub | Use `--fanout <cli>` (parallel, not sequential) |
| `--merge council` LLM adjudication for N>1 | Returns raw list of N outputs | Read the list, summarise manually |
| `--merge all` aggregation | Returns raw list of N outputs | (Same as above — it's the same shape today) |
| `--timeout N` → Temporal `execution_timeout` | CLI honours the foreground tail deadline only; backend execution_timeout fixed at 180m | Increase 180m fixed value in worker if you really need 4h+ |
| `--agent <UUID>` propagation | Accepted by CLI, **not** pushed into `ChatCliInput` (worker warns + falls back to tenant default) | Open a follow-up PR to extend `ChatCliInput` |
| `--events <path>` JSONL stream | State transitions LIVE; child-CLI tokens not yet routed | Foreground tail still gets tokens |

### PR #574 — `alpha review` cross-CLI consensus loop

- Subcommands: `start <ref>`, `status <id>`, `reply <id> <new-ref>`,
  `list`, `watch <id>`.
- Migration **139** (`reviews_coalitions`) — one row per `alpha review`
  invocation; snapshot of `findings` and `agreed_findings`.
- Consensus aggregator (`aggregate_findings`) clusters per-CLI findings
  by `(file, overlapping line range, Jaccard ≥ 0.4 on tokens)`;
  cluster size ≥ 2 → agreed finding.
- `ReviewWorkflow` is a sibling of `CoalitionWorkflow` — flat parallel
  fanout via `asyncio.gather` over child `ChatCliWorkflow` handles,
  vs. Coalition's sequential phases.
- Wire surface: `POST /reviews/start`, `GET /reviews`, `GET /reviews/{id}`,
  `POST /reviews/{id}/reply`, `POST /reviews/{id}/record`,
  `GET /reviews/{id}/events`.

**Known issue (workaround documented):**
`apps/api/app/services/review_dispatch.py::_runner` uses a daemon
thread + `asyncio.run` to fire the Temporal `Client.start_workflow`
call. In practice the workflow silently never starts. Manual
`start_workflow` works. The `/record` endpoint, table, aggregator,
and SSE are all live, so operators drive the loop directly via
`POST /reviews/{id}/record` while a hotfix lands — full recipe in
[`docs/cli/troubleshooting.md`](../cli/troubleshooting.md#review-stays-running-no-findings).
This is the single highest-priority follow-up in Phase 3.

### PR #575 — `temporalio>=1.10` SDK migration

`WorkflowExecutionDescription` flattened — no more
`.workflow_execution_info`. Touches the `alpha watch <id>` path.
Backwards-incompatible; orchestration worker must be redeployed on
the new SDK or `alpha watch` will `AttributeError`.

### PR #577 — explicit `activity_executor` for sync activities

Orchestration worker now constructs the `Worker` with an explicit
`ThreadPoolExecutor` as `activity_executor`. `load_review_state`,
`record_review_finding`, and `aggregate_findings` are sync activities;
Temporal 1.10 requires the executor or the activity poll silently
no-ops. Net effect: makes the review consensus loop actually drive
findings on the backend.

### PR #569 — Higgsfield CLI binary in `code-worker`

`@higgsfield/cli@0.1.40` lands at `/usr/bin/higgsfield` inside the
code-worker image. The **actual generation surface** is the
per-tenant MCP connector dispatch path (not the binary itself); the
binary is for parity with the leaf-CLI runtime detection model so
`alpha status --runtimes` can preflight it.

### PR #572 — platform-aware `format_allowed_tools` + Luna `higgsfield` tool group

`format_allowed_tools` now branches on CLI platform: Gemini uses
single-underscore (`mcp_x`), Claude uses double-underscore (`mcp__x`).
Migration **138** appends `higgsfield` to Luna's `tool_groups` so the
supervisor can route to the connector.

### PR #570 — async chat-result pattern (Cloudflare 524 workaround)

New endpoints to replace the SSE-only `/messages/stream` flow that
collides with Cloudflare's 524 idle-timeout:

- `POST /api/v1/chat/sessions/{sid}/messages/start` → returns `job_id`
- `GET /jobs/{id}/events` → polls events (queue-buffered, not SSE-only)
- `POST /jobs/{id}/cancel` → cancel a job

Migration **137** — `chat_jobs` table. Designed to replace
`/messages/stream` SSE for long-running turns. The CLI still uses
the SSE path today; opt-in feature flag to switch is queued in
Phase 3.

## The three patterns in detail

### Pattern A — `alpha chat send` (short turn, single LLM)

Default delegation. The CLI POSTs to a chat session endpoint and
streams the response back as SSE. Single CLI/agent. Total turn
typically < 30s; hard wall at the Cloudflare 524 deadline (~100s).

```bash
alpha chat send "summarise the Levi's MDM incident in 3 bullets"
alpha chat send "what about the second one?" --session <session_uuid>
alpha chat send "give me JSON" --no-stream --json
```

When to use: tight Q&A, "explain this", "draft this email", any turn
small enough that streaming completion is faster than waiting for a
queued workflow.

When **not** to use: anything that takes > 90s. Cloudflare will cut
the stream; the agent keeps running but you'll see a 524. Switch to
Pattern B.

### Pattern B — `alpha run --fanout` (long turn, durable)

The wedge feature. Tasks survive terminal close, network drops, even
laptop reboots — resume from any other machine on the same account.

```bash
# Single CLI, the 90% case
alpha run --fanout claude_code "refactor X" --background
alpha watch <task_id>          # tail from anywhere
alpha cancel <task_id>          # parent + all fanout children

# Multi-CLI parallel (raw outputs as a list until council adjudication ships)
alpha run --fanout claude_code,codex,gemini_cli "audit Y" --merge council --background

# Foreground with custom deadline (default 1800s)
alpha run --fanout claude_code "..." --timeout 7200

# JSONL events for CI / supervisors
alpha run --fanout claude_code "..." --events ./events.jsonl --background
```

Backed by `FanoutChatCliWorkflow` (Temporal,
`agentprovision-code` queue) → N child `ChatCliWorkflow` handles → N
real CLI subprocesses. Cost attribution rolls up at parent task level.

When to use: any turn longer than a couple minutes; anything you want
to resume from a different host; anything where you want machine-readable
event JSONL.

### Pattern C — `alpha review start` (consensus turn)

Cross-CLI consensus code review. Fan the same prompt out to N CLIs in
parallel; surface only findings ≥ 2 of them agree on. Loop with
`alpha review reply <id> <new-ref>` until consensus = "no agreed
findings" or `--max-rounds` is exhausted.

```bash
alpha review start "#570" --clis claude_code,codex,gemini_cli --max-rounds 3
alpha review status <review_id>
alpha review reply <review_id> "#570-rev2"
alpha review watch <review_id>
alpha review list --status awaiting_response

# Or pipe a diff in
gh pr diff 570 | alpha review start --stdin --clis claude_code,codex
```

Backed by `ReviewWorkflow` (sibling of `CoalitionWorkflow`) +
Blackboard + `reviews_coalitions` table (migration 139). Findings are
returned with their `cli_set` so the operator sees which CLIs flagged
each cluster.

When to use: code review on a PR you don't trust a single LLM to read
alone (hallucination risk, fabricated findings). Due-diligence
analyses where the cost of a false positive is high.

When **not** to use: one-off "what's wrong with this snippet?" — that's
Pattern A.

## How the patterns compose

The three patterns aren't mutually exclusive. Two useful composites:

- **`alpha review` powered by `alpha run`:** `ReviewWorkflow`'s child
  CLIs *are* `ChatCliWorkflow` instances — the same primitive Pattern B
  uses. So once a hotfix lands for the threading bug, Pattern C is
  literally N parallel Pattern Bs with a consensus aggregator on top.
- **`alpha run --fanout` for research, then `alpha review` on the
  output:** a queued `alpha research <topic>` helper wraps the first
  half; you can manually feed the result into
  `alpha review start --stdin` to vet it across CLIs.

## Phase 3 — Extensions queued

Highest-priority follow-ups, in rough order of leverage:

1. **Fix the `dispatch_review_workflow` threading wrapper.** The
   single blocker preventing `alpha review` from actually firing its
   workflow. Daemon thread + `asyncio.run` does not start the
   Temporal client cleanly; replace with a proper
   `asyncio.create_task` from the request handler's loop (or move
   to a Celery-style queue dispatcher). Hotfix PR slated for
   tomorrow.
2. **`alpha review pr <N>` helper subcommand.** Wraps
   `gh pr diff N | alpha review start --stdin --clis ...` into one
   call. Removes the manual ref-string handling and the `#570` shell-
   comment footgun. The CLI side is a thin pre-processor over
   existing `review start --stdin`.
3. **`alpha research <topic> --provider gemini_cli` helper.** Wraps
   `alpha run --fanout gemini_cli "<topic>" --background` plus an
   immediate `alpha watch` for a one-line research-dispatch ergonomic.
   No new backend.
4. **Wire `--providers a,b,c` to real Temporal dispatch.** Currently
   only `--fanout` real-dispatches. `--providers` should hit
   `FanoutChatCliWorkflow` in *first-wins* sequential mode (quota-aware
   fallback chain semantics, mirroring `_resolve_cli_chain` from
   PR #245).
5. **Wire naked `alpha run "..."` (no `--fanout`, no `--providers`) to
   real Temporal dispatch.** Default provider resolved from
   `tenant_features.default_cli_platform`. This kills the last
   synthetic-stub path.
6. **`--fanout` merge modes:**
   - `--merge council` — LLM-summarises N reviews into agreed /
     dissenting structure (mirrors what `alpha review` does already,
     just for non-review prompts).
   - `--merge first-wins` — race semantics. First child to terminate
     wins, the rest are cancelled.
   - `--merge all` — return raw N outputs (current behaviour; just
     formalise as the explicit `all` mode).
7. **`alpha usage` — per-tenant token / cost meter.** Roadmap Phase 4
   item; backed by per-task `cost_usd` + token columns shipped in
   #174. Surface today is stubbed in `cli.rs` but doesn't yet return
   real aggregates.
8. **CLI feature flag for the new async-job chat pattern (PR #570).**
   Operator override during the migration window — `alpha chat send`
   keeps the SSE path by default but a `--async` flag (or env var)
   switches to the new `/messages/start` + `/jobs/{id}/events` flow.
   Eliminates the Cloudflare 524 for long chat turns without the
   user having to switch to `alpha run`.
9. **`--agent <UUID>` propagation through `ChatCliInput`.** Today the
   worker logs a warning and runs as the tenant default. Needs an
   `agent_id` field on `ChatCliInput` + worker-side wiring.
10. **`--timeout` → Temporal `execution_timeout`.** Plumb the CLI
    flag through to the workflow's actual deadline; today the
    backend is fixed at 180m and the CLI flag is foreground-tail-only.

## Open design questions

- Should `alpha review` and `alpha run --fanout --merge council` share
  their adjudication model? They have near-identical shape (N CLIs in
  parallel, structured consensus). A unified aggregator that
  parameterises "what counts as agreement" would let us collapse the
  two surfaces. Counter-argument: code review needs the structured
  `agreed_findings` schema and the reply-loop; arbitrary prompts
  don't. Likely answer: keep them separate but share the per-CLI
  fanout primitive (`FanoutChatCliWorkflow`).
- Default provider for naked `alpha run "..."`. Today the cheapest
  path is hard-coded `claude_code`; long-term it should read
  `tenant_features.default_cli_platform`. Tracked as item 5 above.
- Cancellation semantics on fanout — if the parent is cancelled, do
  children cascade? Today: yes. Document `--no-cascade-cancel` as
  opt-out if/when someone needs it. No demand yet.

## What's NOT changing

The three-pattern mental model — chat / run / review — is the stable
contract. Phase 3 fills in the corners; it doesn't reshape the
patterns. The CLI's job is to keep the patterns clean enough that
operators can pick the right one without reading this doc.
