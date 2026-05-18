# Skill-creator framework — port Claude Code's reference shape onto Alpha

Date: 2026-05-18
Owner: Alpha platform
Status: Design (open)

## Why

The user pointed at Claude Code's `skill-creator` framework (the one in their `~/.claude/skills/skill-creator/` install) as the reference shape Alpha's skill marketplace should converge to. We already implemented the SKILL.md format + `_bundled/` + `_tenant/<uuid>/` layout in PRs #182–#193 (see [[skills_marketplace_v2]]), so the gap is the *creator* surface — the tooling that lets a user iteratively author, evaluate, and ship a skill — not the runtime that loads and dispatches it.

Apple → macOS analogy holds: Alpha is the OS for AgentProvision, and a first-class skill-creator is the IDE for one of its file types. The Claude Code reference is good enough that we want to match its shape almost line-for-line where it makes sense.

## Reference shape (Claude Code's skill-creator)

```
skill-creator/
├── SKILL.md                              # YAML frontmatter + body
├── agents/
│   ├── analyzer.md                       # post-hoc "why did winner win"
│   ├── comparator.md                     # blind A/B output judge
│   └── grader.md                         # eval expectations vs outputs
├── assets/
│   └── eval_review.html                  # browser-based eval set review
├── eval-viewer/
│   ├── generate_review.py                # builds + serves a feedback HTML
│   └── viewer.html                       # the SPA template
├── references/
│   └── schemas.md                        # JSON shapes for evals/grading
├── scripts/
│   ├── aggregate_benchmark.py            # rollup with mean ± stddev
│   ├── generate_report.py
│   ├── improve_description.py            # description-triggering optimizer
│   ├── package_skill.py                  # zip into .skill artifact
│   ├── quick_validate.py                 # YAML + dir-structure sanity
│   ├── run_eval.py                       # single eval runner
│   ├── run_loop.py                       # full description-optimization loop
│   └── utils.py
└── LICENSE.txt
```

The conceptual loop the framework drives:

