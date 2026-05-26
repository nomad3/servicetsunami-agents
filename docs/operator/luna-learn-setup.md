# Luna Learn — First-Time Operator Setup

> Audience: operators standing up Luna Learn on a fresh tenant for the first time.
> Phase: 1 (MVP). Read this end-to-end before exposing the feature to a tenant user.

## 1. What this feature does

Luna Learn lets a tenant teach Luna a new skill by sending her a media URL
(YouTube, Instagram reel, etc.). Luna downloads + transcribes the media,
synthesises a `SKILL.md` from the transcript, ships it through the existing
Code Reviewer agent, and — on review pass — installs the skill into the
tenant's `_tenant/<uuid>/` library. Recall surfaces it through the existing
KG once T2.7-followup ships. Full design + decision log lives in
[`docs/superpowers/specs/2026-05-25-luna-learn-from-media-design.md`](../superpowers/specs/2026-05-25-luna-learn-from-media-design.md).

## 2. Required services running

Luna Learn rides on the standard agentprovision stack. All of these must be
healthy before a tenant triggers a learn job:

- `postgres` — durable state, library_revisions audit, migrations
- `mcp-tools` (a.k.a. `mcp-server`) — hosts `learning.*` tool surface,
  ships with `yt-dlp` + `ffmpeg` baked into the image
- `code-worker` — runs the synthesis prompt + skill draft
- `orchestration-worker` — Temporal worker, drives the learn workflow
- `api` — surfaces `alpha learn` CLI + WhatsApp ingress
- `temporal` — workflow durability + resume

If any of these is down, jobs fall back to the cache+notify path described in §5.

## 3. Tenant prerequisites

Before the feature works for tenant `<uuid>`, an operator must confirm:

### 3.1 Code Reviewer agent must be provisioned

The learn workflow dispatches every synthesised skill to a Code Reviewer
agent before install. The agent UUID is currently **hardcoded** to
`755796a4-4cc4-4d1c-99e5-dd9c4f7d0f22` in
`apps/mcp-server/src/mcp_tools/learning.py`.

- ✅ Works on Simon's tenant (that UUID exists there).
- ❌ Other tenants will see every learn job route to cache+notify with a
  "review unavailable" message, until role-based lookup ships.
- Tracked as **T2.4-followup** ("resolve reviewer by `role=code_reviewer`
  per tenant"). Until that lands, you must either re-use Simon's UUID by
  copying the bundled agent into the new tenant under the same UUID, or
  patch the constant.

### 3.2 Migration 156 must be applied

Migration 156 adds Luna's `learning` tool_group. Apply per the
`migration_apply_pattern` memory:

- No auto-runner runs in the api container.
- `docker exec` into postgres, run the SQL file, then manually insert a row
  into `_migrations` (column is `filename`, **not** `name`).
- `*.sql` files require `git add -f` because of the global gitignore.

If the migration is not applied, Luna's `tool_groups` row will be missing
`learning` and the MCP `learning.*` tools will refuse her calls with a
scope-check error.

### 3.3 Environment variables

- `LUNA_LEARN_SYNTHESIS_MODEL` — model used for the SKILL.md synthesis pass.
  Defaults to `claude-sonnet-4-6`. Override per tenant if you want a cheaper
  / heavier model.
- `ANTHROPIC_API_KEY` — **must** be reachable from the mcp-server container.
  Synthesis hits the Anthropic API directly. Missing key = every learn job
  hard-fails at synthesis with a credential error.

## 4. What ships automatically vs. what needs operator action

| Item                                                     | Status                          |
| -------------------------------------------------------- | ------------------------------- |
| `yt-dlp` in mcp-server image (T0.1)                      | ✅ Automatic                    |
| `ffmpeg` in mcp-server image (T0.1)                      | ✅ Automatic                    |
| Bundled meta-skill `_bundled/luna_learn_from_media/skill.md` | ✅ Automatic                |
| Luna's `tool_groups` DB row includes `learning`          | ⚠️ Needs migration 156 applied  |
| Code Reviewer agent UUID resolvable in tenant            | ⚠️ Manual until T2.4-followup    |
| KG diffusion endpoint                                    | ⚠️ Soft-fail until T2.7-followup |
| `ANTHROPIC_API_KEY` available to mcp-server              | ⚠️ Operator must wire           |

## 5. Known limitations (Phase 1 MVP)

These are accepted limits of the MVP. Each has a tracking item; do not file
new bugs.

- **Public videos only.** No authenticated download paths in Phase 1. A
  social-network auth path was discussed and deferred to Phase 2 per the
  spec §8 decision log.
- **Code Reviewer dispatch broken on live wire.** Tracked as BLOCKER2 from
  the final review and fixed in a follow-up. Until that follow-up ships,
  every learn job routes to the cache+notify path with a
  "review unavailable" message and the synthesised skill sits in the cache
  for inspection.
- **`--from-attachment` will crash the workflow.** `act_probe_attachment` is
  not yet implemented (BLOCKER1 from the final review). Do not advertise
  the `--from-attachment` flag to tenants until that ships.
- **KG diffusion soft-fails.** The `/api/v1/knowledge/observations` internal
  endpoint is not yet shipped (T2.7-followup). The skill still installs
  cleanly, but semantic recall on the new capability will lag until the
  next nightly KG rebuild or the endpoint lands.

## 6. User-facing surfaces

Two ways a tenant user can trigger a learn job:

- **WhatsApp.** Send Luna a YouTube or Instagram reel URL. She acks,
  processes, and notifies on completion.
- **CLI.** From an authenticated `alpha` session:

  ```
  alpha learn <url> [--from-attachment FILE] [--dry-run] [--resume <job_id>] [--resume-last]
  ```

  - `--dry-run` — synthesise but do not install. Useful for inspecting the
    draft skill.
  - `--from-attachment FILE` — currently **broken**, see §5.
  - `--resume <job_id>` — pick up a specific quarantined job.
  - `--resume-last` — pick up the most recent failed job for the calling
    tenant.

## 7. Recovery commands

When the reviewer is unavailable, the KG endpoint is down, or any other
transient failure quarantines a job:

- `alpha learn --resume-last` — retry the most recent failed job. Use this
  once the underlying dependency comes back.
- **Quarantine inspection.** Failed jobs land in
  `_tenant/<uuid>/_learning_quarantine/` with a 30-day TTL. Inspect the
  draft `SKILL.md` and the failure reason before retrying.
- **Cache inspection.** Cache hits + cache-only fallbacks live in
  `_tenant/<uuid>/_learning_cache/` with a 7-day TTL.

## 8. Audit trail

Every successful install leaves a durable trail an auditor can follow:

- A row in `library_revisions` — same audit substrate as every other skill
  install in the tenant.
- Provenance frontmatter on the installed `SKILL.md`:
  - `source_url` — the original media URL
  - `synthesis_date` — UTC timestamp of the synthesis pass
  - `reviewer_agent_id` — UUID of the Code Reviewer that approved it
  - `transcript_sha256` — content hash of the transcript that fed synthesis
  - `learned_by_agent_id` — Luna's agent UUID at the time of install
- One KG observation per installed skill, scoped to the tenant (once
  T2.7-followup ships).

If any of these are missing on a skill that claims to be Luna-learned, treat
it as suspect and quarantine for inspection.
