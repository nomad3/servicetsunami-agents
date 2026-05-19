# `alpha review` — Cross-CLI Consensus Code Review (Phase 1)

Status: Phase 1 plumbing landed. End-to-end CLI fanout depends on
task #287 (real `alpha run` dispatch).

## Motivation

The user already runs `superpowers:code-reviewer` on every PR. The
gap is that *one* CLI reviewing the diff can miss or fabricate
issues; in practice the user wants two or three CLIs to agree before
spending time on a finding.

`alpha review <ref>` formalizes that: fan the same review prompt out
to all active CLIs, **aggregate findings**, and return only what at
least two CLIs agree on. Operator fixes the agreed findings, calls
`alpha review reply <id> <new-ref>`, and loops until consensus =
"no agreed findings" or `--max-rounds` is exhausted.

## Primitive reuse — what existed vs what's new

The Coalition + Blackboard substrate from PR #182–#205 covers most of
this:

| Need                                  | Existing primitive                                            | This PR |
|---------------------------------------|---------------------------------------------------------------|---------|
| Per-CLI raw output, append-only audit | `Blackboard` + `BlackboardEntry`                              | reuse   |
| Round-tracked task with rollback      | `CollaborationSession` (rounds_completed, status)             | reuse   |
| Cross-pod Temporal dispatch           | `dispatch_coalition` pattern in `agent_router.py`             | clone   |
| Pattern-based role routing            | `CoalitionWorkflow` (sequential phases)                       | new fork|
| Consensus aggregation                 | (none — Coalition's "consensus_reached" is a single bool)    | **new** |
| ReviewWorkflow record                 | (none — Coalition rows don't surface aggregated findings)    | **new** |

What's *new* in this PR:

- **`reviews_coalitions` table** (migration 137) keyed by review_id —
  one row per `alpha review` invocation. Snapshot of `findings` and
  `agreed_findings` cached here so the read path doesn't re-walk the
  blackboard on every poll.
- **`review_service.aggregate_findings(...)`** — the consensus
  heuristic. Per-CLI findings are clustered by `(file, line_range
  overlap, Jaccard ≥ 0.4 on description tokens)`; clusters of size
  ≥ 2 become agreed findings.
- **`ReviewWorkflow`** — parallel fanout over `clis` via
  `asyncio.gather` over child `ChatCliWorkflow` handles. Differs
  from `CoalitionWorkflow` which iterates phases sequentially.
- **`alpha review` CLI** — start / status / reply / watch / list.

The Coalition primitive *could not* represent the loop directly
because:

1. CoalitionWorkflow's phases are ordered (planner → critic →
   verifier); reviews are a flat parallel vote.
2. CoalitionWorkflow's consensus is a single boolean on the last
   step; reviews need a structured `agreed_findings` list with
   `cli_set` per cluster.
3. The reply-loop with `updated_ref` doesn't fit
   CoalitionWorkflow's "advance phase" semantics.

So `ReviewWorkflow` is a sibling, not a fork — it reuses
`ChatCliWorkflow`, `Blackboard`, the same Temporal queue, and the
same SSE plumbing.

## Wire surface

```
POST /api/v1/reviews/start         body: ReviewStartRequest
GET  /api/v1/reviews                ?status=&limit=
GET  /api/v1/reviews/{id}           → ReviewState
POST /api/v1/reviews/{id}/reply    body: {updated_ref}
POST /api/v1/reviews/{id}/record   body: {cli, raw_text, findings?}  (workflow-internal)
GET  /api/v1/reviews/{id}/events    SSE: review_snapshot + transitions
```

`record` is the activity sink — `ReviewWorkflow`'s
`record_review_finding` activity calls this to commit each CLI's
output. The consensus aggregator runs synchronously inside
`record_cli_findings` when the last expected CLI reports.

## CLI

```
alpha review start <ref> [--clis claude,codex,gemini] [--scope bugs+security]
                         [--max-rounds 3] [--background] [--stdin]
alpha review status <id>
alpha review reply <id> <new-ref>
alpha review watch <id>
alpha review list [--status awaiting_response] [--limit 20]
```

`<ref>` is opaque to the server. The CLI accepts:
- a PR number (`#570`)
- a commit SHA
- a `path/to/file.py:50-100` range
- `--stdin` (hashes piped content to `stdin://<sha256>`)

## End-to-end UX (what `alpha review start #570` looks like)

```
$ alpha review start "#570" --clis claude,codex,gemini --max-rounds 3
[alpha] review dispatched
       review_id: 6f3a...
       ref: #570
       clis: claude, codex, gemini
       Cross-CLI review dispatched to 3 CLI(s). Poll GET /api/v1/reviews/{id} or stream /events.
       follow with: alpha review status 6f3a... (or `alpha review watch 6f3a...`)

# … workflow fans out to all 3 CLIs in parallel on agentprovision-orchestration queue,
# each child ChatCliWorkflow runs the review prompt with the same #570 ref. As each CLI
# finishes, ReviewWorkflow.record_review_finding activity POSTs the raw text to
# /reviews/{id}/record, which appends to the blackboard and runs the consensus aggregator
# when the last CLI lands. Status transitions to awaiting_response.

$ alpha review status 6f3a...
[alpha] review 6f3a...
       ref: #570
       status: awaiting_response (round 1/3)
       agreed_findings:
          1. [BLOCKER] apps/api/main.py:42-50 — SQL injection in login query  (flagged by: claude, codex)
          2. [IMPORTANT] apps/api/auth.py:7 — missing input validation       (flagged by: claude, codex, gemini)

# Operator (Claude Code) reads the findings, applies fixes, commits, and replies:
$ alpha review reply 6f3a... "#570-rev2"
[alpha] reply submitted — review 6f3a... now running (round 1/3)

# Workflow fires again with the new ref; aggregator runs again; either consensus stops
# (zero agreed_findings) → status=done, or another awaiting_response round.
```

## Dependency on #287

`ReviewWorkflow` dispatches child `ChatCliWorkflow`s on the
`agentprovision-code` queue. Real CLI execution requires the
`alpha run` real-dispatch fix in task #287. Until that lands:

**Testable now**

- Pure consensus aggregator (`aggregate_findings`)
- Text parser (`parse_findings_from_text`)
- Round lifecycle: start → record per CLI → consensus →
  awaiting_response → reply → done (driven via `/record`)
- Schema validation, tenant isolation, idempotency
- Migration 137 applied + index shape
- CLI subcommand parsing (Rust)

**Testable after #287**

- Full end-to-end fanout: `alpha review start #570 --clis
  claude,codex,gemini` actually runs three CLI subprocesses
- SSE event ordering under real Temporal load
- Cost rollup per review (depends on `usage_costs` integration)

Operators can still drive the full loop today by POSTing to
`/reviews/{id}/record` directly (which is what the test suite does)
or by feeding the system mocked CLI outputs while #287 finishes.

## Files

- `apps/api/migrations/137_reviews_coalitions.sql`
- `apps/api/app/models/review_coalition.py`
- `apps/api/app/schemas/review.py`
- `apps/api/app/services/review_service.py` — consensus aggregator
- `apps/api/app/services/review_dispatch.py` — Temporal shim
- `apps/api/app/api/v1/reviews.py` — wire surface
- `apps/api/app/workflows/review_workflow.py`
- `apps/api/app/workflows/activities/review_activities.py`
- `apps/api/app/workers/orchestration_worker.py` — registration
- `apps/api/tests/test_reviews_coalition.py`
- `apps/agentprovision-cli/src/commands/review.rs`
- `apps/agentprovision-cli/src/cli.rs` + `commands/mod.rs`
