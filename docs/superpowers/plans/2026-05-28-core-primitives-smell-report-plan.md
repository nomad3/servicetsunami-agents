# Core-Primitives Smell Report Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:subagent-driven-development` (recommended) or `superpowers:executing-plans` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Produce `docs/reports/2026-05-28-core-primitives-smell-report.md` — one ranked artifact identifying the platform's worst dead code, AI slop, pattern drift, error fingerprints, and refactor hotspots — without changing production code.

**Architecture:** Three sequential phases. Phase 0 scaffolds 11 deterministic scanners under `scripts/smell/` (the only code change this round, sanctioned by spec §1). Phase 1 fan-outs 5 read-only Explore subagents in one tool batch, each running its dimension's scripts against the branch fan-out SHA. Phase 2 aggregates the subagent JSON, fails-loud on context-starved dimensions, dedupes, ranks by `(risk × blast_radius) / effort`, and writes the report.

**Tech Stack:** Python 3.11 (AST: `ast`, `pathlib`), Node 20 (one routing scanner), `grep`/`docker`/`psql` shell, the `Agent` tool with `subagent_type: Explore` for fan-out.

**Spec:** `docs/superpowers/specs/2026-05-28-core-primitives-smell-report-design.md` (Luna-APPROVED round 3, spec-reviewer iter-2 APPROVED).

**Branch / PR:** `chore/core-primitives-smell-report` → PR #729.

---

## File Structure

| Path | Phase | Responsibility |
|---|---|---|
| `scripts/smell/__init__.py` | 0 | Marks package; empty. |
| `scripts/smell/_findings.py` | 0 | Shared `Finding` dataclass + JSON encoder. Every script emits via this helper so the aggregator parses one shape. |
| `scripts/smell/unmounted_routes.py` | 0 | §3.1 dead-code check: FastAPI route files not imported by `apps/api/app/api/v1/routes.py`. |
| `scripts/smell/unimported_symbols.py` | 0 | §3.1 unused public symbols in `apps/api/app/services/`. Wraps `vulture` if installed; AST fallback otherwise. |
| `scripts/smell/unregistered_workflows.py` | 0 | §3.1 Temporal workflow classes not in any worker's `workflows=[...]` list. |
| `scripts/smell/unrouted_pages.js` | 0 | §3.1 React pages under `apps/web/src/pages/` not referenced by `apps/web/src/App.js`. |
| `scripts/smell/migration_drift.py` | 0 | §3.1 migration↔DB drift with explicit preflight; `degraded` exit_summary if DB unreachable so Phase-2 fail-loud trips correctly. |
| `scripts/smell/reexport_only.py` | 0 | §3.2 multi-name wrappers: `*_service.py` whose body is only `from … import *` (or N re-exports). |
| `scripts/smell/docstring_redundancy.py` | 0 | §3.2 functions whose docstring restates the symbol name. |
| `scripts/smell/missing_session_event.py` | 0 | §3.3 functions doing a SQLAlchemy write but no `publish_session_event`. |
| `scripts/smell/missing_rl_log.py` | 0 | §3.3 routing/dispatch functions with no `rl_experience` log. |
| `scripts/smell/tenant_filter_check.py` | 0 | §3.3 `db.query(<TenantedModel>)` chains missing `.filter(... tenant_id ...)`. |
| `scripts/smell/log_errors.py` | 0 | §3.4 multi-format log capture wrapper (`docker logs` → 3 grep shapes → fingerprint). |
| `scripts/smell/nesting_depth.py` | 0 | §3.5 top-N functions by AST nesting depth. |
| `scripts/smell/run_dimension.sh` | 0 | Smoke-test runner: invokes every script with `--smoke` and asserts exit 0 + valid JSON. |
| `docs/reports/2026-05-28-core-primitives-smell-report.md` | 2 | The final deliverable. |

All Phase-0 scripts share an output contract enforced by `_findings.py`. This is the **spec §4 contract verbatim** (`commands_attempted` shape, `containers_seen`, `input_set`, `method_notes` at top level) plus the convenience extension `preflight.exit_summary` (plan-only) used by Task 1.1's fail-loud rule:

