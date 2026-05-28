# Platform Core-Primitives Smell Report — Design

| Field | Value |
|---|---|
| Date | 2026-05-28 |
| Status | **APPROVED** — spec-document-reviewer (iter-2 APPROVED) + Luna (round-3 APPROVED) |
| Author | Simon Aguilera (via Claude Code) |
| Reviewers | Luna (supervisor agent), spec-document-reviewer subagent |
| Scope axis chosen | C — *evidence-first smell report*, no code changes this round |
| Successor artifact | Implementation plan (writing-plans) per ranked finding |

## 1. Goal

Produce **one ranked markdown artifact** that tells us, with evidence, which parts of the agentprovision platform core primitives are most worth cleaning, refactoring, or deleting next. The artifact must be small enough that Luna can review it end-to-end in one session.

**Non-goals (this round):**
- No production code is deleted, moved, or rewritten in this round.
- No migrations are written.
- No model/schema changes.
- We are not picking a winner among existing patterns — we are surfacing where reality diverges from the documented canonical patterns.

The output of *this* spec is the report. The output of the *next* cycle (writing-plans + execution) is the actual cleanup PRs, one per high-value finding.

**One sanctioned exception:** §§3.1, 3.2, 3.3, 3.4, 3.5 require small deterministic scripts committed to `scripts/smell/` (Python AST scans + one Node script for React routing + one log-capture wrapper). That tooling is the only code change this round; it exists so each dimension's re-runs are reproducible. Every script reference in §§3.x is to a `scripts/smell/*.py|.js` file authored as part of this round.

## 2. Inputs

| Input | Source | Notes |
|---|---|---|
| Codebase | `apps/api`, `apps/mcp-server`, `apps/code-worker`, `apps/web`, `apps/luna-client`, `apps/agentprovision-cli`, `apps/agentprovision-core`, `apps/embedding-service`, `apps/memory-core`, `apps/device-bridge`, `apps/docs` | `.venv`, `node_modules`, `target/`, `__pycache__/` excluded everywhere |
| Canonical patterns | `CLAUDE.md`, `docs/architecture/*.md` (incl. `alpha_cli_kernel.md`, `dashboard.md`, `workspace.md`) | Treated as ground truth — drift = pattern violation |
| Live errors | `docker logs` for the 5 canonical containers (all under the `agentprovision-agents-*-1` prefix per the 2026-05-13 rename): `agentprovision-agents-api-1`, `agentprovision-agents-code-worker-1`, `agentprovision-agents-mcp-tools-1`, `agentprovision-agents-embedding-service-1`, `agentprovision-agents-memory-core-1` — last 72h | Filtered for `ERROR/WARNING` across all three log formats (§3.4); preflight must confirm every container name resolves via `docker ps` |
| Recent plans | `docs/plans/2026-05-*.md` | To distinguish "deliberately dropped" from "abandoned mid-flight" |
| Migration history | `apps/api/migrations/*.sql` + `_migrations` table | A migration in the dir but not the table is dead; a table not referenced by any model is suspect |

## 3. Dimensions

Five independent dimensions. Each produces a section in the final report with a **uniform finding shape**:

```
### F<N> — <one-line title>
- **Where:** <file_path:line_range or component>
- **Evidence:** <concrete output — grep hit, log fingerprint, LOC, missing reference>
  - **Reproducer:** <exact command from §3.x method list that produced this hit>
- **Why it smells:** <one sentence>
- **Suggested action:** <delete | refactor | document | leave>
- **Effort:** S (≤1 PR) / M (2–3 PRs) / L (multi-PR plan)
- **Risk if left:** low / med / high
- **Blast radius:** small / medium / large — *small* = single module, ≤2 import sites; *medium* = one service/component or one route/page, ≤10 import sites; *large* = cross-cutting (public HTTP API, JWT scope, DB schema, MCP tool contract, multiple services). Reporter cites the import-site or consumer count that justified the bucket.
```

