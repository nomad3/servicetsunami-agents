# Community Superpowers Skills — Global Import Plan

> **For agentic workers:** Use `superpowers:executing-plans` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Import the highest-value Claude Code community skills (from `obra/superpowers` and `angakh/claude-skills-starter`) into the platform's `_bundled/` tier so every agent, every CLI runtime (Claude Code, Codex, Gemini), and every tenant benefits automatically — no manual installation required.

**Why:** Simon uses a curated set of markdown skills daily in Claude Code CLI (writing plans, code review, smart commits, quality gate). Before this work, those lived only in external repos and had to be re-imported per session. By shipping them as bundled native skills, they auto-trigger via pgvector semantic matching and are available to the code-worker and all future agents without any per-tenant setup.

**Architecture:** The three-tier skill system (`_bundled/` → native, community, tenant) already exists. `SkillManager.scan()` seeds `_bundled/` into the `native/` dir on API startup. `skill_registry_service` embeds each skill into pgvector on sync. This plan adds to `_bundled/` and patches `_execute_markdown` to handle skills that use the `skill.md` body as the prompt (no separate `prompt.md` file needed).

**Branch:** `feature/community-skills-import` → **PR #528** (open, ready for review).

**Out of scope:**
- Building a live-sync pipeline that tracks upstream repo changes automatically (manual refresh via `import_community_skills.py`)
- Importing skills with `python` or `shell` engines from external sources (security risk without sandboxing review)
- UI for browsing/installing individual bundled skills (already served by `/api/v1/skills/library`)

---

## Table of Contents