```json
{
  "preflight": {
    "commands_attempted": [ { "cmd": "<exact shell>", "exit": 0, "lines": 123 } ],
    "containers_seen": [ "agentprovision-agents-api-1", "..." ],
    "input_set": "<short description of what was actually scanned>",
    "exit_summary": "ok | degraded"   // plan extension, documented for aggregator
  },
  "findings": [ { "id": "F<dim>.<n>", "title": "...", "where": "...", "evidence": "...", "reproducer": "...", "why_it_smells": "...", "suggested_action": "delete|refactor|document|leave", "effort": "S|M|L", "risk": "low|med|high", "blast_radius": "small|medium|large" } ],
  "method_notes": "<≤200 words on how the dimension/script scanned, surfaced in Appendix A>"
}
```

---

## Phase 0 — Scaffolding (this session, sequential, must complete before Phase 1)

### Task 0.1 — Shared `_findings.py` + package init

**Files:**
- Create: `scripts/smell/__init__.py` (empty)
- Create: `scripts/smell/_findings.py`

- [ ] **Step 1:** Write `_findings.py` with `@dataclass Finding`, `@dataclass Preflight`, `emit(preflight, findings) -> None` that writes one JSON object to stdout. Validate enums (effort ∈ S/M/L; risk ∈ low/med/high; blast_radius ∈ small/medium/large; suggested_action ∈ the 4 values).

```python
# scripts/smell/_findings.py
from __future__ import annotations
import json, sys
from dataclasses import dataclass, asdict, field
from typing import Literal

Effort = Literal["S", "M", "L"]
Risk = Literal["low", "med", "high"]
BlastRadius = Literal["small", "medium", "large"]
Action = Literal["delete", "refactor", "document", "leave"]

@dataclass
class CommandRecord:
    cmd: str            # exact shell invocation
    exit: int           # process exit code
    lines: int = 0      # lines of output captured

@dataclass
class Finding:
    id: str
    title: str
    where: str
    evidence: str
    reproducer: str
    why_it_smells: str
    suggested_action: Action
    effort: Effort
    risk: Risk
    blast_radius: BlastRadius

@dataclass
class Preflight:
    commands_attempted: list[CommandRecord] = field(default_factory=list)
    containers_seen: list[str] = field(default_factory=list)
    input_set: str = ""
    exit_summary: str = "ok"  # ok | degraded — plan extension on top of spec §4 contract

def emit(preflight: Preflight, findings: list[Finding], method_notes: str = "") -> None:
    json.dump(
        {
            "preflight": asdict(preflight),
            "findings": [asdict(f) for f in findings],
            "method_notes": method_notes,
        },
        sys.stdout, indent=2,
    )
    sys.stdout.write("\n")
```

- [ ] **Step 2:** `python3 -c "from scripts.smell._findings import Finding, Preflight, emit; emit(Preflight(input_set='smoke'), [], method_notes='ok')"` — expect `{"preflight": ..., "findings": [], "method_notes": "ok"}` and exit 0.

- [ ] **Step 3:** Commit:
```bash
git add scripts/smell/__init__.py scripts/smell/_findings.py
git commit -m "smell: shared Finding/Preflight contract for all dimension scripts"
```

### Task 0.2 — `unmounted_routes.py` (§3.1)

**Files:**
- Create: `scripts/smell/unmounted_routes.py`

- [ ] **Step 1:** Implement: for every `apps/api/app/api/v1/*.py` not matching `__init__|routes|deps`, check if its module name appears as an import (any form) in `apps/api/app/api/v1/routes.py`. Each missing one emits a Finding with `where=<file>`, `evidence="not imported by routes.py"`, `reproducer="python3 scripts/smell/unmounted_routes.py"`, action `delete`, effort S, risk low, blast_radius small.

- [ ] **Step 2:** Smoke: `python3 scripts/smell/unmounted_routes.py | python3 -c "import json,sys; d=json.load(sys.stdin); assert 'findings' in d; print('ok', len(d['findings']))"` — expect exit 0.

- [ ] **Step 3:** Commit:
```bash
git add scripts/smell/unmounted_routes.py
git commit -m "smell: unmounted-routes scanner (dimension §3.1)"
```

### Task 0.3 — `unimported_symbols.py` (§3.1)

**Files:**
- Create: `scripts/smell/unimported_symbols.py`

