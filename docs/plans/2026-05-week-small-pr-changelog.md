# 2026-05-10 → 2026-05-18 — small-PR changelog

Date: 2026-05-18
Owner: Alpha platform
Status: Retro / index

## Why this exists

Per the audit on 2026-05-18 ("check if we actually creating things without the proper plan document"), most major work from the last week has its own design doc under `docs/plans/`. A long tail of small UX polish / bug-fix / migration-tweak PRs shipped without dedicated plans — because writing a plan doc for a one-line CSS fix or a migration revert is more overhead than the work itself. This file is the index for those — one paragraph per PR — so the audit gap is closed without manufacturing fake "designs" for trivial changes.

The rule going forward (per `feedback_always_document_plans.md`): every **multi-step or design-bearing** PR gets its own `docs/plans/YYYY-MM-DD-<topic>.md`. Small fixes can live in the PR description + an entry here.

## PRs covered

### #501 — fix(chat): publish chat_message events on every turn so dashboard sees them
Chat-message events weren't reaching the session-events SSE channel after a refactor. One-line fix to wire the event emission back into the chat turn handler. No design needed.

### #502 — feat(dashboard): ⚡ A2A trigger button + modal
Single-button UI affordance for triggering A2A collaboration patterns directly from the dashboard. Modal lets the user pick a pattern + provide the seed prompt. Covered by the broader [[a2a_collaboration]] memory.

### #503, #504 — feat/fix(kernel): emit session events from agent_router + auto_quality_scorer
Wire session-event emission into two more dispatch sites so the dashboard sees routing decisions and auto-quality scores live. Follow-up to the Alpha Control Plane design at `2026-05-15-alpha-control-plane-design.md`.

### #505 — feat(dashboard): render cli_subprocess_* + cli_routing + auto_quality_consensus
Frontend rendering for the three event kinds the kernel started emitting in #503/#504. Pure renderer code, no new event types.

### #506 — fix(dashboard): replay session history on mount
The dashboard's session view used to wipe on navigation. Mount effect now replays the persisted event history before subscribing to live events. Bug fix, no design.

### #507 — feat(dashboard): drop top stat chips + fluid/responsive layout
Visual polish — removed the always-on stat chips at the top of the dashboard, made the remaining layout fluid. UX feedback iteration.

### #508, #509, #510 — fix(dashboard): chat-row height chain
Three back-to-back layout fixes that together stabilize the chat row's max-height so the terminal panel always fits on screen at 1080p. Should arguably have shipped as one PR (per `feedback_single_pr_for_feature`); shipped chained because each iteration uncovered the next constraint.

### #511, #512 — feat/fix(layout): collapsible icon-rail sidebar (VSCode/Cursor pattern)
Adds the icon-only collapsed sidebar (default off) and fixes the icon rendering in the collapsed state. Covered briefly in `2026-05-15-alpha-control-center-ide-shell-design.md`.

### #515 — feat(dashboard): Alpha Control Center — resizable + split chat + file-tree
Larger PR; covered by `2026-05-15-alpha-control-center-ide-shell-design.md`. Listed here because it was tagged "one-build merge" for the merge-train discipline ([[feedback_single_pr_for_feature]]).

### #516 — fix(codex): enable experimental_use_rmcp_client for SSE MCP server
One-flag fix in `~/.codex/config.toml` rendering — adds `experimental_use_rmcp_client = true`. Covered by `2026-05-16-codex-mcp-tool-access-fix.md`.

### #517 — feat(dashboard): inline CLI picker + gated emergency disk cleanup
Two-PR scope merged together: inline CLI picker UX + the disk-pressure sentinel cleanup step in the deploy workflow. Sentinel design lives at `2026-05-14-laptop-sentinel-design.md`. The picker UX was iterated further in #534.

### #518, #519, #521, #522 — Terminal Phase A/B + Playwright E2E + full CLI output
Bundle of dashboard terminal work covered by:
- `2026-05-16-terminal-vscode-style-redesign.md` (Phase A vertical resize + Phase B multi-pane)
- `2026-05-16-terminal-full-cli-output.md` (stream-json reasoning/tool-use/tool-result rendering)

