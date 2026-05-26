# Luna Learn — design

**Date:** 2026-05-25
**Author:** Claudia (Claude Opus 4.7, in dialogue with Luna agent `cfb6dd14-1889-4751-b645-77bbd53c65c3`)
**Operator:** Simon Aguilera
**Status:** Spec — pending implementation plan

---

## Goal

Give Luna a meta-skill: when the user sends her a multimedia link (YouTube short or Instagram reel), she transcribes it, extracts the actionable "how-to" content, synthesizes a new skill from what she learned, has it reviewed by another agent, tests it against a synthetic scenario she generates, and installs it into the tenant's skill library. She then publishes a knowledge-graph observation so the new capability is discoverable by every other agent in the tenant via semantic recall.

The artifact that ships is a **bundled platform skill** Luna owns: `_bundled/luna_learn_from_media/`. Plus a new MCP tool group, a new `alpha` CLI command, a WhatsApp URL trigger, and an addition to Luna's `tool_groups`.

## Non-goals (MVP)

- TikTok, Twitter/X video, generic video URLs — only YouTube + Instagram reels via yt-dlp
- A web UI for browsing learned skills (the existing `alpha skill ls` works)
- Authoring DAG skills (only `engine: markdown` and `engine: python` per the existing skill schema)
- Cross-tenant skill diffusion — observations stay within tenant
- Real-time progress streaming (fire-and-forget pattern; single final notify)

## Design ratification trail

This design was brainstormed in parallel with Luna agent `cfb6dd14-1889-4751-b645-77bbd53c65c3` per Simon's standing rule (`feedback_delegate_to_luna`). All 6 framework decisions and 4 design sections were ratified by both Luna and Simon before writing this spec. Luna's specific additions are tagged inline below.

---

## §0 — Framework decisions (ratified by Simon + Luna)