- [ ] **Step 1:** Try `vulture apps/api/app/services/ --min-confidence 80`; on stdout collect each `<file>:<line>: unused …` line. Fallback AST: parse every `*.py` in `apps/api/app/services/`, list top-level `def`/`class` symbols, then `grep -rE "from app.services.<module> import <symbol>|<module>\.<symbol>" apps/`. Symbols with 0 references → Finding. (`__all__` exports also count as references.)

- [ ] **Step 2:** Smoke: `python3 scripts/smell/unimported_symbols.py | jq '.findings | length'` — exit 0 (count may be 0).

- [ ] **Step 3:** Commit: `git add scripts/smell/unimported_symbols.py && git commit -m "smell: unimported-symbols scanner (dimension §3.1)"`

### Task 0.4 — `unregistered_workflows.py` (§3.1)

**Files:**
- Create: `scripts/smell/unregistered_workflows.py`

- [ ] **Step 1:** AST: parse every `apps/api/app/workflows/*.py` and `apps/code-worker/workflows.py`; collect classes decorated with `@workflow.defn` (or subclassing a Temporal workflow base). Then grep `apps/api/app/workers/*.py` and `apps/code-worker/main*.py` for `workflows=[...]` lists; class not appearing → Finding (`suggested_action: delete` if no references anywhere, otherwise `document`).

- [ ] **Step 2:** Smoke: `python3 scripts/smell/unregistered_workflows.py | jq .preflight.exit_summary` — expect `"ok"`.

- [ ] **Step 3:** Commit: `git add scripts/smell/unregistered_workflows.py && git commit -m "smell: unregistered-workflows scanner (dimension §3.1)"`

### Task 0.5 — `unrouted_pages.js` (§3.1)

**Files:**
- Create: `scripts/smell/unrouted_pages.js`

- [ ] **Step 1:** Node script: for every `apps/web/src/pages/*.js`, check whether its default-export identifier appears in `apps/web/src/App.js` as a `<Route … component={X}>` or in any router config. Missing → Finding. Print JSON matching `_findings.py` shape.

- [ ] **Step 2:** Smoke: `node scripts/smell/unrouted_pages.js | jq .preflight.input_set` — expect a string.

- [ ] **Step 3:** Commit: `git add scripts/smell/unrouted_pages.js && git commit -m "smell: unrouted-react-pages scanner (dimension §3.1)"`

### Task 0.5b — `migration_drift.py` (§3.1)

**Files:**
- Create: `scripts/smell/migration_drift.py`

- [ ] **Step 1:** CLI flags `--container` (default `agentprovision-agents-db-1`), `--db` (default `agentprovision`). Run `docker exec <container> psql -U postgres <db> -t -c "SELECT filename FROM _migrations ORDER BY filename;"`. Record exact command + exit code in `preflight.commands_attempted`. If exit != 0 OR container not in `docker ps`, emit `preflight.exit_summary="degraded"` with `containers_seen=` actual list and an empty findings array — Task 1.1 will catch this as degraded, NOT swallow it. On success, diff against `ls apps/api/migrations/*.sql | xargs -n1 basename | sort`; either side missing the other emits a Finding (`title="migration drift: <name>"`, `where=apps/api/migrations/<name>` or `_migrations row`, `suggested_action: refactor`/`document`).

- [ ] **Step 2:** Smoke (DB present): `python3 scripts/smell/migration_drift.py | jq '.preflight.exit_summary, (.findings | length)'` — expect `"ok"` (or `"degraded"` if DB not running, still exit 0).

- [ ] **Step 3:** Commit:
```bash
git add scripts/smell/migration_drift.py
git commit -m "smell: migration-drift scanner with degraded preflight (dimension §3.1)"
```

### Task 0.6 — `reexport_only.py` (§3.2)

**Files:**
- Create: `scripts/smell/reexport_only.py`

- [ ] **Step 1:** AST: for every `apps/api/app/services/*_service.py`, `*_manager.py`, `*_client.py`: if module body is exclusively `Import`/`ImportFrom`/`Assign` of `__all__` (no `FunctionDef`/`ClassDef`/non-trivial expressions), emit Finding. Suggested action: `delete` if all re-exports come from one source module, else `refactor`.

- [ ] **Step 2:** Smoke: `python3 scripts/smell/reexport_only.py | jq .findings`.