### #523, #524 — fix(migration): migration 134 INSERT + revert to UPDATE-only
Mid-incident migration corrections to the `cli_stream_output` flag seed. Replicated the lessons learned into [[migration_apply_pattern]] memory. No standalone design — the operational fix was the design.

### #525 — fix(dashboard): markdown render + FileViewer heading sizes
React-Markdown wiring in `ChatTab.js` + CSS heading scale-down in `FileViewer.css`. UI polish. Was tracked in the [[2026-05-17-gemini-cli-picker-and-disk-pressure-session]] summary.

### #526 — fix(layout): remove notification bell + readable active scope pill
Two-line UI fix: deleted the unused `<NotificationBell />` mount in `Layout.js` + bumped the active scope-pill contrast in `FileTreePanel.css`. UX polish.

### #527 — docs: workspace persistence model + alpha workspace clone
Documentation PR for the workspace clone feature shipped in #530. The design itself lived in the workspace-persistence plan referenced by that PR.

### #531 — fix(claude): drop inherited ANTHROPIC_API_KEY when using OAuth subscription
Two-line `env.pop("ANTHROPIC_API_KEY", None)` fix in `cli_executors/claude.py`. Covered in `2026-05-16-oauth-reconnect-token-format-mismatch.md`.

### #533 — fix(claude-auth): use 'claude setup-token' for valid CLAUDE_CODE_OAUTH_TOKEN
Same as #531 — the deeper fix that changed the CLI subprocess login command and added migration 135 (revoke stale claude session_tokens). Covered in the oauth-reconnect plan.