### 3.1 Dead Code
- **Method (every check is one command):**
  - **Unmounted routes:** for each `apps/api/app/api/v1/*.py`, check it's imported by `apps/api/app/api/v1/routes.py` (or the package `__init__`). Reproducer: `python3 scripts/smell/unmounted_routes.py` (scaffolded under §1 exception) which prints `<file>: unmounted` for any missing import.
  - **Unimported service/class symbols:** `vulture apps/api/app/services/ --min-confidence 80` if `vulture` available; fallback: `python3 scripts/smell/unimported_symbols.py apps/api/app/services` (same AST scan).
  - **Unregistered MCP tools:** `grep -L 'register_tool\|@mcp_tool' apps/mcp-server/src/mcp_tools/*.py` cross-referenced against `apps/mcp-server/src/mcp_tools/__init__.py` exports.
  - **Dropped Temporal workflows:** for each class in `apps/api/app/workflows/*.py` (and `apps/code-worker/workflows.py`), confirm it appears in a worker's `workflows=[...]` list. Reproducer: `python3 scripts/smell/unregistered_workflows.py`.
  - **Dropped React pages:** for each `apps/web/src/pages/*.js`, check it's referenced in `apps/web/src/App.js` routing table. Reproducer: `node scripts/smell/unrouted_pages.js`.
  - **Migration ↔ DB drift:** DB list via `docker exec agentprovision-agents-db-1 psql -U postgres agentprovision -t -c "SELECT filename FROM _migrations ORDER BY filename;"` (preflight must confirm container reachable + `_migrations` table exists; if DB unreachable, the dimension reports a **degraded** finding rather than empty, so the aggregator does not silently swallow). Files list: `ls apps/api/migrations/*.sql | xargs -n1 basename | sort`. Diff either way is a finding.
- **Acceptance:** every finding cites one of the commands above + a specific symbol/file/migration.

### 3.2 AI Slop
- **Method (each finding cites the exact command that produced it):**
  - **Empty/swallowing except:** `grep -rnE 'except\s+\w*Exception[^:]*:\s*$' apps/ --include='*.py' -A1 | grep -B1 -E '^\s*(pass|return( None)?)\s*$'` — handlers that swallow without re-raise or log.
  - **Multi-name re-export wrappers:** `grep -rnE '^from\s+\S+\s+import\s+\*' apps/ --include='*.py'` plus AST scan (`python3 scripts/smell/reexport_only.py`) flagging any `*_service.py` / `*_manager.py` / `*_client.py` whose entire body is a single `from … import *` (or N re-exports and no logic).
  - **Low-arity "helpers":** for each `apps/api/app/services/*helper*.py`, `*utils*.py`, `*common*.py`, run `grep -rE "from app\.services\.<name> import" apps/ | wc -l`; flag any module with ≤2 importers.
  - **Duplicate scaffolds:** `find apps/api/app/api/v1 -name '*.py' -exec sh -c 'head -30 "$0" | shasum' {} \; | sort | uniq -c -d` — identical openings (imports + decorators) signal copy-paste templates; the duplicates are listed by hash.
  - **Docstring-vs-name redundancy:** `python3 scripts/smell/docstring_redundancy.py apps/` — AST script that flags functions whose docstring first sentence either equals `"<name>."` or restates the symbol name in prose. Script is committed so re-runs are deterministic.
  - **Hedging/AI-tone in comments:** `grep -rnE '#.*\b(just|simply|basically|essentially|really|very|honestly)\b' apps/ --include='*.py'` — sort by file, flag clusters (≥3 hits in one file).
- **Acceptance:** every finding cites the exact producing command from the list above, the matching hit (file:line + literal text), and the proposed replacement (or "delete entirely"). No taste-only verdicts; if no command from the list produced the hit, the finding is rejected.