- [ ] **Step 3:** Commit: `git add scripts/smell/reexport_only.py && git commit -m "smell: reexport-only wrappers scanner (dimension §3.2)"`

### Task 0.7 — `docstring_redundancy.py` (§3.2)

**Files:**
- Create: `scripts/smell/docstring_redundancy.py`

- [ ] **Step 1:** AST: parse all `apps/api/app/**/*.py` (excluding tests/.venv/migrations). For each `FunctionDef`/`AsyncFunctionDef` with a docstring: extract first sentence (split on `. `), strip punctuation; if equal-case-folded to `<name>` or `<name>.` or starts with `f"{name}"` literal-restatement, emit Finding.

- [ ] **Step 2:** Smoke: `python3 scripts/smell/docstring_redundancy.py apps/api/app/services/ | jq .preflight.input_set`.

- [ ] **Step 3:** Commit: `git add scripts/smell/docstring_redundancy.py && git commit -m "smell: docstring-redundancy scanner (dimension §3.2)"`

### Task 0.8 — `missing_session_event.py` (§3.3)

**Files:**
- Create: `scripts/smell/missing_session_event.py`

- [ ] **Step 1:** AST: for each function in `apps/api/app/services/`, `apps/api/app/workflows/`, detect if it calls `db.commit()`, `session.commit()`, `await db.commit()`, or invokes any `.add(`/`.delete(`/`.merge(` on a session. If yes, check whether the same function (or any function in the same module called by it) calls `publish_session_event(`. Negative → Finding.

- [ ] **Step 2:** Smoke: `python3 scripts/smell/missing_session_event.py apps/api/app/services/ | jq '.findings | length'`.

- [ ] **Step 3:** Commit: `git add scripts/smell/missing_session_event.py && git commit -m "smell: missing-session-event scanner (dimension §3.3)"`

### Task 0.9 — `missing_rl_log.py` (§3.3)

**Files:**
- Create: `scripts/smell/missing_rl_log.py`

- [ ] **Step 1:** AST: for each function in `apps/api/app/services/` whose name matches `^(route|select|dispatch|pick|choose|fallback)_\w+$`, check for any call to a name starting with `rl_experience_service.log_` / `record_rl_experience` / `log_rl_experience`. Absent → Finding.

- [ ] **Step 2:** Smoke: `python3 scripts/smell/missing_rl_log.py apps/api/app/services/ | jq .findings[0]`.

- [ ] **Step 3:** Commit: `git add scripts/smell/missing_rl_log.py && git commit -m "smell: missing-rl-log scanner (dimension §3.3)"`

### Task 0.10 — `tenant_filter_check.py` (§3.3)

**Files:**
- Create: `scripts/smell/tenant_filter_check.py`

- [ ] **Step 1:** Build the set of tenant-scoped models by reading `apps/api/app/models/*.py` and selecting classes whose `__tablename__` is associated with a `tenant_id` column. AST-scan `apps/api/app/services/` for `db.query(<Model>)` chains; flag any chain that returns/yields without a `filter(<Model>.tenant_id == …)` (or `.filter_by(tenant_id=…)`) call. Finding per offender.

- [ ] **Step 2:** Smoke: `python3 scripts/smell/tenant_filter_check.py apps/api/app/services/ | jq .preflight.input_set`.

- [ ] **Step 3:** Commit: `git add scripts/smell/tenant_filter_check.py && git commit -m "smell: tenant-filter-check scanner (dimension §3.3)"`

### Task 0.11 — `log_errors.py` (§3.4)

**Files:**
- Create: `scripts/smell/log_errors.py`

- [ ] **Step 1:** CLI flags `--since` (default `72h`), `--container` (repeatable). For each container: `docker logs --since <since> <name>` → pipe through the 3-shape grep, normalize stack traces (strip line numbers, UUIDs, request IDs, timestamps), bucket by fingerprint, return top-20 with count + sample line. Preflight: container list seen via `docker ps --format '{{.Names}}'`; per-container exit code recorded. Each top-20 fingerprint → Finding with `where=<container>`, `evidence="<fingerprint> ×<count>"`, `reproducer=` the docker logs invocation.

- [ ] **Step 2:** Smoke: `python3 scripts/smell/log_errors.py --since 5m --container agentprovision-agents-api-1 | jq .preflight.containers_seen`.

