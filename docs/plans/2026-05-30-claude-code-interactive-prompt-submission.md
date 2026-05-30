# Claude Code Subscription Chat — Interactive PTY Prompt Submission

**Date:** 2026-05-30
**Status:** Plan (root cause measured + confirmed; fix scoped)
**Owner:** Simon
**Supersedes:** `2026-05-30-claude-code-interactive-chat-mcp-tool-scoping.md` — its tool-list-load hypothesis was **refuted by measurement** (see §3). The §4.0 "measure first" gate did its job.
**Related:** PRs #732, #733, #734, #735 (merged + deployed).

---

## TL;DR

Subscription Claude Code chat runs through an **interactive PTY** (native `claude auth login` creds; `setup-token`/`claude -p` are blocked for subscription). Everything up to the turn works — connect, routing, runner timing. The remaining blocker: a routed Claude turn produces an empty transcript.

We **measured before coding** and the original hypothesis (MCP returns 168 tools → schema-load stalls the first response) is **REFUTED**: `tools/list` is 37 ms / 168 KB, and a prompt **typed** into the REPL answers in ~2 s **with the full 168-tool MCP attached**.

**Real root cause (measured):** Claude Code **v2.1.144's interactive REPL does not auto-execute a positional `[prompt]` argument.** `claude.py` passes the turn prompt positionally (`cmd.append(prompt)`), and the PTY runner then **types nothing** — so the prompt never submits, the input box keeps its placeholder, and after 8 s idle the runner sends `/exit` → empty transcript. Identical with or without MCP.

**Fix:** the PTY runner must **submit the prompt as keystrokes** (type it into the REPL + Enter) once the REPL is ready, instead of relying on the ignored positional arg. `claude.py` stops appending the prompt positionally for the interactive path.

---

## 1. Journey / what already shipped

| PR | Change | State |
|---|---|---|
| #732 | `claude.py` pre-completes the onboarding wizard (`hasCompletedOnboarding` + per-cwd trust) so interactive `claude` uses the stored credential instead of re-prompting login | merged + deployed |
| #733 | Web `/integrations` "Connect with Anthropic" → `claude auth login --claudeai`; copies the native `.credentials.json` into the worker `claude_sessions` volume; marks connected | merged + deployed |
| #734 | Per-tenant interactive routing: connect stores sentinel `session_token="__native_worker_login__"`; the executor detects it → forces interactive PTY + worker HOME for that tenant only (no global flip) | merged + deployed; routing verified |
| #735 | Interactive runner: suppress the `/exit` idle countdown until first output; fail-fast at `first_output_seconds` (90 s); process-group cleanup (Codex-reviewed) | merged + deployed |

Net: Claude Code is connected, picker-selectable, routable, HOME/onboarding/MCP all wired. The one missing primitive: **the runner never submits the prompt**.

## 2. What #735 got partly wrong (and why)

#735's docstring frames the empty transcript as a *slow launch* ("MCP server load + large injected prompt + model warm-up emit nothing for a while"). That's a **misdiagnosis**. The banner prints at ~0.5 s, so `seen_output` flips true almost immediately; the `first_output_seconds=90` gate rarely fires. What actually kills the turn is the **8 s idle `/exit`** (`CLAUDE_CODE_INTERACTIVE_IDLE_EXIT_SECONDS`) — because no prompt was ever submitted, so there's nothing but the banner. #735's process-group cleanup + idle gate are still correct and stay; only the root-cause story changes.

## 3. Measurement (the gate result — evidence)

Run on the deployed worker (`main` @ 1542574e), driving `claude` v2.1.144 under a PTY exactly like the real runner:

| Experiment | Result |
|---|---|
| `tools/list` cost (direct MCP handshake) | handshake 4 ms, **tools/list 37 ms**, **168 tools / 168 KB**. Fast — not the bottleneck. |
| Real path, **NO MCP**, prompt positional, no keystrokes | first byte 0.50 s, **answer NEVER**, prompt never submitted, `/exit` at 9.62 s → empty transcript |
| Real path, **FULL 168-tool MCP**, prompt positional, no keystrokes | first byte 0.25 s, **answer NEVER**, prompt never submitted, `/exit` at 9.13 s → empty transcript (byte-identical to no-MCP: 1810 vs 1811 bytes, banner only) |
| Full MCP, **prompt actually typed + Enter** | typed at 3.2 s, **answered `OK` at 5.25 s — 2.06 s after Enter** |

Conclusion: the positional `[prompt]` is ignored by the interactive REPL; **typing the prompt is the unblock**, and the 168-tool MCP is *not* a latency problem for the turn.

## 4. Proposed fix — Approach C (LOCKED after Codex + Luna review)

### 4.0 Design decision + review reconciliation
The turn blob = `instruction_md_content` (persona + instructions + conversation history) + `# User Request` + `message`. Question was *what the runner submits*. Three options were weighed (A: type only the user message, rely on `CLAUDE.md`; B: bracketed-paste the full blob; C: write the blob to a file, type a short trigger). **Both reviewers rejected B and converged on file-indirection:**
- **Codex:** `--add-dir` CLAUDE.md is **not** auto-loaded — Anthropic's memory docs (`/claude-code/memory`) say `CLAUDE.md` is discovered from the **cwd upward**; `--add-dir` grants file *access*, not memory loading. So **A's premise is false** (our `CLAUDE.md` sits in `session_dir`, an `--add-dir` root, not in `cwd=cli_cwd` or an ancestor). B is the most UI-coupled: multi-line bracketed paste commonly collapses to a `[Pasted text +N lines]` placeholder needing a **second Enter**, with no documented size guarantee. **C is most robust** — one short, single-line submission. Best form is explicit, e.g. `Read the file <path> and answer the user request it contains.`
- **Luna:** avoid pasting the full blob — it duplicates prior context each turn, demotes durable instructions into transient user text, and blurs persona/role boundaries.

