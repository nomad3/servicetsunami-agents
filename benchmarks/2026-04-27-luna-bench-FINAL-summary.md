# Luna latency reduction — final session summary (2026-04-27)

**Plan:** `docs/plans/2026-04-23-luna-latency-reduction-plan.md`
**Path measured:** `local_gemma_tools` (test tenant has no Gemini/Claude/Codex creds, falls through to local Ollama Gemma 4)
**Hardware:** Mac M4 with 45 GB unified memory, native Ollama on host (`host.docker.internal:11434`)

## Headline

Across one session of measure → optimize → re-measure, Luna's local-Gemma chat path went from a 22–37 s p50 baseline to a **55 ms p50 on greetings and 15–17 s p50 on every other cell** (entity_recall is noisier at 21–35 s due to n=2 sampling).

| cell | v4 baseline | v8 final | factor |
|---|---:|---:|---:|
| greeting (`hola luna`) | 21,845 ms | **55 ms** | **397 ×** |
| light_recall | 33,452 ms | 15,616 ms | 2.1 × |
| entity_recall | 36,915 ms | 30,814 ms | 1.2 × (noisy) |
| tool_read | 34,854 ms | 15,260 ms | 2.3 × |
| multi_step | 33,003 ms | 17,164 ms | 1.9 × |

## Plan target audit

| Metric | Plan target | v8 actual | status |
|---|--:|--:|---|
| Greeting p50 | <1 s | 55 ms | ✅ **18 × under target** |
| Light recall p50 | <4 s | 15.6 s | ❌ over (Gemma floor) |
| Tool-call read p50 | <6 s | 15.3 s | ❌ over (Gemma floor) |
| Failure rate | <2 % | 0 % | ✅ |

Greeting target hit decisively. Other targets are below the Gemma 4 prefill+generate floor on this M4; can't be reached on the local path without (a) a smaller model, (b) prompt-caching, (c) GPU upgrade, or (d) measuring the actual Gemini CLI path the original user complaint was about.

## What landed (chronological)

| PR | Lever | Effect |
|---|---|---|
| #210 | Repair `local_tool_agent` MCP-SSE client | Tool calls actually work on local path (silent prod bug pre-fix) |
| #211 | Phase A.1 outer-stage instrumentation + bench harness resilience | Per-stage timings persist into `chat_messages.context` |
| #212 | v2 baseline doc | Clean wall-time baseline before attribution |
| #213 | Inner LLM/tool/overhead timing split | Confirmed 99.5 %+ of latency is Gemma 4 inference |
| #214 | v3 attribution doc | Orchestrator confirmed innocent (1 ms total per turn) |
| #215 | Greeting template fast-path | 22 s → 170 ms warm |
| #216 | Keyword fallback for cold-start race | Fast-path actually fires after api restart |
| #217 | v5 fast-path validation doc | Confirmed 130 × greeting improvement |
| #218 | Trim local-path system prompt + tool result truncation | −17 s/turn on every non-greeting cell |
| #219 | Ollama `keep_alive: 30m` on every chat call | Eliminates 50–70 s post-deploy cold start |
| #220 | Cap tool-rounds at 1 for ≤4-word messages | Marginal save on this bench; helps real-world acks ("ok 👍", "gracias") |

## Plan hypotheses, all settled

| H | Status |
|---|---|
| H1 — CLI subprocess spawn 1.5–2 s overhead | **N/A on local path.** No subprocess. |
| H2 — Gemini CLI fastest on tool turns | **Untestable on this tenant.** Needs `--token` against AgentProvision. |
| H3 — Local Gemma fastest on greetings | **Disproven (raw)** then **inverted (post-template)**: greetings now 55 ms via template, faster than any cloud CLI could be. |
| H4 — MCP tool roundtrips 800–1500 ms each | **Disproven by 50–90 ms total per turn** (v4). |
| H5 — CLAUDE.md render >200 ms | **Disproven by 0 ms `setup`** (v3). |
| H6 — Memory recall <250 ms p95 | Pre-built memory branch fires; recall didn't enter the hot timer. |
| H7 — Cold ≥ 2 × warm | **Disproven** by warmup/run clustering (v2-v8). |

## What's permanently dropped from the original plan

- **Plan Tier-1 #1 (CLI process pool / warm CLI workers — 16 eng hours):** zero ms saved on the local path. **Unfunded.** Verified via `setup = 0 ms` and `cli_credentials_missing = 1 ms` across every cell of v3+v4.
- **Plan Tier-2 #4 (reduce CLAUDE.md size — 400 ms estimate):** under-estimated by ~40 ×. Became Tier-1 #2 with a measured 15–17 s save per turn.
- **Plan Tier-3 #7 (skip MCP tool init on no-tool turns — 500 ms):** dwarfed by LLM cost. <100 ms per turn total goes to MCP. Unfunded until LLM cost is solved.

## Open work (queued, not started)

1. **Bench against AgentProvision tenant via `--token <jwt>`.** The original aremko user complaint was about the Gemini-CLI path, not local Gemma. Numbers there will be different (subprocess spawn, gemini-cli's own prompt caching, MCP-SSE handshake). Different optimization queue.
2. **Investigate the unexplained 70 s warmup that occasionally still appears** (v6 greeting warmup, before keep_alive landed). PR #219's Ollama keep_alive should eliminate this; v7+v8 warmups confirm but worth one more deploy cycle to pattern-match.
3. **Smaller local model.** Switching from `gemma4` (14 GB, ~57 tok/s) to a 3B-class model would cut prefill by ~5 × and bring the 15 s floor down to 3–5 s. Trade-off is response quality. Worth A/B-testing on the auto-quality scorer.
4. **Prompt caching.** Modern Ollama supports KV-cache reuse across requests if the system prompt is identical. The trim from PR #218 made the system prompt deterministic (no per-turn timestamps); enabling cache reuse should save another 5–10 s on round 1 of every turn.

## Files

| file | purpose |
|---|---|
| `2026-04-27-luna-bench-v0-aborted.md` | Pre-fix run that surfaced two bugs (PR #210). |
| `2026-04-27-luna-bench-v2-baseline.md` | Clean wall-time-only baseline. |
| `2026-04-27-luna-bench-v3-stage-attribution.md` | Outer-stage attribution (PR #211). |
| `2026-04-27-luna-bench-v4-final.md` | Inner attribution: 99.5 % Gemma inference (PR #213). |
| `2026-04-27-luna-bench-v5-greeting-fastpath-validation.md` | First template path validation (PRs #215+#216). |
| `2026-04-27-luna-bench-v6-trim-validation.md` | Trim validation (PR #218). |
| `2026-04-27-luna-bench-v7-raw.md` + `.json` | Post-keep_alive bench (PR #219). |
| `2026-04-27-luna-bench-v8-raw.md` + `.json` | Post-round-cap bench (PR #220). |
| (this file) | Final session summary. |

## Recommended next session move

Stop on the local path. Mint a JWT for AgentProvision and run:
```bash
python scripts/benchmark_luna.py --token <agentprovision-admin-jwt> --runs 3 --warmup 1
```
The Gemini-CLI path that the original user complaint was about hasn't been measured. Expected wall time: very different — could be faster (cloud Gemini's prompt-caching is mature) or slower (subprocess + MCP-SSE handshake on every turn). Different attribution, different optimization queue.