- [ ] **Step 3:** Commit: `git add scripts/smell/log_errors.py && git commit -m "smell: log-errors multi-format capture (dimension §3.4)"`

### Task 0.12 — `nesting_depth.py` (§3.5)

**Files:**
- Create: `scripts/smell/nesting_depth.py`

- [ ] **Step 1:** AST: walk every `apps/api/app/services/*.py` and `apps/api/app/workflows/*.py`; for each `FunctionDef`/`AsyncFunctionDef` compute max nesting depth of `If`/`For`/`While`/`With`/`Try`. Report top 30 by depth as Findings (`title="deeply-nested function"`, `evidence="depth=<n>, LOC=<m>"`).

- [ ] **Step 2:** Smoke: `python3 scripts/smell/nesting_depth.py apps/api/app/services/ | jq .findings[0]`.

- [ ] **Step 3:** Commit: `git add scripts/smell/nesting_depth.py && git commit -m "smell: nesting-depth scanner (dimension §3.5)"`

### Task 0.13 — `run_dimension.sh` smoke runner

**Files:**
- Create: `scripts/smell/run_dimension.sh`

- [ ] **Step 1:** Bash wrapper that invokes every script with safe defaults and pipes through `jq empty` to assert valid JSON; exits non-zero on first failure. Used by Phase 0 final smoke + Phase 1 subagent preflight.

- [ ] **Step 2:** Run it: `bash scripts/smell/run_dimension.sh` — every script must exit 0 with valid JSON.

- [ ] **Step 3:** Explicit gate before commit — the runner exit code is the test, so abort if non-zero:
```bash
bash scripts/smell/run_dimension.sh || { echo "Phase-0 smoke failed; aborting before commit"; exit 1; }
git add scripts/smell/run_dimension.sh
git commit -m "smell: run_dimension smoke runner — Phase 0 complete"
git push
```

- [ ] **Step 4:** Record fan-out SHA: `git rev-parse HEAD` → write it into `/tmp/smell_fanout_sha`. The aggregator will record this in Appendix A.

---

## Phase 1 — Parallel evidence collection (single tool batch, 5 Explore subagents)

### Task 1.0 — Dispatch all 5 subagents in one batch

**Files:** none directly; subagents return JSON.

- [ ] **Step 1:** In a single assistant message, emit five `Agent` tool calls with `subagent_type: Explore`. Each subagent gets:
  - The fan-out SHA (`/tmp/smell_fanout_sha`).
  - Its dimension definition (§3.1 / §3.2 / §3.3 / §3.4 / §3.5 from the spec) verbatim.
  - The output contract from `_findings.py`.
  - Explicit list of scripts it may run.
  - Instruction: read-only, must return the JSON shape; if any script fails or container unreachable, populate `preflight.exit_summary="degraded"` and STILL return JSON (no silent empty).

- [ ] **Step 2:** Collect the 5 JSON blobs into `/tmp/smell_results/<dim>.json` (one file per dimension).

- [ ] **Step 3:** Per-dimension sanity check:
```bash
for d in dead_code ai_slop pattern_drift errors hotspots; do
  jq '.preflight.exit_summary, (.findings | length)' /tmp/smell_results/$d.json
done
```
Expected: every dimension prints `"ok"` or `"degraded"` plus a count.

### Task 1.1 — Fail-loud check

- [ ] **Step 1:** Aggregator rule: for each dimension, if `findings == []` AND `preflight.commands_attempted == []` (or every command exit ≠ 0), HALT with explicit error naming the dimension. The user (when back) sees the failure rather than a silently-empty report.

- [ ] **Step 2:** If degraded but non-empty preflight, mark the dimension `DEGRADED` in the aggregator state for surfacing in the report's methods log.

---

## Phase 2 — Aggregation & report write-up (sequential, in this session)

### Task 2.1 — Dedupe overlapping findings

**Files:**
- Modify: `/tmp/smell_results/*.json` (read), `/tmp/smell_aggregated.json` (write)

- [ ] **Step 1:** Read all 5 JSON files. Concatenate findings into one list. Dedupe by `(where, evidence)` tuple — if two dimensions hit the same file:line with the same evidence, merge into one finding, attribute both dimensions in `notes`.

