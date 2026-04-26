# Skills × Agent Fleet — Alignment Plan

**Date:** 2026-04-26
**Trigger:** Aremko receptionist (PR #174) crystallised what skills are actually for: wrapping a specialist's tools + domain knowledge + voice into a reusable file that any agent in the fleet can include. Today's marketplace plumbing (3 tiers, 4 engines, semantic auto-match, MCP exposure) was designed before that use case was clear. This plan aligns the system to the actual use case and removes the rest.

## Vision (user statement, paraphrased)

> Skills wrap a specialist — receptionist, vet, booking agent, deal analyst, etc. — into a markdown file with their tools, knowledge, and voice. Agents in the fleet compose one or more skills. When an agent underperforms, the user (or Luna herself, on request) can tweak the skill or agent file from the UI or from chat. The CLI runtime (Gemini / Claude Code / Codex) navigates these folders to find what the active agent needs to do specialised work.

## What we keep

- **File-based markdown with YAML frontmatter** — already working, and matches Codex / Gemini / Claude Code conventions (they look for `SKILL.md` / persona-style files).
- **Skill body injection into CLAUDE.md** — the mechanism that already makes aremko's receptionist work.
- **Per-tenant directory scoping** in `skill_manager.py` — already filters skills correctly per tenant.
- **The aremko_receptionist file** as the canonical template.

## What we change

### A0 — Format compatibility (hard constraint)

Skills and agents MUST round-trip with Claude Code / Anthropic Skills format. A `SKILL.md` we export has to drop into `~/.claude/skills/<slug>/` on someone's laptop and work; a `SKILL.md` written for Claude Code has to import into AgentProvision without manual edits.

**Allowed `SKILL.md` frontmatter** (everything else is rejected on import / stripped on export):
```yaml
---
name: skill-name              # required, kebab-case
description: One sentence — when to use this skill (third person)
allowed-tools: [tool1, tool2] # optional — restricts callable tools
---
```

**Allowed `AGENT.md` frontmatter** (matches Anthropic subagents):
```yaml
---
name: agent-name
description: One sentence describing the agent
tools: [Read, Grep, Bash, mcp__agentprovision__*]  # tool whitelist
model: sonnet                                       # opt-in
---
```

AgentProvision-specific metadata (tenant_id, fork_of, embedding_id, RL stats, `default_skills` composition list, voice, model_tier override, tool_groups) lives **outside** the markdown — either in a sibling `meta.json` next to `SKILL.md` / `AGENT.md`, or as DB rows. The .md file stays portable.

`default_skills` composition — the one feature Claude Code doesn't have natively — gets promoted from per-agent metadata into the frontmatter ONLY if Anthropic ever adopts a similar field. Until then it lives in `meta.json` and our runtime composes it; export to Claude Code drops the field with a comment.

Import endpoint accepts SKILL.md/AGENT.md as-is (single file or zip), validates the frontmatter schema, writes to `_tenant/<uuid>/`. Export endpoint returns the markdown with frontmatter strictly within the Claude Code subset.

### A — Folder layout (mirrors Claude Code's convention)

The split agents-vs-skills + capitalised filenames match `~/.claude/agents/` and `~/.claude/skills/<slug>/SKILL.md` exactly, so the files we author are in principle drop-in usable by any Claude Code instance, and any Claude Code skill we import works without renaming. Gemini CLI's extensions and Codex don't have a comparable agent/skill split, but we lose nothing by being more structured than they are.

```
apps/api/app/agents/                       # NEW — fleet agent definitions
│   ├── _bundled/                           # platform-default agents (read-only at runtime)
│   │   ├── luna/AGENT.md
│   │   ├── code-agent/AGENT.md
│   │   └── …
│   └── _tenant/<tenant_uuid>/<slug>/AGENT.md
apps/api/app/skills/                       # reuses existing root
│   ├── _bundled/                           # platform-default skills
│   │   ├── receptionist/SKILL.md           # generic specialist
│   │   ├── booking/SKILL.md
│   │   ├── cardiology-report/SKILL.md
│   │   └── …
│   ├── _tenant/<tenant_uuid>/<slug>/SKILL.md
│   │   └── aremko_receptionist/SKILL.md
│   └── _archive/                           # GWS imports, orphan tenant, auto-generated junk
```

Properties:

- **Agents (the WHO) and skills (the WHAT) are separate top-level folders**, both with `_bundled` / `_tenant/<uuid>` scope.
- **Filenames are uppercased** (`AGENT.md`, `SKILL.md`) to match Claude Code conventions and the GWS import pattern we already adapted. Today's `skill.md` files all rename in one mechanical pass.
- **Two scopes only**: `_bundled` (shipped with the platform, read-only at runtime) or `_tenant/<uuid>` (editable by that tenant). Drop the `native / community / custom` tiers — they don't map to anything operational.
- **An agent composes skills** via frontmatter:
  ```yaml
  # apps/api/app/agents/_tenant/<uuid>/aremko-receptionist/AGENT.md
  ---
  name: aremko-receptionist
  description: Receptionist for Aremko Spa & Cabañas
  tools: [aremko, calendar]                     # tool-group whitelist
  default_skills: [receptionist, booking, escalation]
  model_tier: full
  voice: warm, brief, Chilean Spanish
  ---
  ```
- **A skill is the same shape Claude Code uses** — minimal frontmatter (`name`, `description`, optional `allowed-tools`) and a markdown body that's the reusable specialist instruction set.
- An `_archive/` sibling holds the 57 GWS imports and the orphan `tenant_271e5a66-…` dir for one release cycle, then gets deleted.

### B — Agent ↔ skill composition

Today: each agent binds to **one** skill via `agent.config.skill_slug`. We need many-to-one.

Frontmatter on `agent.md`:

```yaml
---
name: Aremko Receptionist Agent
slug: aremko_receptionist_agent
default_skills: [receptionist, booking, escalation]
tool_groups: [aremko, calendar]
default_model_tier: full
voice: "warm, brief, Chilean Spanish"
---
```

When the chat path renders CLAUDE.md, it concatenates the agent body + each referenced skill body in declared order. `agent.config.skill_slug` is upgraded to `agent.config.skills` (list, per-agent override). Backward-compat: if `skill_slug` is set, treat as `skills: [<slug>]`.

### C — Editable from the frontend (EXTEND existing flows, don't rebuild)

**Agent edit** — already partially wired. Make it whole:

- `AgentDetailPage` already has 9 tabs including a read-only `config` tab. **Make the config tab editable**: replace the static cards + raw JSON block with a form (system_prompt textarea, model tier dropdown, temperature/max_tokens inputs, **skills multi-select**, tool_groups multi-select). Save calls existing `agentService.update(id, data)` (PUT `/agents/{id}` already exists).
- The 5-step `AgentWizard` is create-only today (`agentService.create`). Either reuse it as `AgentWizard mode="edit"` (preferred, keeps one form) or just leave it create-only and use the inline config-tab editor for edits. Recommend the latter — wizard is too heavy for "tweak the system prompt."
- **Multi-skill composition**: the existing wizard `SkillsDataStep` binds `agent.config.tools` (tool group strings, not skills). Add a parallel `agent.config.skills` field — an ordered list of skill slugs. The runtime concatenates each referenced skill body into CLAUDE.md after the agent body. Backward-compat for `agent.config.skill_slug` (single-slug → list of one).

**Skill edit** — already works. Surgery only:

- `SkillsPage` already has `setEditSkill(skill)` opening a modal with frontmatter form + body editor + `updateFileSkill(slug, payload)` save (`PUT /skills/library/{slug}`). **Keep this.**
- Drop the Native/Community/Custom tier tabs — replace with two columns: "Agents" / "Skills" (or a scope selector: Bundled / My tenant).
- Drop `python` and `shell` from the engine selector in the create modal — 0 executions in 7 days, dead path. Keep `markdown` (and add `agent` for agent personas).
- Filter out `category = 'auto-generated'` skills from the list (the 2026-04-18 plan called this out and it's still showing up).
- Editing a `_bundled` skill forks it to `_tenant/<uuid>/<slug>/` automatically — bundled files are never overwritten. New behaviour, but cleanly contained in the save handler.

API surface:

```
GET  /api/v1/library/agents              # list visible to tenant
GET  /api/v1/library/skills              # list visible to tenant
POST /api/v1/library/agents              # create new tenant agent
PUT  /api/v1/library/agents/{slug}       # update (forks bundled if needed)
GET  /api/v1/library/skills/{slug}/raw   # raw markdown
POST /api/v1/library/skills              # create
PUT  /api/v1/library/skills/{slug}       # update
DELETE /api/v1/library/skills/{slug}     # tenant only — bundled is immutable
```

### D — Editable from chat

Two new MCP tools, gated by tenant role (admin or owner of the agent):

- `update_skill(slug, new_body)` — rewrites a `_tenant/<uuid>/skills/<slug>/skill.md` and emits an audit row.
- `update_agent(slug, frontmatter_patch=None, body=None)` — same for agents.

User flow: *"Luna, update your aremko receptionist skill to also list desayunos when someone asks about breakfast packages."* → Luna calls `update_skill(slug='aremko_receptionist', new_body=...)` → file is rewritten → next chat turn picks up the new body.

Safety: edits are diffed and persisted into a new `library_revisions` table so the user can roll back. Bundled files cannot be edited via this tool — the call forks first, exactly like the UI.

### E — CLI runtime navigation

The CLI agents (Gemini / Claude Code / Codex) already get the rendered CLAUDE.md per turn. To match the user's "navigates these folders when they want to do specialized work":

- Mount `apps/api/app/library/` read-only into the code-worker container at `/workspace/library`.
- Add an MCP tool `list_skills_in_library(scope='_bundled' | '_tenant')` that returns slugs + summaries — so Luna mid-turn can decide "I need to also pull the booking skill" and call `read_skill(slug)` to read its body.
- This is opt-in per turn, not automatic. Keeps the hot-path latency stable.

## What we delete

| Today | Action |
|---|---|
| `apps/api/app/skills/sql_query/` etc. (top-level dups of `native/`) | Delete after move to `_bundled/` |
| `apps/api/app/skills/community/` (57 GWS imports, 0 usage) | Move to `archive/`, hide from UI, drop after one release |
| `apps/api/app/skills/tenant_271e5a66-…/` (orphan tenant) | Delete |
| Skills with `category = 'auto-generated'` in DB | Hard-delete |
| `engine: python / shell / tool` paths in `skill_manager.py` | Remove (0 executions in 7d). Keep `markdown` only |
| `match_skills_to_context` MCP tool wiring + `auto_trigger` embeddings | Remove — not on chat hot path, not worth maintaining until needed |
| Tier tabs (Native / Community / Custom) in SkillsPage | Remove — replaced by Agents / Skills columns |
| `vw_fabrication_candidates` and `tool_calls` audit | KEEP — it's the only thing that lets us measure if any of this works |

## What we keep on the shelf

- The Part B "expose skills as MCP tools for external agents" idea from the original 2026-04-18 plan. Real use case but not until skills are stable enough that a third-party would actually want them.
- Skill-to-skill `chain_to`. Useful but no demand.
- Engine = python / shell. Resurrect when there's a concrete need — most "skills" are actually domain prompts, not callable scripts.

## Migration approach (risk-managed)

### Phase 0 — Inventory + freeze
1. Tag the current state (`pre-fleet-alignment`) so we can revert atomically.
2. Compute a manifest of every skill + every binding so we can verify post-migration counts.

### Phase 1 — Folder move (mechanical, no behavior change)
3. New layout under `apps/api/app/library/`. `skill_manager.py` learns to read from both old and new for one release, with a deprecation log when it falls back to the old path.
4. Move bundled skills to `library/skills/_bundled/`. Delete the duplicated top-level dirs.
5. Move per-tenant skills to `library/skills/_tenant/<uuid>/`.
6. Move the auto-generated junk and the GWS imports to `library/archive/`.
7. Bind tests: scan all `agent.config.skill_slug` values and verify each still resolves.

### Phase 2 — Many-skills composition
8. Add `agent.config.skills` (list). Reader prefers `skills`, falls back to `skill_slug`. Writers write `skills`.
9. `cli_session_manager.generate_cli_instructions` concatenates each skill body in order, after the agent body.
10. Existing aremko binding stays working without changes (`skill_slug: aremko_receptionist` reads as `skills: [aremko_receptionist]`).

### Phase 3 — UI editor (EXTEND existing flows)
11. **AgentDetailPage `config` tab** becomes editable: replace the read-only cards + JSON block with a form (system_prompt textarea, tier dropdown, temperature/max_tokens, **skills multi-select**, tool_groups multi-select). Wires to existing `agentService.update`.
12. **SkillsPage**: drop tier tabs → replace with Bundled / My-tenant scope selector (or Agents / Skills two-column). Keep the existing edit modal, drop `python`/`shell` engines from creation, filter `auto-generated` from list.
13. **Bundled-fork-on-edit** in the existing `updateFileSkill` save handler — never overwrite shipped files.

### Phase 4 — Chat-side editing
14. `update_skill` and `update_agent` MCP tools, tenant-admin gated, with diff persistence in a new `library_revisions` table.
15. Audit log surfaces every edit with actor + diff.

### Phase 5 — Code-worker library mount
16. Mount `apps/api/app/library/` read-only into the code-worker container.
17. Two MCP tools: `list_library_skills(scope)` and `read_library_skill(slug)`. Mid-turn discovery.

### Phase 6 — Cleanup
18. Delete the dual-read fallback in `skill_manager.py`.
19. Delete `engine: python/shell/tool` execution paths.
20. Delete `match_skills_to_context` and the auto_trigger embedding sync.
21. Drop `library/archive/` after one full release cycle without complaints.

## Acceptance

- A tenant admin can create an agent + 1-3 skills entirely from the UI in under 2 minutes.
- A tenant admin can ask Luna in chat *"update your X skill to also do Y"* and the next turn reflects the change.
- The library tree under `apps/api/app/library/` has zero duplicate files. Every file is reachable from a real `agent` or `skill` binding.
- The aremko receptionist works identically before and after the migration (verified against the current chat tests).
- `vw_fabrication_candidates` for the aremko tenant does NOT regress (skill body intact through migration).
- The SkillsPage browser-traffic floor changes from "0 hits per day" to "non-trivial" — proving the UI is now usable.

## Sequencing recommendation

Phases 1 + 2 are the unblock. They're mechanical, low risk, and they expose the "compose multiple skills" capability that fleet agents have been waiting for. Ship those as one PR.

Phase 3 (UI editor) is the highest-value follow-up — that's what makes "tweaking when an agent underperforms" actually realistic.

Phase 4 (chat-side editing) is the wow-factor but should wait until 3 lands and we've used the UI editor enough to know the data shape.

Phase 5 (code-worker mount) is a power-user capability — defer until a concrete CLI agent actually needs to discover skills mid-turn.

Phase 6 cleanup happens after Phase 5 stabilises.

## Open questions for you

1. **Naming**: `library` vs `fleet` vs `personas` — any preference? `library` reads well in URLs (`/library/skills/aremko_receptionist`) and matches what the CLI agents will navigate.
2. **Bundled agent set**: which agents should ship in `library/agents/_bundled/`? Today native has Luna + integral-business-support + integral-devops + integral-sre. Do we want a curated set (Luna, Code Agent, Sales, Support, Receptionist) or just port what exists?
3. **Forking semantics**: when a tenant edits a bundled skill, should the fork show as the SAME slug (overrides bundled for this tenant) or a new slug? I'd default to "same slug, tenant version wins" — simpler mental model.
4. **`update_skill` from chat — who's allowed?** Per-tenant admin only? The agent owner (`agents.owner_user_id`)? Anyone in the conversation? My default would be admin or owner only, with audit log.