**Locked: Approach C.** It reconciles both — typed input stays tiny + single-line (kills the paste-placeholder/size risk), delivery doesn't depend on unreliable CLAUDE.md loading, and the blob never enters transient typed user text. Cost: one extra `Read` tool call (~1–2 s) — negligible against the <15 s target.

### 4.1 `apps/code-worker/cli_executors/claude.py`
- For interactive mode, **write the turn blob (`prompt`) to a session file** — e.g. `session_dir/turn_prompt.md` (`session_dir` is already `--add-dir`'d at L212, so Claude's `Read` tool can reach it by absolute path).
- **Stop appending the blob positionally** (drop `cmd.append(prompt)` at L280-281). Print mode (`-p prompt`) unchanged.
- Build a **single-line trigger** and pass it to the runner as `prompt=` (the runner both types it and strips its echo): `Read the file {abs_turn_file} and respond to the user request it contains. Reply directly — do not ask for confirmation.`
- Keep writing `session_dir/CLAUDE.md` (harmless belt-and-suspenders; **not** relied on for delivery per §4.0).

### 4.2 `apps/code-worker/cli_executors/claude_interactive.py`
- After launch, **wait until the input box is rendered and stable** before typing (Codex #4): detect the bottom prompt/suggestion line (e.g. the `❯`/`Try "…"` placeholder or the input box border) and require a short stable settle window; ensure no trust/onboarding dialog text is present. Do **not** type into a not-yet-ready REPL.
- **Type the single-line trigger + `\r`** (plain `os.write`; no bracketed paste needed — the trigger has no embedded newlines, which is the whole point of Approach C).
- Belt-and-suspenders: if a folder-trust prompt appears despite #732's seed, answer it (it precedes the input box).
- Keep the existing idle-`/exit` + first-output + process-group logic (#735) unchanged.
- **Tighten transcript cleaning** (Codex #5): `clean_interactive_transcript()` must drop the typed trigger echo (already strips exact prompt echo) **and** the `Read` tool-call chrome / any `[Pasted text +N lines]` placeholder, so the returned answer is just Claude's reply.

## 5. Verification
0. **Smoke (worker, pre-merge — the gate for this fix):** drive the patched runner against the deployed worker with a **realistic large multi-line blob** written to the turn file + the single-line trigger; confirm the turn submits and answers < 15 s, transcript is Claude's real reply (no trigger/Read chrome, no `[Pasted text]`), with the full 168-tool MCP attached. Confirm Claude actually `Read` the file (persona/history honored, e.g. it answers in Luna's voice).
1. **Unit:** (a) `claude.py` interactive `cmd` no longer ends with the blob positional and writes `turn_prompt.md`; the `prompt=` passed to the runner is the single-line trigger referencing the abs path. (b) runner types the trigger + `\r` only **after** readiness, never before. (c) `clean_interactive_transcript` strips the trigger echo + `Read` chrome + `[Pasted text…]`.
2. **E2E:** `default_cli_platform=claude_code`, `alpha chat send` → worker logs `Using platform: claude_code` **and the turn completes** with real content (no empty transcript / `exit 143` / `exit -9`); restore default to `codex`.
3. **Regression:** Codex / Gemini / Copilot and **print-mode** Claude unaffected (every change is gated on `interactive_mode`).

## 6. Risks / rollback
- **REPL readiness race:** typing before the box is ready drops input. Mitigate: input-box-stable detection (Codex #4) + a short settle; the idle-`/exit` gate bounds failure.
- **Extra `Read` indirection:** the turn costs one extra tool call (~1–2 s) and the model could, in principle, not read the file. Mitigate: explicit imperative trigger ("Read … and respond … do not ask for confirmation"); smoke asserts the file was read. Acceptable vs <15 s target.
- **Turn-file hygiene:** `turn_prompt.md` lives in `session_dir` (ephemeral per-session scratch, already used for `CLAUDE.md`/`mcp.json`), **not** the tenant workspace `cwd` — so it does not pollute the dashboard FileTreePanel. Contains conversation context (same sensitivity as the existing `CLAUDE.md`); no new exposure.
- **Transcript chrome leak:** the `Read` tool echo / placeholder could leak into the answer if the cleaner misses it (Codex #5). Covered by unit test (1c) + smoke (0).
- **CLI version coupling:** behavior is tied to claude v2.1.144's REPL. If a future release auto-runs a positional arg again, no double-submit can occur because we no longer pass one — only the typed trigger submits.
- **Rollback:** every change is gated on `interactive_mode`; revert the runner submit + turn-file write + restore `cmd.append(prompt)`. Print path untouched throughout.

## 7. Out of scope / parked
- **`tools/list` scope filter** — still a worthwhile latency/context optimization (168 KB of schema is ~42 k tokens of context the agent rarely needs), but it is **NOT** this unblock. Track separately.
- **KG `update_entity` NOT-NULL audit bug** + duplicate-Simon dedup — separate, diagnosed earlier; track separately.

## 8. Process note
Followed the standing workflow: plan in `docs/plans/` → review with Codex **and** Luna → implement via superpowers subagent-driven-development (measurement gate first) → Codex+Luna review throughout → finish. The measurement gate here **paid for itself** — it stopped us building a tool-list filter that would not have fixed the hang.