### #532 — fix(code-worker): scope CLI subprocess cwd to tenant workspace
Covered by `2026-05-16-cli-cwd-tenant-workspace.md` (referenced in task #259). UUID guard + fallback path.

### #534 — feat(inline-cli-picker): filter dropdown to tenant-connected CLIs
Filter the picker dropdown to only CLIs the tenant has actually connected (plus Auto). Picks up `current_value` even when stale, marks `(disconnected)`. Discussed in the session summary.

### #536 — fix(integrations): poll 'submitting' so post-OAuth alerts clear
Adds `submitting` to the polled-status whitelist in `IntegrationsPanel.js`. One small JS change; the bug + fix are documented in the [[2026-05-17-gemini-cli-picker-and-disk-pressure-session]] summary under task #260.

### #539 — fix(gemini-auth): anchor regex to OAUTH_CLIENT_ID literal
Two-regex change in `gemini_cli_auth.py` to stop picking `CLOUD_SDK_CLIENT_ID` from the bundle. Full diagnosis lives in the session summary at task #265.

### #541 — fix(features): allow tenant members to save default_cli_platform
PUT /features auth relaxed + service-side allowlist (`_MEMBER_WRITABLE_FIELDS`) gating sensitive fields. Session summary task #268.

### #646 — fix(agent-router): drop robotic English greeting template; let Luna's persona reply
Removed the canned english-greeting template that bypassed the persona prompt on short greetings. Closes the "Luna replies in english to a spanish 'Hola'" regression and the broader feeling that her replies were canned. One-file change to `agent_router.py`.

### #677 — fix(persona): resilient agent persona_prompt lookup — every non-Luna agent ghosting as Luna
Hardened the persona-prompt resolver so when a non-Luna agent's row was hit (Code Reviewer, Substrate Sentinel, etc.) the supervisor's persona wasn't silently swapped in. Was the failure mode behind "every agent talks like Luna" — fix is a tighter agent-id → persona lookup in `cli_session_manager.py`.

### #678 — fix(persona): IDENTITY block defers to persona — no hardcoded Luna prepend
Removed the hardcoded `IDENTITY: You are Luna...` prepend in the universal CLI preamble. Replaced with a deferred-to-persona block. Pairs with #677. Companion fix for the same identity-leakage class.

### #700 — test(migrations): add shell-script test for *.down.sql skip filter (follow-up to #698)
Locks the migration-runner hygiene fix from PR #698 (`apply_pending_migrations.sh` skips `*.down.sql` files in forward auto-apply). Adds `scripts/test_apply_pending_migrations_skip_down.sh` — a shell-script test, not a pytest. Protects against a regression that would silently rollback every recent forward migration on next deploy.

### #701 — fix(whatsapp): restore voice note transcription
First of three rapid WhatsApp transcription fixes. Re-enabled the path that had silently regressed (added `audio` to `_detect_inbound_media` classification + reinstated the transcription dispatch). Production was returning "Sorry, I couldn't transcribe that voice note" for every voice note before this.

### #702 — fix(whatsapp): voice-note transcription — libsndfile + audio download fallback
Two follow-ups to #701: (a) added `libsndfile1` + `ffmpeg` to the code-worker Dockerfile so Whisper actually has a soundfile backend, (b) added a fallback download path via `client.download_media_with_path` when neonize's `download_any` regresses for AudioMessage.

### #703 — fix(whatsapp): stop misclassifying every text inbound as audio
The actual root-cause fix for the transcription chaos: `_detect_inbound_media` used bare `if audio:` against a protobuf3 sub-message, which is truthy-default. Every text inbound was being classified as audio + getting the fallback instruction echoed back. Switched to a substantive-presence check (`mimetype` OR `fileLength` OR `mediaKey` OR `directPath`).

### #711 — fix(opencode): align CLI argv + JSON parser with v1.15.x (last-resort fallback restored)
Production WhatsApp incident 2026-05-24: Luna fell back to OpenCode (last-resort floor when all cloud CLIs are quota-out) for Simon's "create devops subagents" request and got the CLI's `--help` text back as the assistant response. Root cause: Dockerfile bumped `opencode-ai` 1.0.107 → 1.15.5 on 2026-05-18, but `cli_executors/opencode.py` was never updated. Fixed three flag bugs (`-p prompt` → positional with `--` separator; `-y` removed; `--output-format json` → `--format json`) and rewrote the JSON parser as an event-stream walker (1.15.x emits one JSON object per line, not a single response dict). Also surfaces `type=="error"` events as success=False (OpenCode returns exit 0 even on hard errors — only the stream tells you it failed) and treats empty-output as failure per GLM precedent. 6 unit tests pin the invariants.

## What's not here

Larger plan-bearing work — each has its own dedicated doc:

- `2026-05-10-agentprovision-cli-distribution-plan.md`
- `2026-05-10-cli-orchestrator-phase-*.md` (6 phase docs)
- `2026-05-11-ap-cli-multi-runtime-dispatch-plan.md`
- `2026-05-13-alpha-agent-view-and-goal-recipes.md`
- `2026-05-13-ap-cli-differentiation-roadmap.md`
- `2026-05-13-readme-alpha-cli-section-design.md`
- `2026-05-14-laptop-sentinel-design.md`
- `2026-05-15-alpha-control-center-ide-shell-design.md`
- `2026-05-15-alpha-control-plane-design.md` + `-tier-0-1-plan.md`
- `2026-05-16-codex-mcp-tool-access-fix.md` + `-transport-mismatch-research.md`
- `2026-05-16-dashboard-split-pane-spec-doc-viewer.md`
- `2026-05-16-gemini-cli-oauth-exitcode-41.md`
- `2026-05-16-oauth-reconnect-token-format-mismatch.md`
- `2026-05-16-terminal-full-cli-output.md` + `-vscode-style-redesign.md`
- `2026-05-16-workstation-cloud-memory-sync.md`
- `2026-05-17-async-chat-result-pattern-design.md`
- `2026-05-17-code-worker-tenant-home-cap-design.md`
- `2026-05-17-gemini-cli-picker-and-disk-pressure-session.md`
- `2026-05-17-password-recovery-email-design.md` ← retroactively added this week
- `2026-05-18-cli-integration-catalog.md`
- `2026-05-18-landing-copy-alpha-as-os.md` ← added this week