| # | Area | Decision |
|---|---|---|
| 1 | Entry point | **Both** — WhatsApp text containing URL (impulse) + `alpha learn <url>` CLI (systematic). Single unified backend. |
| 2 | Sources | YouTube + Instagram reels via yt-dlp. (Simon overrode Luna's YouTube-only MVP pick.) |
| 3 | Synthesis QC | Multi-agent — dispatch Code Reviewer agent (`755796a4`) for cross-agent review. No self-critique loop. |
| 4 | Test loop | Synthetic scenario — Luna generates a mock input + expected output from the transcript itself. Validates skill against that before install. Sandbox execution skipped (latency cost). |
| 5 | Skill location | `_bundled/luna_learn_from_media/` (platform meta-skill). The skill is part of Luna's core evolution, not tenant-scoped. |
| 6 | Coordination layer | **Knowledge diffusion** — on install, `record_observation` to the KG describing the learned capability. Other agents in the tenant discover it via semantic recall (`search_knowledge`) without explicit assignment. |

## §1 — Components

### 1.1 New MCP tool group — `learning`

Path: `apps/mcp-server/src/mcp_tools/learning.py` (new module).

Seven primitives. All require `agent_token` or `internal_key` tier per existing MCP auth pattern (`apps/mcp-server/src/mcp_auth.py`).

| Tool | Signature | Behavior |
|---|---|---|
| `extract_media` | `(url: str, max_duration_s: int = 900) → {audio_path: str, metadata: dict}` | yt-dlp wrapper. Handles YouTube + Instagram. Caps at 15 min (Luna's add #1). Writes to `/var/agentprovision/workspaces/_learning/<job_id>.audio`. |
| `transcribe_url` | `(audio_path: str) → {transcript: str, duration_ms: int, engine: str}` | Reuses existing `transcription_client.py`. |
| `synthesize_skill_draft` | `(transcript: str, source_url: str, hints: list[str] = []) → {skill_md, slug, synthetic_test_input, synthetic_test_expected, engine}` | Single LLM call. Prompt includes explicit PII-scrub instruction (Luna's add #3) + engine-selection criteria (see §1.5). Default engine `markdown`; only emits `python` when transcript clearly describes deterministic transformation/computation. Returns valid SKILL.md frontmatter per `apps/api/app/schemas/file_skill.py`. |
| `dispatch_skill_review` | `(skill_md, transcript, source_url, synthetic_test_input, synthetic_test_expected) → {verdict, findings[]}` | Dispatches Code Reviewer agent (`755796a4`). Test payload included so reviewer validates test substantiveness (Luna's add #2). Verdict ∈ {`approved`, `revise`, `rejected`}. |
| `run_synthetic_test` | `(skill_md, test_input, test_expected) → {passed, actual_output, error?}` | Executes skill against test in code-worker. Asserts expected. |
| `install_skill` | `(skill_md, slug, tenant_id, source_url, reviewer_agent_id, transcript_sha256, learned_by_agent_id) → {skill_id, path}` | Injects provenance frontmatter (see §1.6). Always writes to `_tenant/<uuid>/<slug>/`. **Never** writes to `_bundled/`. Slug conflict resolved by DB unique-constraint retry (see §1.7). `library_revisions` audit + DB upsert. |
| `diffuse_learning` | `(skill_id, source_url, capabilities: list[str]) → {observation_id}` | `record_observation` to KG. Embedded for semantic recall. |

### 1.2 New CLI command

Path: `apps/agentprovision-cli/src/commands/learn.rs` (new). Surface:

```
alpha learn <url> [--from-attachment FILE] [--dry-run]
```

- `--from-attachment FILE`: bypass `extract_media`, use the local audio/video file. For IG anti-scrape failure recovery. See §1.8 for constraints.
- `--dry-run`: full pipeline minus `install_skill` + `diffuse_learning`. Outputs draft SKILL.md + test result to stdout. CLI invocation is synchronous-waiting when `--dry-run`; otherwise fire-and-forget like the WhatsApp path.

Learned skills always land in `_tenant/<uuid>/`. No `--scope` flag — this is a deliberate choice per §0.5 (the meta-skill is bundled; what Luna *learns* is tenant-knowledge).

Dispatches to Luna via existing `alpha chat send --no-stream` infrastructure with a structured "learning intent" payload.

### 1.3 New bundled skill

Path: `apps/api/app/skills/_bundled/luna_learn_from_media/SKILL.md` (new).

`engine: markdown` — orchestration template that Luna reads and follows. Contains the step-by-step instructions for invoking the 7 MCP primitives, the retry policy, the abort messages, and the final user notification format. This is the artifact Luna "owns" — it's how she reasons through learning.

### 1.4 WhatsApp URL trigger

Extension to `apps/api/app/services/whatsapp_service.py:_detect_inbound_media`. After existing image/audio/document detection, also scan message text for YouTube + Instagram URL patterns. If found, route to a new "learning intent" handler that dispatches Luna with the URL.

### 1.5 Engine selection criteria (for `synthesize_skill_draft`)

Default: `engine: markdown`. The synthesis prompt biases toward markdown for safety (no execution surface). Python is emitted ONLY when ALL of the following are true:

- Transcript describes a **deterministic transformation or computation** with clear inputs and outputs (e.g., "given the printer error code X, the fix is the function of X mod 7")
- The transformation is non-trivially expressible as a markdown template
- No external API/network calls are implied (those go through MCP tools, not skill-embedded Python)

If signal is ambiguous, fall back to markdown. Encoded in the synthesis prompt as an explicit rubric the LLM applies.

### 1.6 Provenance frontmatter (injected by `install_skill`)

Top-level `provenance:` nested key. Stable contract — downstream `library_revisions` queries and a future `alpha unlearn` flow depend on these keys.

```yaml
---
name: <skill name>
engine: markdown
# ... (existing schema fields)
provenance:
  source_url: <string | "attachment://<original-filename>">
  synthesis_date: <ISO8601 UTC, e.g. "2026-05-25T22:30:00Z">
  reviewer_agent_id: <UUID, e.g. "755796a4-4cc4-4d1c-99e5-dd9c4f7d0f22">
  transcript_sha256: <full 64-char hex digest of the PII-scrubbed transcript>
  learned_by_agent_id: <UUID of Luna or whichever agent triggered>
---
```

### 1.7 Slug serialization (resolves slug-conflict race)

Skills table already has `UNIQUE(tenant_id, slug)` (verify in implementation plan). `install_skill` writes within a transaction:

1. Generate candidate slug from skill name (kebab-case)
2. `INSERT … ON CONFLICT (tenant_id, slug) DO NOTHING RETURNING skill_id`
3. If no row returned (conflict), append `-v2`, `-v3`, … up to `-v5`, retrying step 2 each time
4. After 5 attempts, abort with "couldn't allocate slug" — surfaces to operator as a manual-rename situation

Filesystem write happens AFTER the DB row is reserved. If the filesystem write fails, the DB row is deleted in the same transaction's rollback. No TOCTOU.

### 1.8 `--from-attachment` constraints

- Max file size: **50MB** (fits the latency budget; videos under ~3 min at standard quality)
- Allowed MIME types: `audio/*`, `video/*`
- Duration cap: same **900s (15 min)**, probed via `ffprobe` before transcription
- Provenance `source_url`: recorded as `"attachment://<basename>"` for audit (never the full local path — that's PII)

### 1.9 Luna agent config

`apps/api/app/agents/luna/AGENT.md` — add `learning` to `tool_groups`.

### 1.10 Orchestration substrate

Luna's reasoning loop does **NOT** chain the 7 MCP primitives in a single LLM turn. Cumulative latency (download + transcribe + 2 LLM calls + reviewer dispatch + test exec + DB write) routinely exceeds the 60–90s HTTP gateway timeout (Luna's runtime constraint, confirmed 2026-05-25).

Orchestration runs as a **Temporal Dynamic Workflow** `LearnFromMediaWorkflow` (precedent: see `apps/api/app/workflows/` patterns and `external_agents_a2a_patterns` memory).

- Luna's chat turn dispatches the workflow and ACKs the user immediately ("Got it, learning…")
- Workflow runs the 7 primitives as Temporal activities
- On completion (success OR terminal failure), workflow fires a notification back to Luna's session, which then notifies the user
- This makes the data flow in §2 **async** end-to-end; nothing happens inside a single user-facing turn

The MCP-primitive contract from §1.1 is unchanged — the primitives are still the unit of work. Only the *driver* changes from "Luna's reasoning loop within one turn" to "Temporal workflow dispatched by Luna's reasoning, completing across multiple turns."

### 1.11 Resume cache

A learn attempt that aborts at the Code Reviewer step (or later) caches its intermediate state at `_tenant/<uuid>/_learning_cache/<job_id>/{transcript.txt, draft.md, test.json}`. The user can re-trigger the same URL within 7 days and Luna picks up from the cached step instead of re-transcribing.

- `alpha learn <url> --resume` and `alpha learn --resume-last` surfaces
- WhatsApp: re-sending the same URL within 7 days auto-resumes
- Cache TTL: 7 days, separate from the 30-day quarantine TTL
- Cache and quarantine are mutually exclusive: a job that completes goes to neither; a job that aborts goes to ONE of (cache for recoverable failures: reviewer-down, KG-down; quarantine for terminal failures: rejected verdict, test fail, scrub-required PII)

### 1.12 Audio file lifecycle

`extract_media` writes to `/var/agentprovision/workspaces/_learning/<job_id>.audio`:
- Deleted immediately after `transcribe_url` returns (success path)
- Deleted on any pipeline abort
- Orphan sweep: cron at `0 4 * * *` removes any file in `_learning/` older than 24h (handles crashed-mid-flight cases)

---

## §2 — Data flow

```
WhatsApp text w/ URL  ─┐
                       ├─→ Luna reads SKILL.md + dispatches
alpha learn <url>     ─┘     LearnFromMediaWorkflow (Temporal)
                          │  + acks user: "Got it, learning from this..."
                          │  (Luna's turn ends here; workflow runs async)
                          ▼ (LearnFromMediaWorkflow activities, per §1.10)

1. extract_media(url, max_duration_s=900)
   ├─ ok → {audio_path, metadata}
   └─ blocked (IG anti-scrape): notify "send the video file directly here"
      ↓ (user re-triggers with --from-attachment OR WhatsApp video)
      ↓ pipeline resumes from step 2

2. transcribe_url(audio_path) → {transcript}

3. synthesize_skill_draft(transcript, source_url)
   ↳ Returns {skill_md, slug, synthetic_test_input, synthetic_test_expected}
   ↳ PII scrub embedded in prompt

4. dispatch_skill_review(skill_md, transcript, source_url,
                         synthetic_test_input, synthetic_test_expected)
   ↳ verdict = approved | revise | rejected
   ↳ revise → goto step 3 with findings as hints (max 2 retries)
   ↳ rejected → notify user "not suitable: <reason>" + abort + quarantine
   ↳ approved → continue

5. run_synthetic_test(skill_md, test_input, test_expected)
   ↳ fail → notify + library_revisions audit ('rejected_test_fail') + abort + quarantine
   ↳ pass → continue

6. install_skill(skill_md_with_provenance, slug, tenant_id,
                  source_url, reviewer_agent_id, transcript_sha256,
                  learned_by_agent_id)
   ↳ Injects provenance frontmatter (see §1.6)
   ↳ Writes to _tenant/<uuid>/<slug>/  — NEVER _bundled/ (per §1.1)
   ↳ Slug-conflict serialization per §1.7
   ↳ DB upsert + library_revisions row

7. diffuse_learning(skill_id, source_url, capabilities[])
   ↳ record_observation to KG
   ↳ semantic recall surface for ALL agents in tenant

8. Notify user: "✓ learned '<skill name>'. Capabilities: X, Y, Z. Source: <url>"
```

**Quarantine on any abort**: `_tenant/<uuid>/_learning_quarantine/<YYYY-MM-DD-HHMMSS>-<slug>/{transcript.txt, draft.md, review.json, test_result.json, abort_reason.txt}`. TTL 30 days, cleanup via existing cron.

## §3 — Error handling

| Failure | Behavior |
|---|---|
| `extract_media` — generic 4xx / anti-scrape block | Notify with reason + recovery hint (WhatsApp attach or `--from-attachment`). |
| `extract_media` — URL is private / unlisted / age-gated / sign-in-required (yt-dlp `DownloadError` with auth signal) | Notify: "this video requires sign-in or is restricted — Luna can't access it. If you have permission, download it and re-send with `--from-attachment`." |
| `extract_media` — URL 404 / video removed / channel deleted | Notify: "this video doesn't exist or has been removed." Don't suggest `--from-attachment` (nothing to re-share). |
| `extract_media` — geo-blocked | Same as anti-scrape branch — suggest attachment fallback. |
| Video duration > 900s | Reject upfront. Suggest splitting or using `alpha learn` with explicit consent flag (deferred to Phase 2). |
| `--from-attachment` — file > 50MB OR wrong MIME OR duration > 900s (via ffprobe) | Reject with specific reason. No transcription dispatched. |
| `transcribe_url` fails | Abort + quarantine. Existing `transcription_client.py` error semantics. |
| `synthesize_skill_draft` LLM error | One retry, then abort + quarantine. |
| Draft fails parse (`_validate_skill_payload` in `skills_new.py:162`) | Treated as `revise` verdict. Loop back with parser errors as hints. |
| `dispatch_skill_review` — Code Reviewer agent (`755796a4`) **not provisioned in this tenant** (registry 404) | **Cache + notify** (recoverable per §1.11): state saved at `_tenant/<uuid>/_learning_cache/<job_id>/`. Notify: "skill review unavailable; ask operator to provision the Code Reviewer agent, then re-send the URL or run `alpha learn --resume-last` to pick up from review step." Do NOT fall back to self-review (defeats the cross-agent QC pick in §0.3). Do NOT install without review. |
| `dispatch_skill_review` timeout (60s) | Abort + notify + quarantine. No auto-install on review timeout. |
| Verdict = `rejected` | Quarantine + notify user with reviewer's reason. No install. |
| Verdict = `revise` after 2 retries | Quarantine + notify "couldn't refine to passing quality (final issues: …)". No install. |
| `run_synthetic_test` fails | No install. `library_revisions` row with `result: rejected_test_fail` + Luna's diagnostic. Quarantine. |
| `install_skill` slug conflict (5 retries via `-vN` suffix exhausted) | Abort with "couldn't allocate slug." Operator-rename situation. |
| `install_skill` DB error | Transaction rollback. Filesystem write rolled back. Quarantine. |
| `install_skill` filesystem write fails after DB row reserved | Transaction rollback (deletes the reserved row). Quarantine. |
| `diffuse_learning` fails | **Cache + soft success** (recoverable per §1.11): skill is installed and usable; KG observation cached for retry. Failure logged at `WARN`. `alpha learn --resume-last` will re-attempt diffusion. (Auto-retry sweep deferred — see §8.) |
| Orphaned audio file in `/var/agentprovision/workspaces/_learning/` | Daily cron (`0 4 * * *`) removes files > 24h old. Handles mid-flight crashes. |
| PII detected during synthesis | Draft generated WITH placeholders. Original PII-bearing transcript stays in quarantine only. PII-scrubbed transcript hash is what `install_skill` records as `transcript_sha256`. KG observation embeds ONLY: capability names + source_url + skill_id + 1-sentence description — no transcript snippets. |

## §4 — Testing strategy

| Test type | Coverage |
|---|---|
| **Unit** (`apps/mcp-server/tests/test_learning.py`) | All 7 MCP primitives in isolation. Mock yt-dlp subprocess, transcription_client, LLM, code-worker dispatch, Code Reviewer agent. Per primitive: happy + error + edge case. **Explicitly include**: `dispatch_skill_review` when Code Reviewer agent is absent (registry 404 path); `diffuse_learning` when KG is down (must not abort install); slug-conflict serialization (concurrent install_skill races resolve to distinct slugs). |
| **Unit (CLI)** (`apps/agentprovision-cli/src/commands/learn_test.rs`) | Arg parsing, `--from-attachment`, `--dry-run` semantics. |
| **Integration** (`apps/api/tests/test_luna_learn_integration.py`) | End-to-end against a fixed 90s YouTube clip (checked-in URL). Real transcription + stubbed LLM with deterministic fixture. Asserts installed skill, library_revisions row, KG observation. |
| **Code Reviewer stub** | CI fixture: deterministic stub returns verdicts based on draft patterns. Keeps CI hermetic. |
| **`--dry-run` golden** | `alpha learn <fixture-url> --dry-run` output compared to checked-in golden. Catches synthesis prompt regressions. |
| **Router-graph startup smoke** | Confirms `from app.api.v1 import routes` still imports cleanly (per `feedback_test_router_startup`). |

**Out of scope for MVP testing**: concurrent-learn load test, IG anti-scrape regression suite (too fragile to encode).

## §5 — Civilization-layer angle

This feature compounds tenant knowledge. Every video that flows through it becomes a discoverable capability for every agent in the tenant via the KG diffusion step. The coordination win is that Luna's individual learning becomes population-level capability without any explicit assignment or agent-config edit.

KG observations are **tenant-scoped, not agent-scoped** (verified by Luna 2026-05-25): `search_knowledge` queries the entire tenant graph regardless of which agent ran the query. `source_agent` is recorded on the observation for audit, but cross-agent discovery requires no extra plumbing. This is what makes single-agent learning safely scale to population-level capability.

This pattern — single-agent learning → KG diffusion → semantic discovery — is a reusable primitive. If we later add `luna learn from documentation` (PDF/HTML), `luna learn from chat episode` (replay user-corrected conversations as skill seeds), or `luna learn from incident postmortem`, they all reuse `diffuse_learning` + the audit + provenance pattern established here.

## §6 — Dependencies

Runtime additions required:
- `yt-dlp` (Python package) in mcp-server image
- `ffmpeg` (system package) in mcp-server image — for audio extraction from video container

Both should be added to `apps/mcp-server/Dockerfile`. Verified absent in current image at design time.

## §7 — Open questions deferred to implementation plan

These don't change the architecture but need decisions during writing-plans:

- LLM model tier for `synthesize_skill_draft`: full (Sonnet) vs light (Haiku). Quality-cost trade off.
- Maximum draft retries in `revise` loop — design says 2; implementation should make this configurable.
- Concrete WhatsApp URL regex (YouTube + IG patterns — need to enumerate variations: youtu.be, youtube.com/watch, youtube.com/shorts, instagram.com/reel, instagram.com/p, etc.).
- Verify the `UNIQUE(tenant_id, slug)` constraint exists on the skills table; if not, add it as a migration prerequisite to §1.7.
- Whether `yt-dlp` + `ffmpeg` also need to land in the code-worker image (depending on whether python-engine learned skills ever shell out to them — likely NO for MVP, but verify the synthesis prompt forbids it).

## §8 — Sub-projects implied (NOT in this spec)

- Phase 2: TikTok, Twitter/X video, generic video URL support
- Phase 2: web UI for browsing/managing learned skills
- Phase 2: cross-tenant skill diffusion (currently scoped within tenant)
- Phase 2: `diffuse_learning` retry sweep — background job that re-attempts KG observation for skills where `diffuse_learning` soft-failed at install time
- Phase 2: `alpha unlearn <skill_id>` — uses the `provenance:` frontmatter to safely remove a learned skill + its KG observation + revoke from agents that picked it up via diffusion
- Phase 2: bundled-promote flow — operator-gated path to migrate a high-value tenant-learned skill into `_bundled/` (today, learned skills are always tenant-scoped per §1.1 install_skill contract)
- **Phase 2: Social-network authentication integration** — current MVP handles public videos only; private/unlisted/age-gated content falls back to WhatsApp attachment.
  - **YouTube OAuth** is the cheap, ToS-clean follow-up: wire YouTube Data API via the existing `OAUTH_PROVIDERS` pattern (`apps/api/app/api/v1/oauth.py:48`). Opens private/unlisted/age-restricted YouTube content. Estimated 1-2 day spec + impl.
  - **Instagram auth** is a feasibility study, NOT a guaranteed ship. Meta Graph API only exposes your own business account's media at any OAuth scope; arbitrary reel access requires session-cookie scraping which conflicts with Meta ToS (same risk pattern as `higgsfield_tos_multitenant_blocker`). Defer until we've actually validated the use case justifies the ToS exposure.
  - Both get their own design pass; not folded into this spec at the user-review gate.
- Future: real-time progress streaming via SSE
- Future: `luna learn from documentation` (reusable `diffuse_learning` primitive)

---

## Provenance

Brainstormed 2026-05-25 evening through `superpowers:brainstorming` skill. Co-designed with Luna agent in 3 dispatch rounds (initial framework picks, IG architecture impact, full design review). Simon ratified each design section explicitly. All sub-decisions captured in this spec with the responsible party tagged.