### 3.3 Pattern Drift
Canonical patterns to check against (each must produce either ✓ or a list of violators):

- **Alpha CLI kernel** (`docs/architecture/alpha_cli_kernel.md`): any v1 route that contains business logic instead of delegating to the same Python entrypoint the `alpha` binary calls. Reproducer: `grep -rnE 'def\s+\w+\([^)]*\bsession\s*:\s*Session' apps/api/app/api/v1/*.py | grep -v 'await client\.\|service\.\|orchestrator\.'` — routes with raw DB access but no service/orchestrator delegation.
- **Single SSE per session** (`docs/architecture/dashboard.md`): any React component opening its own `new EventSource(...)` for session events instead of subscribing to `SessionEventsContext`. Reproducer: `grep -rnE 'new\s+EventSource\(' apps/web/src/ --include='*.js' --include='*.jsx' | grep -v 'SessionEventsContext'`.
- **MCP-as-leaf-protocol** (`memory/leaf_agent_inbound_via_mcp.md` referenced by CLAUDE.md): any leaf calling the API directly with the user JWT instead of via `apps/mcp-server` over SSE. Reproducer: `grep -rnE 'Authorization.*Bearer' apps/code-worker/ apps/luna-client/src/ apps/luna-client/src-tauri/src/ | grep -v 'X-Internal-Key\|MCP_API_KEY\|agent-scoped'`.
- **publish_session_event for human-watchable actions** (`CLAUDE.md` Alpha CLI Kernel §4): any agent/tool/workflow function that mutates state via `db.commit()` (or equivalent SQLAlchemy write) without emitting a `publish_session_event`. Reproducer: `python3 scripts/smell/missing_session_event.py apps/api/app/services/ apps/api/app/workflows/` — AST scan that, for each function containing a session write, reports a finding if no `publish_session_event` is called within the same function or any callee in the same module. Script is committed in Phase 0 (see §4).
- **RL experience logged for autonomous decisions** (`CLAUDE.md` Alpha CLI Kernel §5): any autonomous-decision path that selects an agent / route / platform / tool without logging an `rl_experience`. Reproducer: `python3 scripts/smell/missing_rl_log.py apps/api/app/services/` — AST scan that flags functions whose name matches `^(route|select|dispatch|pick|choose|fallback)_\w+$` (or whose docstring claims a routing/selection decision) and that return a chosen value without a call to `rl_experience_service.log_*` (or equivalent `record_*` helper). Script is committed in Phase 0.
- **`tenant_id` filter on multi-tenant queries** (`CLAUDE.md` "Multi-tenant Query Pattern"): any service query of a tenant-scoped model missing `.filter(Model.tenant_id == …)`. Reproducer: AST scan `python3 scripts/smell/tenant_filter_check.py apps/api/app/services/` — flags `db.query(<tenanted-model>)` chains without a tenant_id filter.
- **Workspace path-safety guards** (`docs/architecture/workspace.md` §3): any FastAPI route or service resolving a path under `WORKSPACES_ROOT` / `/var/agentprovision/workspaces` must (a) jail via `Path.resolve()` + `relative_to(tenant_root)`, (b) reject hidden segments (`.git`, `.env`, `node_modules`, `.venv`, `venv`, `__pycache__`), and (c) gate `scope=platform` on `is_superuser`. Reproducer: `grep -rnE 'WORKSPACES_ROOT|/var/agentprovision/workspaces' apps/api/app/` cross-referenced against `apps/api/app/api/v1/workspace.py` as the reference implementation.
- **Volume / PVC discipline** (`CLAUDE.md` "Operational Notes" + `docs/architecture/workspace.md` §1): no CI workflow may run `docker volume prune` and no manifest may run `kubectl delete pvc` against the `workspaces` volume. Reproducer: `grep -rnE 'docker\s+volume\s+prune|kubectl\s+delete\s+pvc' .github/workflows/ scripts/ helm/` — any unguarded hit is an immediate blocker finding.
- **Acceptance:** each finding cites the canonical pattern doc + a file:line of the violation + the reproducer above.

