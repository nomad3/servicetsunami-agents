# Codebase Cleanup & Hardening Plan

**Date:** 2026-04-22
**Scope:** Low-risk, incremental cleanup of `apps/api`, `apps/mcp-server`, `apps/web`, `apps/code-worker` in preparation for enterprise deployment (Levi's demo + BYO-infra rollout).

## Principles

1. **No behavior changes.** Each PR in this plan must produce identical runtime behavior. Cleanup ≠ refactor. If a change is semantic, it goes in a separate, explicitly-scoped PR.
2. **One concern per PR.** Seven phases, seven PRs (or more if a phase splits naturally). Easy to revert, easy to review.
3. **Tests or probes gate every merge.** If there's no test, add a probe: API startup succeeds, migrations apply, `/api/v1/agents` returns 200, Luna responds to a known prompt. Don't trust type-check alone.
4. **Leave the codebase strictly better each pass.** Net LOC should go down or stay flat; no new abstractions unless they retire two old ones.
5. **AI slop is a category, not a judgment.** Specifically: (a) stale docstrings that contradict the code, (b) placeholder comments like `# TODO: implement`, (c) dead files with AI-style names (`Accounting — Tool Handler (818b25)`), (d) over-commented obvious code, (e) leftover "example" code paths wired to nothing.

## Ground truth (as of 2026-04-22 main @ `673389b2`)

Measured via grep/find:

- **400** Python files under `apps/api/app/`
- **156** JS/JSX files under `apps/web/src/`
- **50** Python files under `apps/mcp-server/src/`
- **93** service modules in `apps/api/app/services/` — suspicious, likely has duplicates
- **73** route modules in `apps/api/app/api/v1/` — likely has duplicates
- **86** `except Exception: pass` occurrences across 30 files — many are load-bearing, some are lazy
- **3 copies** of Luna's `skill.md`: `skills/native/luna/`, `skills/native/agents/luna/`, `skills/agents/luna/`
- **Triple-nested** skill dirs: `skills/native/native/…`
- **Skill directories with AI-generated names**: `Accounting — Tool Handler (818b25)`, `Operations — Tool Handler (e6ac67)`, `Real Estate — Tool Handler (b9725a)`, `Accounting — Tool Handler (d62acf)` — these are slop
- **Orphan tenant dir inside native skills**: `skills/native/tenant_271e5a66-…/dental_revenue_auditor` — wrong layout, tenant skills shouldn't live inside `native/`
- **Explicitly-legacy module**: `apps/api/app/services/llm/legacy_service.py` (still referenced? unknown)
- **Dual chat services**: `chat.py` (521 LOC) and `enhanced_chat.py` (169 LOC) — need to know which is the real one
- **Residual AgentKit**: removed 2026-04-19, but `agent_kit.pyc`, `agent_kits.pyc`, schemas still cached. Source already gone; stale bytecode in VCS-ignored `__pycache__`.
- **Largest service files**: `workflow_templates.py` (1582), `whatsapp_service.py` (1473), `knowledge.py` (1096), `memory_recall.py` (968), `cli_session_manager.py` (890) — candidates for split, but not in this cleanup (scope discipline)

## Non-goals (explicitly out of scope)

- Rewriting any service. Split-long-file refactors go in a separate plan.
- Introducing new frameworks (e.g. Alembic, Pydantic v3, TypeScript conversion for web).
- Performance optimization.
- Adding tests for uncovered code. Only tests needed to gate cleanup changes.
- Touching migration SQL. Column renames and schema changes are out of scope.
- Touching `migration/` import directory — that's vendored legacy source.

---

## Phase 1 — Cruft removal (PR #1, ~1 day, zero risk)

**Goal:** Delete files that are obviously dead, no import hits, no route mounts.

### Tasks

1. **Drop AI-slop skill directories** (4 dirs, duplicated twice each under `skills/` and `skills/native/`):
   - `skills/Accounting — Tool Handler (818b25)/`
   - `skills/Operations — Tool Handler (e6ac67)/`
   - `skills/Accounting — Tool Handler (d62acf)/`
   - `skills/Real Estate — Tool Handler (b9725a)/`
   - Same four under `skills/native/…`
   - Verify: `grep -r "Tool Handler" apps/api/app --include='*.py'` returns zero hits before deleting.

2. **Flatten skill directory layout**:
   - `skills/native/native/` (triple-nested) — merge contents into `skills/native/` if any, delete the nested `native/`.
   - `skills/native/agents/luna/` and `skills/agents/luna/` — these duplicate `skills/native/luna/`. Identify the one `skill_registry_service.sync` actually reads, delete the other two. Check `SkillRegistry.seed_paths` (or equivalent) in `skill_registry_service.py`.
   - `skills/native/tenant_271e5a66-…/` — tenant skills don't belong in `native/`. Move to `skills/custom/tenant_…/` or delete if orphaned. Check the tenant UUID: if that tenant still exists, migrate; if not, delete.

3. **Remove `legacy_service.py`**:
   - `apps/api/app/services/llm/legacy_service.py` — grep for imports; if unused, delete. If referenced, file a one-line follow-up issue noting the callers need migration.

4. **Sweep stale `__pycache__` residue that corresponds to deleted source** (AgentKit, etc.):
   - `agent_kit*.pyc`, `agent_kits*.pyc` — these are local cache artifacts, typically gitignored. Confirm `.gitignore` has `__pycache__/` and `*.pyc`. If any `.pyc` is tracked, delete and rm from git.

5. **Remove empty directories** created by earlier removals:
   - `apps/api/app/api/v1/endpoints/` — check if empty, delete.
   - Any `apps/api/app/services/*/` that only contains `__init__.py` and a stub.

6. **Remove PR-scope media assets accidentally committed to `apps/web/public/images/`**:
   - `"Creación_de_Transición_de_Imagen_a_Video.mp4"` (unicode filename, 95% chance unused)
   - `Gemini_Generated_Image_fovh8nfovh8nfovh.png` (AI-generated placeholder name)
   - `Gemini_Generated_Image_lka21blka21blka2-2.png`
   - Grep web source for references before deleting. If untouched, drop them — they bloat the bundle.

### Validation

- `python -m py_compile` on every remaining `.py` under `apps/api/app/`.
- API container startup: `docker compose up -d api` → healthy within 60s.
- Web build: `cd apps/web && npm run build` → no new warnings.
- Skill sync log line: count of skills registered stays constant *or* decreases (if we removed duplicates Luna was double-seeding).

### Estimated cleanup

- ~20 directories deleted, ~8 files deleted, ~3MB bundle reduction.

---

## Phase 2 — Service / route deduplication (PR #2, ~2 days, low risk)

**Goal:** Collapse `chat.py` vs `enhanced_chat.py` and audit the 93 service modules / 73 route modules for duplicates.

### Tasks

1. **Resolve `chat.py` vs `enhanced_chat.py`**:
   - Map every import of each. `enhanced_chat.py` is 169 LOC with a `write_audit_log` import — likely the real entrypoint. `chat.py` at 521 LOC may be legacy, WhatsApp-flavored, or the inner engine.
   - Determine: (a) which one does chat routes call, (b) which one does WhatsApp use, (c) is one a superset of the other.
   - Outcome: one authoritative `chat_service.py` (rename if needed), the other deleted or made a thin facade that re-exports.
   - **Guardrail:** before merging, tail the API logs during 3 chat turns (2 web + 1 WhatsApp); logs must match pre/post.

2. **Resolve `services/memories.py` vs `services/memory/memory_service.py`**:
   - The `memory/` subpackage has one file. Collapse into `services/memories.py` or vice versa. Grep call sites.

3. **Audit the 93 service modules for near-duplicates.** Specific candidates based on grep:
   - `agents.py` vs `agent_tasks.py` vs `agent_identity.py` vs `agent_identity_service.py` vs `agent_groups.py` — is there a duplicate pair?
   - `collaboration_events.py` vs `collaboration_service.py` vs `coalition_service.py` vs `blackboard_service.py` — the A2A surface. Probably all legit, but verify.
   - `knowledge.py` vs `knowledge_extraction.py` — both large; confirm clean separation.
   - Produce a dependency graph (`pydeps` or manual) of services importing services. Files with zero inbound imports are candidates for deletion.

4. **Audit the 73 route modules for dead mounts.** `routes.py` imports every router; anything imported but not mounted, or mounted and never hit, should be flagged:
   - Run the API for 24h with normal traffic, capture access-log endpoints → set-diff against the mount list → dead routes.
   - Don't delete immediately; mark with `# TODO(cleanup-phase-2): confirm no callers 2026-05`.

### Validation

- `grep -r "from app.services.chat" apps/` and `from .chat import` hit count unchanged post-rename (or explicitly updated).
- Chat turn latency p50 unchanged.
- No 5xx on any route for 1 hour of production-like traffic.

---

## Phase 3 — Error handling cleanup (PR #3, ~2 days, medium-low risk)

**Goal:** Convert the 86 bare `except Exception: pass` sites into either (a) explicit exception types with logging, (b) `contextlib.suppress(...)` if the swallow is deliberate, or (c) actually propagate.

### Approach

Not every `except: pass` is lazy. Three categories:

1. **Load-bearing** (must stay swallowed): audit log writes, telemetry emission, optional integrations (Redis, Temporal optional in dev). Keep but:
   - Narrow the exception to the specific one thrown (`except redis.ConnectionError`, `except TemporalError`, etc.)
   - Add `logger.debug("...")` so the failure is at least traceable at DEBUG.
   - Add a one-line comment `# Telemetry must not fail user path`.

2. **Lazy / dishonest**: swallowing to hide a real bug. Pattern is usually `try: return result except: return None`. These are where bugs hide.
   - Replace with explicit exception handling.
   - If the result genuinely may be None, fix the type signature to reflect it.
   - If the "error" is actually expected (e.g. 404), handle it explicitly.

3. **Panic-bare** (highest risk): `try: db.commit() except: pass`. These lose data silently. Always fix — either rollback + raise, or log + re-raise.

### Hit list (by file — decide category, fix accordingly)

Top offenders:
- `whatsapp_service.py` — 18 sites (probably mostly load-bearing — external dep)
- `agent_router.py` — 18 sites (suspicious — needs audit)
- `chat.py` — 6 sites
- `claude_auth.py` — 5 sites
- `cli_session_manager.py` — 4 sites
- `main.py` — 4 sites (startup is a valid place to swallow)
- `gemini_cli_auth.py` — 3 sites
- `skills_new.py` — 3 sites

### Validation

- Structured log volume at DEBUG should increase (visible failures) but ERROR log volume should not increase.
- All existing tests pass.
- Manually exercise chat, workflow creation, WhatsApp message → no new errors surfaced that weren't already happening.

---

## Phase 4 — Type strengthening (PR #4, ~2 days, low risk)

**Goal:** Lean on Pydantic 2 + MyPy-lite strictness on the API boundary. Do **not** add MyPy to the whole codebase — that's a separate investment.

### Tasks

1. **Every Pydantic response model must match the ORM model's types.**
   - We just shipped a bug where `TestRunOut.created_at: Optional[str]` mismatched `AgentTestRun.created_at: DateTime`. Pydantic 2 refused to coerce, every response 500'd.
   - Action: write a small script that scans all `response_model=` usages in `apps/api/app/api/v1/` and verifies the Pydantic class fields' types are assignable from the corresponding ORM column types. Flag mismatches.
   - Fix flagged sites.

2. **Replace `Any` in API schemas** where the shape is actually known.
   - Grep `apps/api/app/schemas/` for `Any`, `dict`, `list` (un-parametrized). Each one is a lost contract.
   - Don't chase every one — focus on request and response models for public routes.

3. **Add return-type annotations to service entry points.**
   - Each function called from a route handler should declare its return type. Makes refactors safer and IDE-assist work.

4. **`Optional[X] = None` consistency.**
   - Pydantic 2 is stricter about "optional vs default None." Sweep for `foo: X = None` (implicit optional) → `foo: Optional[X] = None` (explicit).

### Validation

- The Pydantic-ORM consistency script passes with zero mismatches.
- API still starts, routes still 200.
- `ruff check apps/api/app` (new or existing) passes without new warnings.

---

## Phase 5 — Circular dependency audit (PR #5, ~1 day, low risk)

**Goal:** Identify and break any import cycles. Nothing here is *known* to be cyclic — this is a diagnostic pass.

### Approach

1. Install `pydeps` or `import-linter` (dev dep only).
2. Run against `apps/api/app/` and produce a graph.
3. Any cycle → document it, break it via:
   - Moving the shared symbol to a `types.py` or `schemas/` module that both sides import.
   - Switching to lazy-import inside the function body (acceptable for cross-service calls).
   - Dependency-inverting via an abstract base class (last resort, only if genuinely cyclic).

### Likely cycles (based on layout)

- `services/enhanced_chat.py` ↔ `services/cli_session_manager.py` — both reference each other indirectly via the orchestration flow.
- `services/audit_log.py` imported by routes, which are imported by services for RBAC checks. Unlikely, but worth verifying.
- Memory package: `app/memory/` (new) vs `app/services/memories.py` vs `app/services/memory/memory_service.py` — triangle relationships.

### Validation

- `pydeps` reports zero cycles after fixes.
- Python startup time on the API container measurable and unchanged or improved.

---

## Phase 6 — Deprecated code removal (PR #6, ~1 day, low risk)

**Goal:** Remove modules/functions explicitly marked legacy/deprecated.

### Targets

1. `apps/api/app/services/llm/legacy_service.py` — name says it all. Verify zero callers (done in Phase 1 confirmation), delete.
2. All AgentKit residue:
   - If `agent_kit.py`, `agent_kits.py`, `agent_kit.py` schema exist in source (not just cache), delete.
   - Grep `AgentKit` in `apps/web/src/`; remove any residual frontend references.
3. **Legacy chat memory key**: `cli_session_manager.py:806` falls back to `"claude_cli_session_id"` (legacy key). Is anyone still writing that key? If not, drop the fallback.
4. **The `_agent_ordering.py` module** — underscore prefix = private; has 2 TODOs. Either finish it or inline it into its one caller.
5. **`docs/plans/`** — 20+ plan docs. Mark any whose feature has fully shipped with a `SHIPPED.md` note or move to `docs/plans/archive/`. Not deletion, but filing. Future onboarding hits.

### Validation

- API startup clean, all routes 200.
- Frontend builds clean, no missing imports.
- `grep -r AgentKit apps/` returns zero hits.

---

## Phase 7 — Frontend cleanup (PR #7, ~1 day, low risk)

**Goal:** Apply the same discipline to `apps/web/src/`.

### Tasks

1. **Remove the unused images** flagged in Phase 1 (if still present in web repo).
2. **Audit `src/services/` for duplicate clients**:
   - `agent.js`, `memory.js`, `auth.js`, `mediaService.js`, etc. — one per resource is fine; look for duplicates.
3. **Audit `src/components/`** for orphans:
   - Scan every `.js` / `.jsx` for imports; any component in `components/` not imported anywhere is dead.
4. **Remove inline `console.log`** that wasn't intentional:
   - `grep -r "console.log" apps/web/src/` → review each; keep only the ones behind `if (process.env.NODE_ENV === 'development')` guards or explicit debug flags. Strip the rest.
5. **Audit `.ap-*` class usage** per the design-system unification:
   - Any `<div style={{ backgroundColor: '#...' }}>` that could be `var(--ap-*)` → convert.
   - Any new hardcoded hex (non-enum-map) introduced since PR #161 → convert.
6. **Remove `dangerouslySetInnerHTML`** if any — not expected, but worth a grep.

### Validation

- `npm run build` produces a bundle ≤ current bundle size.
- No new ESLint warnings.
- Visual smoke on 6 key pages: chat, agents, agent detail, memory, workflows, integrations.

---

## Phase 8 — AI-slop reduction (PR #8, ~1 day, low risk)

**Goal:** Remove the tonal markers that make the code read as AI-generated.

### Specific patterns to remove

1. **Stale/redundant docstrings** that restate the function signature:
   ```python
   def get_agent(db, agent_id):
       """Get an agent by ID."""  # <- delete
       return db.query(Agent)...
   ```
2. **Section-header comments** for 3-line blocks:
   ```python
   # ── Fetch the data ──
   data = fetch()

   # ── Process the data ──
   result = process(data)
   ```
   Delete all of these. Obvious from code.
3. **Trailing "for future use" / "placeholder for now" comments.** Grep, review, delete.
4. **Over-parenthesized regex / SQL comments** explaining syntax.
5. **"Helpful" exception messages that duplicate the exception type**:
   ```python
   raise ValueError("ValueError: invalid input")  # <- just raise ValueError("invalid input")
   ```
6. **AI-style enum/list values** masquerading as data (`"lorem ipsum"`, `"sample_data"`, `"your_input_here"`) in config files.
7. **Emoji in log lines / code comments** that the user hasn't explicitly asked for — per CLAUDE.md convention.

### Approach

- Do this pass manually, file-by-file, for the 10 largest service files (already listed above).
- Don't automate with find-replace — context matters.

### Validation

- Net LOC reduction measurable.
- `git diff --stat origin/main` shows only `.py` and `.js` changes, no behavior diff.
- Code reads as maintained by humans, not generated in one pass.

---

## Execution order & risk profile

| Phase | Risk | Reverts needed if broken | Parallelizable |
|-------|------|---------------------------|----------------|
| 1 Cruft | Zero | Just `git revert` | — |
| 2 Service dedup | Medium | Could break chat path; easy revert | Sequential after 1 |
| 3 Error handling | Medium-low | Could surface errors that were previously hidden (feature not bug) | After 2 |
| 4 Types | Low | Pure type annotations; no runtime change | Parallel with 3 |
| 5 Circular deps | Low | Lazy-imports may slow startup slightly | After 2, 4 |
| 6 Deprecated | Low | Deletions; revert by restoring | Parallel with 5 |
| 7 Frontend | Zero-low | Isolated; no backend impact | Parallel with any |
| 8 AI slop | Zero | Comment/style only | Parallel with any |

**Suggested sequencing:**
- Week 1: PR #1 (cruft), PR #2 (service dedup).
- Week 2: PR #3 (error handling), PR #4 (types) in parallel.
- Week 3: PR #5 (circular deps), PR #6 (deprecated), PR #7 (frontend), PR #8 (slop) in parallel.

Total: ~10 engineering days spread over 3 weeks. Can be compressed to 5 days if one person focuses on it.

## Measurements (track before/after per PR)

- Lines of Python in `apps/api/app/`
- Lines of JS in `apps/web/src/`
- API container startup time (ms to first healthy probe)
- Web bundle size (main.*.js gzipped)
- `except Exception: pass` count
- Count of files with zero inbound imports (dead file count)
- Ruff/ESLint warning count
- Test pass rate (we'll add a minimal API smoke test for this pass even though test coverage is out of scope)

## Rollback / safety

- Every PR is independently revertable via `git revert <merge-sha>`.
- No PR changes database schema, migration sequence, or API contracts visible to the frontend.
- Each PR runs through the existing CI redeploy pipeline before the next phase starts.
- If any phase surfaces a regression in production (chat p50, error rate, memory extraction), pause the next phase until diagnosed.

## Success criteria

After all phases ship:

- `apps/api/app/services/` and `apps/api/app/api/v1/` each drop at least 10% of files.
- `apps/web/src/` bundle drops ≥ 5%.
- Zero `except Exception: pass` without either a narrowed exception type or a comment justifying the swallow.
- Zero `Any` / untyped dict in public API response models.
- Zero files matching AI-slop patterns from Phase 8.
- All existing functionality still works (Luna responds, Memory tab shows entities, workflows create, agents promote with gate, marketplace publishes).
- Onboarding a new engineer: reading the top 10 service files conveys the architecture without needing CLAUDE.md as a decoder ring.
