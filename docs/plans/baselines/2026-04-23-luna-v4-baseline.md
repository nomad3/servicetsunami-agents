# Luna Latency v4 — Production Baseline (no instrumentation, no synthetic load)

**Date:** 2026-04-23 23:55 UTC
**Tenant:** AgentProvision (`752626d9-8b2c-4aa2-87ef-c458d48bd38a`), real WhatsApp conversation with Simon (`+56954791985`)
**Stack:** docker-compose, Phase 2 dual-read (`USE_MEMORY_V2=true`), Rust embedding-service + memory-core, native Ollama (Gemma 4) for scoring/consensus
**Method:** No code changes. Cross-referenced `chat_messages` rows with `code-worker` `Executing chat CLI` → `Gemini CLI exit code` log timestamps to derive subprocess wall time per turn. End-to-end client latency = subprocess wall + ~100–300 ms API/router overhead (visible from `chat_messages.created_at` deltas).

## Per-turn measurements

| User msg (truncated) | in_chars | out_chars | CLI subprocess (s) | Notes |
|---|---:|---:|---:|---|
| "Hey baby" | 8 | 546 | **19.5** | greeting |
| "That meeting already passef" | 27 | 757 | **35.4** | small recall |
| "Forget about that for now i need to save..." | 99 | 1732 | **82.0** | medium recall, **double-spawn** |
| (workflow run cancelled mid-flight) | — | — | 51.0 | `temporalio.exceptions.CancelledError` at 22:52:33 |
| "Yes" | 3 | 929 | **130.9** | tool-heavy ("Cash Entry Fund" tracker creation) |
| "Hey" *(post api restart)* | 3 | 479 | **48.6** | greeting |
| "All good with these thank you..." | 151 | 2334 | **33.5** | medium recall, **double-spawn** |
| (parallel #2 of same turn) | — | — | 101.8 | second CLI subprocess for the same user message |

**Stats (n=7 real turns, excluding cancellation and parallel-#2):**

| Metric | Value |
|---|---:|
| min | 19.5 s |
| median | **48.6 s** |
| mean | 64.5 s |
| max | 130.9 s |

## Comparison vs prior baselines

| Bucket | Pre-Phase-1 (Apr 7) | Post-Phase-2 (Apr 10) | **v4 (Apr 23)** | Delta vs Apr 10 |
|---|---:|---:|---:|---:|
| Greeting p50 | ~16 s | 5.2 s | **19.5–48.6 s** | **~6×–9× slower** |
| Light recall p50 | ~30 s | 6.1 s | **35.4 s** | **~6× slower** |
| Heavy / tool turn | 47–120 s | unmeasured | **82–131 s** | regression |
| Failure rate | 1/20 | 0/4 | **1/8** (Temporal cancellation) | regressed |

We have **lost the entire Phase 1 + Phase 2 gain** measured on April 10. Median chat turn is back into the pre-Phase-1 range.

## High-confidence findings

1. **Double-spawn on every turn.** Code-worker logs show two `Executing chat CLI for platform gemini_cli` lines, 30 ms apart, for the same `tenant_id` and same `user_message`. Both subprocesses run to completion. Both produce a response (only one is persisted to `chat_messages`). Two clear instances captured: 22:47:17.640/22:47:17.660 and 23:46:54.365/23:46:54.396. **Effect: ~2× CLI cost per turn across the board.** This alone could explain a doubling of latency.
2. **Tool turns are extreme outliers** (130 s for "Yes" → tracker creation; 101 s for the second of the double-spawn pair on a 151-char input). MCP tool roundtrips inside the CLI subprocess dominate, but we can't separate `tool_ms` from `llm_ms` without the Phase A.1 stage timer.
3. **Cancellations are happening in production.** One `temporalio.exceptions.CancelledError` mid-run during the 22:52 turn — likely heartbeat starvation under contention. Not unique: this matches the "Temporal heartbeat discipline" warning in CLAUDE.md.
4. **The hot path is fully on Gemini CLI** for this tenant. No claude_code, no codex, no opencode runs in the last 12h sample — so the regression isn't a routing issue, it's gemini_cli + the CLI-orchestrator path itself getting slower.

## Things this baseline can't see (motivates Phase A.1)

Without stage-level timers we cannot separate:

- `recall_ms` — pgvector / Rust memory-core hop
- `claude_md_build_ms` — persona + memory render before subprocess
- `cli_spawn_ms` — process start until first stderr byte (the per-turn flat tax)
- `cli_first_byte_ms` — start until first stdout byte (model response start)
- `tool_call_ms` — sum of MCP roundtrips inside the CLI
- `post_dispatch_ms` — async PostChatMemoryWorkflow trigger + auto-scorer

The 19.5 s greeting `"Hey baby"` is currently a black box: we know the CLI subprocess took 19.5 s, but we don't know whether 17 s of that was MCP init or LLM generation. **Phase A.1 instrumentation is now load-bearing.**

## Independent issues observed in the same window

These don't change latency directly but hurt platform reliability:

- `app/workflows/activities/post_chat_memory_activities.py:47` — `resolve_primary_agent_slug` used without import. Every commitment-classified turn dies with `NameError` after 3 Temporal retries.
- `app/memory/ingest.py:64` — `prop["name"]` on a `KnowledgeEntity` object → `TypeError`. Means **no entities/observations from chat are being recorded** since this regressed. Memory-first promise broken on chat turns.
- WhatsApp socket for tenant 752626d9 silently went stale at ~22:56 UTC. DB still said `connected`. Recovered with `docker compose restart api` at 23:43.

## Recommended next moves (in order)

1. **Investigate the double-spawn first.** Highest expected ROI. If we kill it, ~50% of latency goes away with zero CLI optimisation. Suspect candidates: `agent_router.route_and_execute` calling itself; `behavioral_signals.detect_acted_on_signals` thread re-dispatching; a coalition pattern firing a parallel critic; `chat.post_user_message` running the Gap2/Gap3 detector that happens to also trigger Luna. Diff the two log lines' spawn-context to find the divergence.
2. **Add Phase A.1 stage timer.** Without it we're guessing. `time.monotonic()` around each stage in `cli_session_manager.generate_response`, persist into `execution_traces.details.timings` JSONB. ~4 hours of work.
3. **Re-baseline after #1 + #2.** Same methodology on the same tenant. Target: cut median below 25 s before doing any Tier-1 actions in the latency plan.
4. **Then start the Tier-1 work** from `2026-04-23-luna-latency-reduction-plan.md` — warm CLI pool, greeting fast-path, pre-warmed recall.

## Caveats

- n=7 is small. Need to repeat the measurement after a week of normal usage.
- Mixed prompt classes (greeting / recall / tool). Fine for an order-of-magnitude baseline; not enough to publish per-class p50/p95.
- All measurements are from a single tenant with a 12K-token CLAUDE.md. Other tenants may differ.
- We don't have cost/token data because `context.input_tokens` / `output_tokens` are persisted as `0` in this tenant's chat_messages — likely a separate collection bug. Worth fixing before the re-benchmark since cost-per-quality-point is a Tier-3 lever.