- [ ] **Step 2:** Save to `/tmp/smell_aggregated.json`.

### Task 2.2 — Rank by (risk × blast_radius) / effort  *(spec §4 formula verbatim)*

**Files:**
- Modify: `/tmp/smell_aggregated.json` (in-place rank).

- [ ] **Step 1:** Map risk → {low:1, med:3, high:9}; blast_radius → {small:1, medium:3, large:9}; effort → {S:1, M:3, L:9}. Score = (risk × blast_radius) / effort, matching spec §4 verbatim. **No `suggested_action` multiplier** (kept out of the formula to avoid drifting from spec; if two findings tie, break by alphabetical `where`).

- [ ] **Step 2:** Take top 10 for the "Top-10 ranked findings" report section.

### Task 2.3 — Write the report

**Files:**
- Create: `docs/reports/2026-05-28-core-primitives-smell-report.md`

- [ ] **Step 1:** Layout (per spec §5):
  1. **Luna-summary** (≤200 words, plain prose, "5 fattest fish").
  2. **Top-10 ranked findings** with the uniform shape.
  3. **Full per-dimension findings** grouped, each `F<dim>.<n>`.
  4. **Appendix A** — methods log: fan-out SHA, per-dimension preflight blocks, exact commands run, log windows scanned.
  5. **Appendix B** — Luna consensus thread snapshot (point at spec Appendix B).

- [ ] **Step 2:** Sanity check: every Top-10 finding must trace to a reproducer command from the spec's §3 method list.

### Task 2.4 — Commit + push + flip PR to Ready

- [ ] **Step 1:** Commit:
```bash
git add docs/reports/2026-05-28-core-primitives-smell-report.md
git commit -m "report: core-primitives smell report (Phase 2 aggregation)"
git push
```

- [ ] **Step 2:** Update PR #729 body to add the "Smell report committed" checkbox and flip to ready:
```bash
gh pr ready 729
```

- [ ] **Step 3:** Update task list, archive `/tmp/smell_*` working files.

### Task 2.5 — Send completion summary to Luna

- [ ] **Step 1:** Same chat session (`d9e5b6ad-1f33-4624-bb71-f65908c2716e`). Send:
```bash
alpha chat send "Smell report committed. Branch: chore/core-primitives-smell-report. Top-10 findings ranked by (risk×blast×action)/effort. PR #729 now Ready. The §9 open questions (sixth dimension, dim merges, ranking weight choice, missed canonical patterns) are still open — please answer when you have a moment so the writing-plans cycle for the actual cleanups can pick them up." --session d9e5b6ad-1f33-4624-bb71-f65908c2716e --no-stream
```

- [ ] **Step 2:** Capture Luna's reply, append to the report as Appendix B addendum if non-trivial.

### Task 2.6 — Leave user-facing summary

- [ ] **Step 1:** Final assistant message in this session: a one-paragraph status with PR link, top-3 findings, count of total findings, any DEGRADED dimensions, and what writing-plans round would be next. The user picks up here when they're back.

---

## Acceptance criteria for THIS plan

1. ✓ Spec is referenced and treated as ground truth (no scope creep).
2. ✓ Every Phase-0 task creates exactly one script + smoke-test + commit (DRY, frequent commits).
3. ✓ All scripts share the `_findings.py` contract.
4. ✓ Phase 1 is a single tool-call batch (parallel by construction).
5. ✓ Phase 2 fails loud on context-starved dimensions.
6. ✓ Report committed, PR flipped to Ready, Luna informed.

## Risks (specific to execution, beyond the spec's §8)

- **Vulture absent:** Task 0.3 must succeed on AST fallback. Mitigation: smoke step runs the fallback path explicitly.
- **AST script false negatives** (e.g. dynamic `getattr` lookups): acknowledged trade-off; findings are evidence-based, not exhaustive. Document as a known limitation in report Appendix A.
- **Subagent disagrees with the dimension definition:** the prompt includes the dimension verbatim from the spec; if a subagent improvises, the aggregator's "reproducer must match a §3 method" sanity check (Task 2.3 Step 2) rejects the finding.
- **Docker / DB unreachable from subagent sandbox:** Task 1.1 fail-loud rule surfaces this rather than silently emitting "all clean."