- [§1 Skill Curation (Tasks 1-2)](#1-skill-curation)
- [§2 Skill Files (Tasks 3-4)](#2-skill-files)
- [§3 Execution Fix (Tasks 5-6)](#3-execution-fix--markdown-engine)
- [§4 Verification Tooling (Task 7)](#4-verification-tooling)
- [§5 Acceptance Gates (Tasks 8-10)](#5-acceptance-gates)

---

## §1 Skill Curation

**Goal:** Define exactly which skills to import and from where. Manual curation is intentional — upstream repos change in breaking ways; we need a stable, vetted list.

### Task 1: Source selection

**Decision:** Two upstream repos chosen after reviewing the `hesreallyhim/awesome-claude-code` catalogue:

| Repo | Why |
|---|---|
| `obra/superpowers` | The gold standard for structured TDD workflows (`writing-plans`, `executing-plans`). These are the exact skills Simon uses for planning sessions. |
| `angakh/claude-skills-starter` | Comprehensive dev-workflow skills (commit, PR, tests, quality-gate, dep-audit, scaffold, project-overview, design-pipeline). Well-maintained, all markdown. |

**Not selected:** `hesreallyhim/awesome-claude-code` (is a catalogue, not skills), `benjaminlq/claude-skills` (Python engine, requires sandboxing review), any framework-specific skills.

- [x] Source list documented in `scripts/import_community_skills.py` as `SKILL_SOURCES` registry

### Task 2: Skill shortlist

12 skills selected across two categories:

| Slug | Category | Daily Use Case |
|---|---|---|
| `writing-plans` | coding | Create structured TDD implementation plans |
| `executing-plans` | coding | Walk plans task-by-task with checkpoint discipline |
| `code-review` | coding | Systematic pre-merge review checklist |
| `security-review` | coding | OWASP-aligned security pass before PRs |
| `smart-commit` | coding | Conventional commit with auto-generated message |
| `pr-create` | coding | PR with structured summary + test plan |
| `run-tests` | coding | Fail-fast test runner with coverage reporting |
| `quality-gate` | coding | lint → typecheck → test pipeline (blocks code-worker PRs) |
| `dep-audit` | devops | CVE scan on installed dependencies |
| `project-overview` | productivity | Orient a new agent session in the codebase |
| `scaffold` | coding | Generate new service/module boilerplate |
| `design-pipeline` | productivity | Structure a design doc → review → build pipeline |

- [x] All 12 slugs confirmed against upstream repos

---

## §2 Skill Files

**Goal:** Write YAML-frontmatter `skill.md` files for each skill under `apps/api/app/skills/_bundled/<slug>/`.

### Task 3: File structure per skill

Each skill directory has exactly one file: `skill.md`. No `script.py`, no `prompt.md`. The markdown body IS the prompt — `_execute_markdown` reads it and returns it as the skill output for the calling agent.

Required frontmatter fields:
```yaml
name: Human Readable Name
description: One-line summary for pgvector auto-trigger matching
engine: markdown
version: 1
category: coding | devops | productivity
tags: [tag1, tag2]
auto_trigger: "the phrase or intent that routes to this skill"
source_repo: https://github.com/upstream/repo
```

- [x] 12 `skill.md` files created in `apps/api/app/skills/_bundled/`

### Task 4: Frontmatter validation

`auto_trigger` field is critical — it's what the pgvector matcher uses to route chat messages to skills. Each skill has a distinct trigger phrase that doesn't overlap with other skills.

- [x] All `auto_trigger` fields set and non-overlapping

---

## §3 Execution Fix — Markdown Engine

**Goal:** Fix `_execute_markdown` and `_parse_skill_md` so skills with `engine: markdown` and no separate `prompt.md` actually run.

### Task 5: Fix `_parse_skill_md` default script_path

**Root cause:** `_parse_skill_md` was defaulting `script_path` to `"script.py"` regardless of engine. When `execute_skill` hit a markdown skill, it checked for `script.py`, didn't find it, and returned an error.

**File:** `apps/api/app/services/skill_manager.py`

```python
# Before (line ~134)
script_path=metadata.get("script_path", "script.py"),

# After
script_path=metadata.get(
    "script_path",
    "skill.md" if metadata.get("engine") == "markdown" else "script.py"
),
```

- [x] Default corrected in `_parse_skill_md`

### Task 6: Fix `execute_skill` to skip file-existence check for markdown/tool engines

**Root cause:** `execute_skill` was asserting the script file exists before dispatching to engine handlers. Markdown skills don't need a separate script file.

**File:** `apps/api/app/services/skill_manager.py` (in `execute_skill`)

```python
# Skip file-existence check for non-file engines
if skill.engine not in ("markdown", "tool"):
    script = Path(skill.skill_dir) / skill.script_path
    if not script.exists():
        return {"error": f"Script not found: {script}"}
```

Also: `_execute_markdown` now falls back to `skill.md` body if explicit `script_path` doesn't exist, and strips YAML frontmatter before returning the prompt body.

- [x] File-existence gate bypassed for markdown engine
- [x] Frontmatter stripped from markdown body before returning

---

## §4 Verification Tooling

**Goal:** Ensure the verifier catches real runtime failures, not just YAML parse errors.

### Task 7: Upgrade `scripts/import_community_skills.py`

The original verifier only parsed YAML frontmatter. It reported all skills valid while `run_skill("code-review")` would fail at runtime.

New `verify_skills()` checks the actual executable source per engine:

| Engine | Check |
|---|---|
| `markdown` | `skill.md` body is non-empty after stripping frontmatter |
| `python` | `script.py` (or `script_path`) file exists |
| `shell` | `script.sh` (or `script_path`) file exists |
| `tool` | `tool_class` field is set in frontmatter |

Script exits with code 1 on any error so CI can gate on it.

- [x] `verify_skills()` upgraded to engine-aware checks
- [x] Exit-1 on error confirmed

---

## §5 Acceptance Gates

**Goal:** Confirm the skills work end-to-end before merging.

### Task 8: Static verification passes

```bash
python3 scripts/import_community_skills.py --verify
# Expected: 13 skills (12 new + lead_scoring), 0 errors, exit 0
```

- [ ] Run and confirm output

### Task 9: API startup — skill registry sync

```bash
docker-compose restart api
docker-compose logs -f api | grep -E "skill|bundled"
# Expected: "Seeded bundled skill: writing-plans" × 12, no parse errors
```

Then hit `GET /api/v1/skills/library` — all 12 new skills appear with correct `name`, `category`, `engine: markdown`.

- [ ] API restart confirms skill seeding
- [ ] Library endpoint returns all 12

### Task 10: Runtime execution smoke test

Two skills to verify execution works end-to-end:

```bash
# Direct API call
curl -X POST http://localhost:8001/api/v1/skills/run \
  -H "Authorization: Bearer $TOKEN" \
  -d '{"skill_name": "writing-plans", "inputs": {"task": "add Redis cache to the chat service"}}'
# Expected: {"result": "<markdown prompt body>"} — not {"error": ...}

# Via chat (auto-trigger)
# Send: "create an implementation plan for adding rate limiting"
# Expected: skill auto-triggers, code-worker picks up writing-plans instructions
```

- [ ] `writing-plans` run_skill returns markdown body
- [ ] `security-review` auto-triggers on "security review the auth changes"

---

## Current State

| Item | Status |
|---|---|
| 12 `skill.md` files in `_bundled/` | Done (PR #528) |
| `_parse_skill_md` default fix | Done (PR #528) |
| `execute_skill` markdown gate fix | Done (PR #528) |
| `_execute_markdown` frontmatter strip | Done (PR #528) |
| Verifier engine-aware checks | Done (PR #528) |
| Co-author trailer removed from commits | Done (force-pushed) |
| Acceptance gates §5 | Pending merge + deploy |

**Next action:** Review the tasks above, confirm alignment, then merge PR #528. The acceptance gates (Tasks 8-10) should run in staging after merge.
