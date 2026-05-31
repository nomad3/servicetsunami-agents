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
- Build a **single-line trigger** and pass it to the runner as `prompt=`. The trigger also names an **answer file** so the response comes back out-of-band (see §4.4): `Read the file {abs_turn_file} and respond to the user request it contains. Write ONLY your final answer to {abs_answer_file} (overwrite it); no preamble. Reply directly — do not ask for confirmation.`
- Pass `answer_file={abs_answer_file}` (under `session_dir`) to the runner so it can read the clean answer back.
- Keep writing `session_dir/CLAUDE.md` (harmless belt-and-suspenders; **not** relied on for delivery per §4.0).

### 4.2 `apps/code-worker/cli_executors/claude_interactive.py`
- After launch, **wait until the input box is rendered and stable** before typing (Codex #4): detect the bottom prompt/suggestion line (e.g. the `❯`/`Try "…"` placeholder or the input box border) and require a short stable settle window; ensure no trust/onboarding dialog text is present. Do **not** type into a not-yet-ready REPL.
- **Type the trigger as TWO separate writes** — the text, a short settle (~0.5 s), then `\r` on its own (smoke-proven §4.4 / Defect 1). The REPL runs bracketed-paste mode on; a long line glued to `\r` in one write is swallowed as paste content and the `\r` becomes a literal newline, never Enter → the turn never submits. Separated, it submits and answers in ~2 s with MCP attached.
- Belt-and-suspenders: if a folder-trust prompt appears despite #732's seed, answer it (it precedes the input box).
- Keep the existing idle-`/exit` + first-output + process-group logic (#735) unchanged.
- **Read the answer from `answer_file`, not the transcript** (§4.4 / Defect 2): once the turn goes idle, read `answer_file` and return it as the response (normalize returncode to success when it's non-empty). The TUI transcript is a **fallback only** — `clean_interactive_transcript()` (hardened to drop trigger echo / `[Pasted text]` / `Read` chrome) is best-effort and **cannot** reliably reconstruct a cursor-addressed TUI redraw (spinner frames, box rules), so it must not be the primary answer source.

### 4.4 Smoke-gate findings (the gate fired — two defects in the first build)
The pre-merge worker smoke (§5.0) drove the patched runner against the live worker + 168-tool MCP with a realistic blob and **failed**, surfacing two issues the unit tests (with non-wrapping fakes + no real REPL) could not:
- **Defect 1 — glued submit never fires.** Writing `trigger + "\r"` in one buffer: the REPL's bracketed-paste mode absorbs the long line as paste and the trailing `\r` becomes a newline inside the box, not Enter. Result: trigger echoes, no submit, returncode 143. **Proven fix:** text → ~0.5 s settle → `\r` as a separate write → submits, MCP attaches, file is read, Claude answers (`"2+2 is 4, and I'm Luna…"`). → §4.2.
- **Defect 2 — the cleaner can't beat the TUI.** Interactive Claude is a full cursor-addressed TUI (spinner frames, redrawn input box, box rules); a line-based `clean_interactive_transcript` returned 3150 chars of chrome with the answer buried. A terminal-emulator replay (e.g. `pyte`) would work but violates the module's stdlib-only constraint. **Fix:** out-of-band answer file (§4.1/§4.2) — Claude writes its final answer to a file we read back; the scrape stays only as a fallback.

Both are now in the fix; the re-smoke must pass before merge.

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

## 9. Residual production failure — STARTUP FREEZE under host load (2026-05-30, post-#742)

After #738/#741/#742 the runner logic is sound — a controlled reproduce-and-capture (warm `/home/codeworker` HOME, fresh per-session cwd with trust seeded, real `mcp.json`, `--permission-mode acceptEdits`, under host load 18–24) passed **5/5** (17–22 s; `submit_text` at +0.7 s, answer written, rc normalized). Yet the user still hit intermittent `exit 143, response_len=0` from WhatsApp.

**Root cause (Codex static review + empirical capture AGREE).** The one captured failure (01:55:58 turn → 101 s) dead-stops the instant Claude finishes its first paint: input box `❯` + `acceptEdits` banner rendered, then **zero further bytes — no trigger echo, no spinner**, `answer.md` never written. Claude painted its UI then its **Node event loop froze** under host starvation (the Mac had just rebooted; load 18–26 from ollama/embedding-service/etc.). 101 s ≈ submit + the internal `first_output_seconds=90` post-submit no-output cap → SIGKILL = 143. It is **transient and self-healing** as load drops (hence the 5/5 green repros). Notably the *preceding* 01:52 turn the user blamed actually **succeeded** (auto-scored 72/100); only the 01:55 retry froze.

Two real correctness bugs Codex flagged compounded it: (a) `response_seen` flipped `True` on **any** post-submit byte incl. a folder-trust redraw, which would skip the whole `not response_seen` branch and **disable the recovery resend**; (b) the cold cap (90 s) is far too long to wait on a process that will never respond — it just delays the cure.

**Fix (#743) — resilience, not more submit logic (both diagnostics: the submit path is healthy):**
1. **Split the post-submit cap** — new `post_submit_first_output_seconds` (env `CLAUDE_CODE_INTERACTIVE_POST_SUBMIT_FIRST_OUTPUT_SECONDS`, default **35 s**) bounds only the **dead-silent** (frozen) case in `decide_pty_action` §3. The instant Claude emits any post-submit byte, `response_seen` flips and the turn moves to the answer-await path (§4b) which keeps the full 90 s — so a slow-but-**alive** reply is **never** killed early; only a frozen one is.
2. **Fresh-process retry** — `execute_claude_chat` wraps the interactive turn in a bounded loop (env `CLAUDE_CODE_INTERACTIVE_MAX_ATTEMPTS`, default **2**). On the freeze signature (`returncode != 0` **and** empty stdout) it relaunches once with a **fresh** per-turn scratch dir (`_build_interactive_turn`). A resend into a frozen REPL is useless; only a new process cures it. Relaunch is immediate (no gap) so the Temporal activity heartbeat stays fresh.
3. **Resend reachable (Codex BLOCKER)** — the `response_seen` flip is now trust-filtered (same guard `response_substantive` already uses), so a trust-dialog redraw no longer escapes the freeze gate or disables the resend.

Worst case is bounded: a frozen attempt dies at ~35–50 s, the fresh relaunch typically succeeds in ~20 s — well under Temporal's 1500 s and the 30 s internal heartbeat.

**Parked follow-ups (own PRs):** (P1) **per-worker concurrency guard** so two cold `claude` launches can't starve each other (the 01:52+01:55 overlap is what triggered the freeze) — deferred because a blocking semaphore on the hot path needs care around the heartbeat; (P2) Codex's blind-5 s-ceiling submit (`decide_pty_action`) can in theory submit before `input_box_seen` — left as-is since the empirical capture showed the input-box path fires first and the new retry + trust-filtered resend both cover the eaten-submit mode; (P3) `_TRUST_RE`/`_INPUT_BOX_RE` are brittle string probes — revisit if a future claude release changes the trust/placeholder strings.

**Verification.** Unit: 3 new `decide_pty_action` cases (short cap kills a frozen turn at 35 s; the cap is opt-in and never shortens legacy behavior when unset; resend still fires first within the window) + existing 61 green. **E2E gate (must run on the deployed image, NOT an isolated host smoke — the host `claude` is too fast to reproduce the fresh-container freeze):** route tenant `752626d9` to `claude_code`, send several real chat turns, confirm 0 empty/143 failures and that an induced freeze recovers via relaunch (worker log `relaunching fresh process`), then restore default to `codex`.