1. Capture intent (what should the skill do, when should it trigger, what's the output)
2. Write SKILL.md draft (frontmatter description is THE trigger; body has progressive-disclosure references to `scripts/`, `references/`, `assets/`)
3. Author 2–3 realistic test prompts (in `evals/evals.json`)
4. Run with-skill AND without-skill in parallel subagents (no `/skill-test` slash command; pure subagent dispatch)
5. Capture timing/token data from the subagent notifications
6. Grade each run via the grader agent against assertions
7. Aggregate to `benchmark.json` (pass_rate / time / tokens, with mean ± stddev and delta)
8. Run analyzer to surface patterns the aggregate hides (non-discriminating assertions, flaky evals, time/token tradeoffs)
9. Launch the eval-viewer (browser + per-run feedback textarea + benchmark tab + previous-iteration diff)
10. User leaves feedback → `feedback.json`
11. Author improves the skill, re-runs, repeats until satisfied
12. `improve_description.py` / `run_loop.py` optimize the YAML `description:` field for triggering accuracy
13. `package_skill.py` zips into a portable `.skill` artifact

The whole thing is built around three core ideas:
- **Progressive disclosure** — metadata always loaded, body loaded on trigger, bundled resources loaded on demand
- **Eval-driven iteration** — every change measured both quantitatively (assertions) and qualitatively (user feedback)
- **Triggering as a first-class problem** — the YAML `description:` field is what determines whether Claude reaches for the skill; optimize it explicitly

## What we already have

| Reference component | Our equivalent | Status |
|--------------------|----------------|--------|
| SKILL.md + YAML frontmatter | `apps/api/app/skills/_bundled/*/skill.md` + `_tenant/<uuid>/*/skill.md` | ✅ shipped (PR #182–#193) |
| `name`, `description`, `engine`, `category`, `tags`, `auto_trigger`, `inputs[]` frontmatter | `_normalize_external_metadata()` + `_parse_skill_md()` in `apps/api/app/services/skill_manager.py` | ✅ shipped |
| Native + tenant tier split | `tier: "native"` vs `tier: "tenant"` in `FileSkill` | ✅ shipped |
| Claude-Code-format import | `_normalize_external_metadata` handles GWS / Claude Code shapes; `apps/api/app/services/community_skills_import.py` | ✅ shipped |
| Skills marketplace UI | `/skills` route + `update_skill_definition` MCP tool | ✅ shipped |
| Audit log of skill edits | `library_revisions` table (migration 110) | ✅ shipped |
| Per-tenant override of bundled skill | `_tenant/<uuid>/<slug>/` shadowing | ✅ shipped |
| `read_library_skill` MCP tool | exists | ✅ shipped |
| Subprocess-based skill execution | code-worker delegated via `_run_skill` | ✅ shipped |

So we have the *runtime* (load + execute + version + audit) but **none** of the *authoring* loop: no eval harness, no comparator, no grader, no analyzer, no eval-viewer, no benchmark aggregator, no `improve_description.py`, no `package_skill.py`.

## Gaps and proposed delivery

### Phase 1 — schemas + grader (foundation, no UI yet)

Add the data shapes the rest of the framework depends on:

- `docs/skill-creator/schemas.md` — JSON shapes for `evals.json`, `eval_metadata.json`, `grading.json`, `benchmark.json`, `feedback.json`, `comparison.json`. Mirror Claude Code's shapes byte-for-byte so a skill author who has used theirs is immediately productive on ours.
- `apps/api/app/services/skill_creator/grader.py` — service-layer grader. Takes `(transcript, outputs_dir, expectations)`, returns the `grading.json` shape. Reuses our existing LLM router (so it can dispatch to Claude or any Lane B model depending on tenant plan).
- `apps/api/migrations/15X_skill_evals_tables.sql` — `skill_evals`, `skill_eval_runs`, `skill_eval_grading` tables. Each tenant's authoring sessions live in their own row. Indexed by `(tenant_id, skill_id, iteration)`.
- `POST /api/v1/skills/{skill_id}/evals/grade` — endpoint that runs the grader against a saved run.

### Phase 2 — eval runner + workspace layout

- `apps/api/app/services/skill_creator/eval_runner.py` — dispatches eval prompts. The "subagent" mechanic is whatever Alpha already uses for delegated chat turns (the existing `_call_agent` path). For each eval: spawn a turn against the skill being authored AND a baseline turn (no skill or previous skill version).
- Workspace layout under `<workspaces_root>/<tenant>/skills/<skill-slug>-workspace/iteration-<N>/eval-<id>/` — mirrors Claude Code's directory shape so the eval-viewer can be lifted nearly verbatim later.
- `apps/code-worker/skill_eval_executor.py` — code-worker side; the actual subprocess that runs the skill against the eval prompt. Writes `transcript.md`, `outputs/`, `metrics.json`, `timing.json` into the iteration dir.
- `POST /api/v1/skills/{skill_id}/evals/run` — kicks off all evals for an iteration; returns a job id (uses the chat-job pattern from [[2026-05-17-async-chat-result-pattern-design]]).

### Phase 3 — benchmark aggregator + analyzer

- `apps/api/app/services/skill_creator/aggregate.py` — equivalent of `aggregate_benchmark.py`. Reads all grading.json files for an iteration, produces `benchmark.json` with `run_summary.with_skill / without_skill / delta` (each with `pass_rate / time / tokens` as `{mean, stddev}`).
- `apps/api/app/services/skill_creator/analyzer.py` — reads the benchmark, surfaces patterns the aggregate hides (non-discriminating assertions, high-variance evals, time/token tradeoffs). Returns a list of `notes: string[]`.
- `GET /api/v1/skills/{skill_id}/evals/iterations/{N}/benchmark` — serves the aggregate + notes.

### Phase 4 — eval-viewer in the Den

The user already shipped the Alpha Control Center / Den IDE shell (PR #515). The eval-viewer is the natural next surface inside it:

- New Den tab type: `skill-eval-iteration`. Renders one iteration's runs left/right (with_skill | without_skill), per-run feedback textarea, prev-iteration diff, benchmark tab.
- Reuse our existing `FileViewer` for `transcript.md` rendering and the markdown chat-message renderer for prompts/outputs. The XLSX/PDF/image rendering paths from Claude Code's `viewer.html` are gravy — most skills produce text/JSON.
- Feedback saves to `skill_eval_feedback` table (NOT to a `feedback.json` file — we don't want per-tenant authoring artifacts on the workspaces volume; DB is the right place for this).
- `WS /api/v1/skills/{skill_id}/evals/iterations/{N}/feed` — Server-sent events for live updates as eval runs complete.

### Phase 5 — comparator (blind A/B)

- `apps/api/app/services/skill_creator/comparator.py` — given two output directories, dispatches an LLM call WITHOUT telling the model which side produced which. Returns `comparison.json` per the schema.
- `POST /api/v1/skills/{skill_id}/evals/iterations/{N}/compare` — runs the comparator across paired runs.
- Den UI: "Compare iteration N vs M" button.

### Phase 6 — description optimizer

- `apps/api/app/services/skill_creator/description_optimizer.py` — port of `improve_description.py` + `run_loop.py`. The 20-query eval set (8–10 should-trigger, 8–10 should-not-trigger, mostly near-misses), 60/40 train/test split, multi-iteration loop that calls an LLM to propose description rewrites, evaluates each on held-out test, returns `best_description`.
- The "triggering test" itself dispatches against the actual model that powers the user's current Alpha session (per Claude Code's instruction to use the same model id the live session uses).
- Den UI: "Optimize description" button on the skill detail page → opens a side panel showing the iteration loop in real time + the before/after.

### Phase 7 — packaging

- `apps/api/app/services/skill_creator/package.py` — zips a skill directory into a `.skill` artifact. Mirror Claude Code's `package_skill.py`.
- `GET /api/v1/skills/{skill_id}/package.skill` — download. Per-tenant; superuser can also download `_bundled/` skills for export.
- This closes the loop with [[skills_marketplace_v2]]: any tenant can export a skill they've authored, share it, and another tenant imports via the existing `import_skill_from_url` MCP tool.

### Phase 8 — `skill-creator` itself as a skill

Once the API + Den UI are in place, ship the actual `skill-creator` skill as a bundled skill in `apps/api/app/skills/_bundled/skill-creator/`. Its `SKILL.md` body walks the agent through the iteration loop using the new API endpoints. Triggers on phrases like "create a skill", "improve a skill", "evaluate this skill", "optimize the trigger".

The Den tab type from Phase 4 is the UI; this skill is the kernel that drives it from a chat turn.

## Phasing rationale

Phase 1–3 are server-side and unblock the rest. Each is ~300–500 LOC. Phase 4 is the user-visible surface and is roughly a one-week effort by itself. Phases 5–7 stack on Phase 4 cleanly and can ship in any order. Phase 8 needs everything else first.

If we wanted a one-week MVP, the smallest demo that captures the loop is:

- Phase 1 (schemas + grader)
- Phase 2 minimal (eval runner that hardcodes the with-skill / without-skill pair, no parallelism)
- Phase 3 minimal (aggregator only, no analyzer)
- Phase 4 minimal (one Den tab that lists iterations + their pass rates + per-run feedback textarea)

That's ~1500 LOC end-to-end and enough to author a real skill against. Everything else iterates from there.

## Open questions

1. **Where does code-worker fit?** Claude Code's eval-runner spawns subagents via the local `claude` binary. Our equivalent is the existing code-worker dispatch (`_call_agent`). Confirm code-worker can take a "use this skill against this prompt" task without us creating a new task type — likely just a new `ChatCliTaskInput` flavor.

2. **Per-tenant model routing.** Claude Code uses whatever model the local CLI is bound to. We need to honor each tenant's `default_cli_platform` (from #541's allowlist) plus the [[rl_exploration_rate]] for chat_response. The eval runner should explicitly pass the model id so re-runs are deterministic; the description optimizer in particular needs the same model the tenant's live Alpha session uses.

3. **Cost.** Authoring a skill via this framework runs N evals × M iterations × 2 baselines per iteration × LLM cost. Default to Lane B (Qwen / DeepSeek / GLM per [[2026-05-18-cli-integration-catalog]]) for skill authoring; users can opt into Claude/Codex for the final hardening pass. Cost meter visible in the Den tab.

4. **Schema portability.** If we match Claude Code's JSON shapes byte-for-byte, can we also accept their `.skill` exports as-is? Probably yes for SKILL.md but the eval/feedback artifacts may not align — confirm with one of the bundled superpowers skills.

5. **Description optimizer's trigger test.** Claude Code's runs the trigger via `claude -p`. Ours could either (a) hit the LLM directly with the same prompt + skill list, or (b) actually invoke the agent router and see whether the skill surfaces in the router's selection. Option (b) tests our real triggering path; (a) is simpler. Lean (b) for fidelity, (a) for the MVP.

## Risks

- **Compute cost.** Skill authoring is the most token-heavy workflow Alpha will run per-tenant. Without cost meters + budget caps surfaced in the Den, a power user can rack up serious bills. Phase 4 must include a per-iteration cost display + a tenant budget cap that hard-stops further runs.
- **Subagent timeouts.** Claude Code's runs are local and bounded by terminal sessions; ours run through Temporal + code-worker. Existing Cloudflare 524 SSE issue ([[2026-05-17-async-chat-result-pattern-design]]) applies. Phase 2 must use the async chat-result pattern from that doc, not the legacy SSE-with-heartbeats path.
- **Workspace volume bloat.** Skill authoring writes a lot of files per iteration. Honor the per-tenant HOME quota from [[2026-05-17-code-worker-tenant-home-cap-design]] — skill-eval workspaces count against the same 2 GiB cap. Or carve a separate `skill_evals/` mount under the workspaces volume with its own quota.
- **Claude Code drift.** Their reference framework will keep evolving. We pin to the shape we ship Phase 1 against and accept drift — periodically re-baseline (every quarter?). NOT a hard contract.

## Next step

User reviews Phase 1 + 2 scope, signs off, and we open the migration + first executor PR. The catalog work in [[2026-05-18-cli-integration-catalog]] (Higgsfield MCP / Qwen executor / Wave 2 code CLIs) runs in parallel — different code paths, no contention.