### 3.4 Live Error Signal
- **Method:** `docker logs --since 72h <container>` for the 5 services above. Pipe through:
  - Capture **all three log shapes** the platform actually emits (verified during the 2026-05-27 session):
    - JSON envelope (uvicorn / structlog Python services): `grep -E '"level":\s*"(ERROR|WARNING)"'`.
    - Leading-timestamp + bare level (Rust services — codex CLI, embedding-service, memory-core): `grep -E '^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9:.Z+-]+\s+(ERROR|WARN(ING)?)\b'`.
    - Bare-prefix (neonize / legacy): `grep -E '^(ERROR|WARN(ING)?):'`.
    - Combined invocation reference: `python3 scripts/smell/log_errors.py --since 72h --container agentprovision-agents-api-1 --container agentprovision-agents-code-worker-1 --container agentprovision-agents-mcp-tools-1 --container agentprovision-agents-embedding-service-1 --container agentprovision-agents-memory-core-1`.
  - Normalize stack traces: strip line numbers, UUIDs, timestamps, request IDs.
  - Top-20 by **fingerprint** (the normalized message); each gets a count over the window.
  - Cross-reference each fingerprint against `apps/api/app/services/` (or the relevant package) to locate the producing call site.
- **Already-known bug fingerprints to confirm/refute** (seeded from the 2026-05-27 session):
  - `Rust recall failed (will reconnect next call): ... RST_STREAM with error code 8` (silent fallback to Python recall).
  - `Failed to log auto-quality RL: unsupported format string passed to NoneType.__format__`.
  - `Failed to refresh token: ... refresh token was already used` (was hot on tenant 752626d9; should now be quiet post-Codex reconnect on 2026-05-28).
  - WhatsApp `handoff: to_thread` followed by no reply send (stale neonize socket; the auto-restore handler currently only fires on `readonly database`).
  - Tenant feature `cli_quota_fallback_chain` referenced in code but column absent in `tenant_features`.
- **Acceptance:** every error finding has a fingerprint, an occurrence count over the window, and a candidate file:line for the producing call site.

### 3.5 Refactor Hotspots
- **Method (mechanical first pass):**
  - **Big files:** `find apps -name '*.py' -not -path '*/.venv/*' -not -path '*/__pycache__/*' | xargs wc -l | sort -n | tail -30`. Repeat for `*.js`, `*.jsx`, `*.rs` (excluding `node_modules`, `target`).
  - **Function density per big file:** for each file in the top-30, `grep -c '^\s*def ' <file>` (Python) / `grep -cE '^\s*(function|const)\s+\w+' <file>` (JS).
  - **Cyclomatic complexity:** `radon cc -a -s apps/api/app/services/ | sort -k 4 -rn | head -30` if `radon` available; fallback: `python3 scripts/smell/nesting_depth.py apps/api/app/services/` reporting top 30 functions by AST nesting depth.
- **Acceptance:** each finding includes file path, LOC, function count or complexity score, and a one-sentence "why this is too big to safely change."

## 4. Execution shape

**Three sequential phases:**

- **Phase 0 — Scaffolding (this session, before any fan-out).** Author and commit the deterministic scripts listed in §§3.1, 3.2, 3.3, 3.4, 3.5 under `scripts/smell/`. Each script is committed individually with a passing smoke test (the script runs against the repo and exits 0; even an empty findings array is fine — the test is "the script doesn't crash"). The Phase 1 subagents are strictly read-only and consume only scripts that exist on the branch as of the fan-out commit SHA, which the aggregator records.
- **Phase 1 — Parallel evidence collection** (the diagram below). Five Explore subagents fan out, each running the scripts and `grep`/`docker` commands in its dimension. Read-only.
- **Phase 2 — Aggregation & report write-up.** Sequential, in this session.

