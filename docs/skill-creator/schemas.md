# Skill-creator JSON schemas

Status: Phase 1 — frozen against Claude Code's `~/.claude/skills/skill-creator/references/schemas.md`
as of 2026-05-18. We mirror their shape byte-for-byte where possible so a skill author who has
used the reference framework is immediately productive on Alpha.

Every shape below is the contract the **API + frontend + bundled `skill-creator` skill agree
on**. If you change one of these schemas you must bump the consumer code in lockstep
(`apps/api/app/services/skill_creator/*.py`, the Den eval-viewer, and any bundled
`skill-creator` skill body). See [[2026-05-18-skill-creator-framework-port]] for the
delivery sequencing.

Section | Used by | Status
--------|---------|--------
[evals.json](#evalsjson) | Eval runner (Phase 2) | Phase 1: contract only
[eval_metadata.json](#eval_metadatajson) | Eval runner (Phase 2) | Phase 1: contract only
[grading.json](#gradingjson) | Grader (Phase 1) | **shipped Phase 1**
[benchmark.json](#benchmarkjson) | Aggregator (Phase 3) | Phase 1: contract only
[feedback.json](#feedbackjson) | Eval-viewer (Phase 4) — DB-backed, file shape kept for export | Phase 1: contract only
[comparison.json](#comparisonjson) | Comparator (Phase 5) | Phase 1: contract only

Conventions
-----------

- All identifiers are strings unless explicitly typed.
- All timestamps are RFC 3339 with `Z` suffix (UTC), e.g. `"2026-05-18T14:23:01Z"`.
- All file paths are POSIX, forward-slash, **relative to the iteration directory** unless
  explicitly absolute. The iteration directory layout is fixed by Phase 2 and is mirrored
  one-to-one by `skill_eval_runs.outputs` keys.
- Money/numbers are JSON numbers (not strings). Token counts are integers. Times are integers
  in milliseconds unless `_seconds` is in the field name.
- Fields with a default get the default when omitted; fields without a default are required.
- "**required**" means the producer MUST write it; consumers MAY treat absence as a hard error.

---

## evals.json

The list of test prompts an author writes for a skill. Lives at
`<skill>-workspace/evals/evals.json` (matches Claude Code's path).

```json
{
  "version": 1,
  "skill_slug": "expense-classifier",
  "evals": [
    {
      "id": "eval-001",
      "name": "Restaurant receipt",
      "prompt": "Classify this expense: ...",
      "expectations": [
        {
          "id": "expect-001",
          "description": "Output JSON has a `category` field equal to 'meals'",
          "kind": "assertion"
        }
      ],
      "tags": ["happy-path"]
    }
  ]
}
```

Top-level object:

Field | Type | Required | Meaning
------|------|----------|--------
`version` | int | yes | Schema version. Currently `1`. Bump only on breaking change.
`skill_slug` | string | yes | Slug of the skill the evals belong to.
`evals` | array<Eval> | yes | Ordered list of eval definitions. Order is significant (affects iteration directory ordering).

Eval object:

Field | Type | Required | Meaning
------|------|----------|--------
`id` | string | yes | Stable id within the file. Used as the directory name `eval-<id>` inside the iteration.
`name` | string | yes | Human-readable label for the Den UI.
`prompt` | string | yes | The user-turn message text sent to the skill (and the baseline) at run time.
`expectations` | array<Expectation> | yes | At least one expectation. See `grading.json` for shape.
`tags` | array<string> | no | Free-form labels. Common tags: `happy-path`, `edge-case`, `regression`. Used by the analyzer (Phase 3) to surface tag-level pass rates.

---

## eval_metadata.json

Per-run metadata captured by the eval runner (Phase 2). Lives at
`<skill>-workspace/iteration-<N>/eval-<id>/eval_metadata.json`.

```json
{
  "version": 1,
  "eval_id": "eval-001",
  "iteration": 3,
  "with_skill": true,
  "skill_slug": "expense-classifier",
  "skill_version": "0.4.0",
  "model": "claude-3-5-sonnet-20241022",
  "cli_platform": "claude_code",
  "started_at": "2026-05-18T14:23:01Z",
  "completed_at": "2026-05-18T14:23:09Z",
  "timing_ms": 8123,
  "token_usage": {
    "input": 412,
    "output": 87,
    "total": 499
  },
  "status": "ok",
  "error": null
}
```

Field | Type | Required | Meaning
------|------|----------|--------
`version` | int | yes | Schema version. Currently `1`.
`eval_id` | string | yes | Foreign key into `evals.json::evals[].id`.
`iteration` | int | yes | 1-indexed iteration number this run belongs to.
`with_skill` | bool | yes | `true` if the skill was loaded; `false` for the baseline (no-skill) run paired with it.
`skill_slug` | string | yes | Slug of the skill under test. Same value on both `with_skill` legs of the pair.
`skill_version` | string | no | Version string from the skill's `skill.md` frontmatter, captured at run start. Empty for baseline. Lets the viewer show "skill v0.3.0 → v0.4.0" between iterations.
`model` | string | yes | Provider-namespaced model id. Same id on both legs of a paired run.
`cli_platform` | string | yes | One of `claude_code`, `codex`, `gemini_cli`, `copilot_cli`, `qwen_code`, `kimi_k2`, `deepseek`, `glm`, `aider`, `goose`, `opencode`. Matches `_DEFAULT_PRIORITY` in `cli_platform_resolver.py`.
`started_at` | string (RFC 3339) | yes | UTC.
`completed_at` | string (RFC 3339) | yes | UTC.
`timing_ms` | int | yes | `completed_at - started_at` in ms. Producer computes this so the Den UI doesn't have to.
`token_usage.input` | int | yes | Prompt tokens.
`token_usage.output` | int | yes | Completion tokens.
`token_usage.total` | int | yes | Always `input + output`. Producer computes to dodge consumer arithmetic mistakes.
`status` | string | yes | `ok`, `error`, or `timeout`.
`error` | string | nullable | Error message when `status != "ok"`. `null` on success.

---

## grading.json

Output of the grader (Phase 1, this PR). Lives at
`<skill>-workspace/iteration-<N>/eval-<id>/grading.json` AND is returned by
`POST /api/v1/skills/{skill_id}/evals/grade` directly.

```json
{
  "version": 1,
  "eval_id": "eval-001",
  "run_id": "0d9c…",
  "graded_at": "2026-05-18T14:23:15Z",
  "grader_model": "claude-3-5-sonnet-20241022",
  "score": 0.6667,
  "passed": false,
  "expectations": [
    {
      "id": "expect-001",
      "description": "Output JSON has a `category` field equal to 'meals'",
      "passed": true,
      "reasoning": "The transcript shows `{\"category\":\"meals\",...}`."
    },
    {
      "id": "expect-002",
      "description": "Confidence >= 0.8",
      "passed": false,
      "reasoning": "Confidence reported as 0.62, below the 0.8 threshold."
    },
    {
      "id": "expect-003",
      "description": "Includes a vendor extraction",
      "passed": true,
      "reasoning": "Vendor field is 'Olive Garden'."
    }
  ]
}
```

Field | Type | Required | Meaning
------|------|----------|--------
`version` | int | yes | Schema version. Currently `1`.
`eval_id` | string | yes | Foreign key into `evals.json::evals[].id`.
`run_id` | string (UUID) | yes | Foreign key into `skill_eval_runs.id`.
`graded_at` | string (RFC 3339) | yes | UTC. When the grader finished.
`grader_model` | string | yes | Provider-namespaced model id used to grade. Recorded so the same eval can be re-graded with a stronger model later for forensics.
`score` | number | yes | Fraction of expectations passed: `count(passed=true) / len(expectations)`. Range `[0, 1]`. Producer computes.
`passed` | bool | yes | `true` iff every expectation passed (`score == 1.0`). Producer computes.
`expectations` | array<GradedExpectation> | yes | One entry per expectation, in input order. Same length as input.

GradedExpectation object:

Field | Type | Required | Meaning
------|------|----------|--------
`id` | string | yes | Stable id, mirrors the input expectation's id.
`description` | string | yes | The original expectation text. Echoed so a grading.json file is self-contained.
`passed` | bool | yes | The grader's binary verdict.
`reasoning` | string | yes | The grader's one-to-three-sentence justification. Surfaces in the eval-viewer.

**Expectation kinds (input to grader):**

Field | Type | Required | Meaning
------|------|----------|--------
`id` | string | yes | Stable id within the eval.
`description` | string | yes | Plain-English assertion the grader judges.
`kind` | string | no | `assertion` (default — grader judges text-level) or `structured` (grader is told the output is JSON and may parse it). Phase 1 honors both as soft hints; the grader still emits the same `GradedExpectation` shape.

---

## benchmark.json

Aggregator output (Phase 3). One file per iteration at
`<skill>-workspace/iteration-<N>/benchmark.json`.

```json
{
  "version": 1,
  "skill_slug": "expense-classifier",
  "iteration": 3,
  "eval_count": 5,
  "generated_at": "2026-05-18T14:25:00Z",
  "run_summary": {
    "with_skill": {
      "pass_rate": {"mean": 0.80, "stddev": 0.18},
      "timing_ms": {"mean": 7240, "stddev": 1100},
      "tokens": {"mean": 510, "stddev": 64}
    },
    "without_skill": {
      "pass_rate": {"mean": 0.40, "stddev": 0.25},
      "timing_ms": {"mean": 6800, "stddev": 850},
      "tokens": {"mean": 488, "stddev": 70}
    },
    "delta": {
      "pass_rate": {"mean": 0.40, "stddev": null},
      "timing_ms": {"mean": 440, "stddev": null},
      "tokens": {"mean": 22, "stddev": null}
    }
  },
  "per_eval": [
    {
      "eval_id": "eval-001",
      "with_skill": {"passed": true,  "timing_ms": 8123, "tokens": 499},
      "without_skill": {"passed": false, "timing_ms": 7012, "tokens": 421}
    }
  ]
}
```

Field | Type | Required | Meaning
------|------|----------|--------
`version` | int | yes | Schema version. Currently `1`.
`skill_slug` | string | yes | Slug of the skill the benchmark belongs to.
`iteration` | int | yes | Iteration number.
`eval_count` | int | yes | Number of evals contributing to the aggregate. Both legs of a pair count as one (paired stats).
`generated_at` | string (RFC 3339) | yes | UTC.
`run_summary.with_skill` | Stats | yes | Aggregate over the skill-loaded runs.
`run_summary.without_skill` | Stats | yes | Aggregate over the baseline runs.
`run_summary.delta` | Stats | yes | `with_skill - without_skill` per metric. `stddev` is `null` for the delta because it isn't a meaningful single-sample statistic; producers MUST emit `null` not `0`.
`per_eval` | array | yes | Per-eval detail row used by the eval-viewer for the "expand to per-eval rows" view.

Stats object:

Field | Type | Required | Meaning
------|------|----------|--------
`pass_rate.mean` | number | yes | Mean pass rate (0..1). For paired runs, treats each eval as one Bernoulli sample.
`pass_rate.stddev` | number/null | yes | Sample stddev. `null` only on the delta object.
`timing_ms.mean` | number | yes | Mean run timing in ms.
`timing_ms.stddev` | number/null | yes | Sample stddev. `null` only on the delta object.
`tokens.mean` | number | yes | Mean total tokens per run.
`tokens.stddev` | number/null | yes | Sample stddev. `null` only on the delta object.

---

## feedback.json

User-authored notes on a run / iteration (Phase 4). In Alpha the canonical store is the
`skill_eval_feedback` DB table (not yet shipped — Phase 4); this file shape is what the
**export** flow emits when a tenant downloads their authoring history.

```json
{
  "version": 1,
  "iteration": 3,
  "generated_at": "2026-05-18T14:30:00Z",
  "items": [
    {
      "scope": "run",
      "eval_id": "eval-001",
      "with_skill": true,
      "author": "user@example.com",
      "created_at": "2026-05-18T14:29:42Z",
      "text": "Output is fine but it returned 'food' instead of 'meals'; rubric needs to allow synonyms."
    },
    {
      "scope": "iteration",
      "author": "user@example.com",
      "created_at": "2026-05-18T14:29:55Z",
      "text": "Big jump from iter 2 — adding the JSON example to the body did the work."
    }
  ]
}
```

Field | Type | Required | Meaning
------|------|----------|--------
`version` | int | yes | Schema version. Currently `1`.
`iteration` | int | yes | Iteration this feedback belongs to.
`generated_at` | string (RFC 3339) | yes | UTC. Time the export was rendered (not the time of the latest item).
`items` | array<FeedbackItem> | yes | All feedback items for the iteration, in creation order.

FeedbackItem object:

Field | Type | Required | Meaning
------|------|----------|--------
`scope` | string | yes | `run` (about one specific eval run) or `iteration` (about the whole iteration).
`eval_id` | string | conditional | Required when `scope == "run"`. Omitted when `scope == "iteration"`.
`with_skill` | bool | conditional | Required when `scope == "run"`. Identifies which leg of the paired run.
`author` | string | yes | Email or user identifier of the author.
`created_at` | string (RFC 3339) | yes | UTC.
`text` | string | yes | Free-form note. No markdown rendering guaranteed — viewers MAY render as markdown but producers SHOULD assume plain text.

---

## comparison.json

Output of the blind A/B comparator (Phase 5). One file per compared pair at
`<skill>-workspace/iteration-<N>/eval-<id>/comparison.json` AND returned by the
comparator endpoint.

```json
{
  "version": 1,
  "eval_id": "eval-001",
  "iteration_a": 2,
  "iteration_b": 3,
  "compared_at": "2026-05-18T14:35:00Z",
  "comparator_model": "claude-3-5-sonnet-20241022",
  "winner": "B",
  "confidence": 0.75,
  "reasoning": "B's output explains the rationale for the category choice; A's omits it. Both are technically correct on the assertion.",
  "axes": [
    {"name": "correctness", "winner": "tie", "notes": "Both pass the rubric."},
    {"name": "clarity", "winner": "B", "notes": "B includes a sentence justifying the choice."},
    {"name": "brevity", "winner": "A", "notes": "A is ~30% shorter."}
  ]
}
```

Field | Type | Required | Meaning
------|------|----------|--------
`version` | int | yes | Schema version. Currently `1`.
`eval_id` | string | yes | Foreign key into `evals.json::evals[].id`.
`iteration_a` | int | yes | Iteration whose run is presented as "A" to the comparator. The comparator is told **only** "A" and "B" — not which iteration is which.
`iteration_b` | int | yes | Iteration whose run is presented as "B" to the comparator.
`compared_at` | string (RFC 3339) | yes | UTC.
`comparator_model` | string | yes | Provider-namespaced model id of the LLM that performed the comparison.
`winner` | string | yes | One of `A`, `B`, `tie`.
`confidence` | number | yes | `[0, 1]`. The comparator's self-reported confidence.
`reasoning` | string | yes | One-paragraph justification.
`axes` | array<Axis> | yes | Per-axis breakdown. Standard axes are `correctness`, `clarity`, `brevity`; the comparator MAY add others.

Axis object:

Field | Type | Required | Meaning
------|------|----------|--------
`name` | string | yes | Short axis label. Lowercase, kebab-case.
`winner` | string | yes | `A`, `B`, or `tie`.
`notes` | string | yes | One-to-two-sentence rationale for this axis.

---

## Versioning policy

- The top-level `version` integer on every file is independent. Bumping one does not require
  bumping the others.
- A non-breaking addition (e.g. adding an optional field, adding a new value to an enum that
  consumers already tolerate) does NOT bump `version`. Update this doc and add the field as
  "no required".
- A breaking change (renamed field, removed field, semantic change of an existing field) bumps
  `version` and the consumer MUST read the new shape only when `version` matches. Producers
  MUST emit the highest version they support.
- Pin a baseline to the upstream Claude Code framework quarterly. Any drift from their shape
  must be deliberate (recorded in this file under a "Deltas from upstream" section, which we
  will add the first time we deliberately diverge — empty as of Phase 1).