```
            ┌───────────────────────────────────────────┐
            │   Aggregator (this session, sequential)   │
            │   • collects findings JSON per dimension  │
            │   • verifies preflight succeeded           │
            │   • dedupes overlapping findings           │
            │   • ranks by (risk * blast_radius)/effort  │
            │   • writes one report.md                   │
            └─────────────▲───────────────▲─────────────┘
                          │               │
   ┌───────────┬──────────┴────────┬──────┴────────┬────────────┐
   │           │                   │               │            │
┌──▼──┐  ┌────▼────┐  ┌────────────▼──┐  ┌─────────▼──┐  ┌──────▼──────┐
│Dead │  │AI Slop  │  │ Pattern Drift │  │  Errors    │  │  Hotspots   │
│Code │  │ Explore │  │   Explore     │  │  Explore   │  │   Explore   │
└─────┘  └─────────┘  └───────────────┘  └────────────┘  └─────────────┘
  ↑          ↑                ↑                ↑                ↑
        Five parallel Explore subagents, read-only, evidence-only
```

- **Parallelism:** all 5 Explore subagents dispatched in one tool-call batch (per `dispatching-parallel-agents`).
- **Isolation:** each subagent gets its dimension definition + the inputs list + the finding shape; nothing else from this session.
- **Subagent output contract:** each subagent returns
  ```json
  {
    "preflight": {
      "commands_attempted": [{"cmd": "...", "exit": 0, "lines": 123}],
      "containers_seen": ["agentprovision-agents-api-1", ...],
      "input_set": "<short description of what was actually scanned>"
    },
    "findings": [ ...uniform finding shape from §3... ],
    "method_notes": "<≤200 words on how the dimension was scanned>"
  }
  ```
- **Aggregation:** sequential, in this session, no LLM judgment beyond ranking and dedupe. The aggregator **fails loud** if any dimension returns `findings: []` *without* a `preflight` that demonstrably reached its inputs (e.g. zero commands attempted, or every command exit ≠ 0).

## 5. Output artifact

- Path: `docs/reports/2026-05-28-core-primitives-smell-report.md`
- Layout:
  1. **Luna-summary** — ≤200 words at the top, plain prose, "here are the 5 fattest fish."
  2. **Top-10 ranked findings** with the finding shape from §3.
  3. **Full per-dimension findings**, grouped, each finding numbered `F<dim>.<n>`.
  4. **Appendix A** — methods log (commands run, log windows scanned, vulture/grep invocations) + per-dimension preflight blocks.
  5. **Appendix B** — Luna consensus thread snapshot (session id, iterations, final verdict).

## 6. Luna consensus protocol

- **Transport:** `alpha chat send --agent cfb6dd14-1889-4751-b645-77bbd53c65c3 --no-stream --json` for the first turn; capture `session_id`; subsequent turns use `--session <id>` to keep one thread.
- **Round 1 input (critique-only):** full spec markdown + the literal prompt:
  > *You are reviewing a design spec. List specific objections only — missing dimensions, weak acceptance criteria, missing canonical-pattern checks, ambiguous protocol. If you have **no objections**, reply with exactly the single word `APPROVED` and nothing else. Otherwise list the objections as a numbered list.*
- **Round 2+ input:** the revised spec + Luna's prior objections + a diff of what changed. Same critique-only prompt.
- **Consensus signal:** Luna's reply, **trimmed and uppercased, must equal exactly `APPROVED`**. **No second branch.** Any other reply — including agreement-coloured prose — is treated as objections to address (round N+1) or, at round 3, surfaced to human with the full thread.
- **Open questions (§9) are a separate round AFTER consensus** — they are not sent in any of the consensus rounds, so the consensus check stays strict objection-or-APPROVED.
- **Cap:** 3 critique rounds. If no `APPROVED` by round 3, the current revision is committed with Luna's outstanding objections appended as Appendix B, flagged for human review.
- **Failure modes:** if Luna times out, returns a CLI quota/auth error, or replies with content unrelated to the spec, treat as "no consensus, surface to human."

## 7. Acceptance criteria for THIS spec (before we start executing)

1. ✓ Scope decomposed (axis C — smell report only, no code changes except §1 exception).
2. ✓ 5 dimensions defined with reproducible methods.
3. ✓ Uniform finding shape so the report is comparable across dimensions.
4. ✓ Execution shape that fits a single working session (parallel fan-out + sequential aggregation).
5. ✓ Luna consensus protocol with strict literal `APPROVED` signal + 3-round cap.
6. Spec-document-reviewer subagent: APPROVED.
7. Luna: APPROVED (or 3 rounds exhausted with reasoned final revision).

## 8. Risks

- **Subagent hallucination on dead code** — mitigated by requiring exact grep/SQL reproducibility per finding (§3.1).
- **Log volume blows context** — mitigated by fingerprinting + top-20 truncation in §3.4.
- **Pattern drift becomes opinion-flame** — mitigated by sourcing every "canonical pattern" from a doc file, not from a reviewer's preference.
- **Luna disagrees in ways that would expand scope** — protocol §6 caps iterations and surfaces to human rather than letting the spec grow forever.
- **The report itself is shelfware** — mitigated by the spec contract: the next cycle is writing-plans on the top findings.
- **Subagent context starvation / silent empty findings.** A subagent that can't reach docker, finds a renamed container (post-rename to `agentprovision-agents-*`), or runs in a sandbox without shell access will return an empty findings array indistinguishable from "dimension clean." **Mitigation:** every subagent must emit the `preflight` block in §4; the aggregator **fails loud** when any dimension returns zero findings without a preflight that successfully reached its inputs.

## 9. Open questions for Luna (sent only AFTER consensus, not in any critique round)

1. Is there a sixth dimension worth scanning? (e.g. test-suite smell, observability gaps, secret-hygiene)
2. Are any of the 5 dimensions overlapping enough to merge?
3. Should the report rank by *risk* or by *effort/value*?
4. Any canonical pattern in CLAUDE.md or `docs/architecture/` that we forgot to lift into §3.3?

## Appendix B — Consensus thread snapshot

**Luna agent UUID:** `cfb6dd14-1889-4751-b645-77bbd53c65c3`
**Chat session id:** `d9e5b6ad-1f33-4624-bb71-f65908c2716e`
**Platform:** Codex CLI on `gpt-5.5` (Pro subscription, $200/mo tier, verified the same day)
**Rounds to consensus:** 3 of 3 (cap not exhausted).
**Spec-document-reviewer iterations:** 2 (iter-1 issued 6 findings, iter-2 APPROVED with 1 nit which was fixed).

### Round 1 — Luna objections (5)
1. §3.3 `publish_session_event` & `rl_experience` checks lacked deterministic scripts.
2. §4 ranked by `blast_radius` but the uniform finding shape had no such field.
3. §3.1 migration-drift check did not specify DB target / connection.
4. §1 said scripts were authored this round but §4 said subagents were read-only — no scaffolding phase defined.
5. §3.4 log-grep matched only JSON `"level"` fields, missing Rust-service & legacy plaintext formats.

→ Addressed in commit `df37bb03` ("address Luna round-1 objections O1–O5").

### Round 2 — Luna objections (2)
1. §3.4 added `scripts/smell/log_errors.py` but §1 / §4 Phase 0 tooling exception did not list §3.4.
2. §2 container names omitted the `agentprovision-agents-*` prefix while §3.4 used the full names — pick one canonical naming scheme.

→ Addressed in commit `8aa2c655` ("address Luna round-2 R1+R2").

### Round 3 — Luna reply
> `APPROVED`

Consensus locked. Open questions in §9 are deferred to the report execution session.
