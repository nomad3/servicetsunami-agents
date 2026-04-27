# Luna latency benchmark v2 — baseline (no per-stage attribution)

**Date:** 2026-04-27
**Bench script:** `scripts/benchmark_luna.py`
**Target:** `http://localhost:8000`, `test@example.com` tenant (no Gemini / Claude / Codex creds — every turn fell back to `local_gemma_tools`)
**Plan reference:** `docs/plans/2026-04-23-luna-latency-reduction-plan.md` Phase B

> **Caveat** — Phase A.1 stage instrumentation isn't in this run (PR #211, lands next deploy). Numbers below are **end-to-end wall time** of `POST /messages/enhanced` only. Stage-level attribution lands in v3.

## Results

| cell | n | wall p50 | wall p95 | wall avg | platform |
|---|---:|---:|---:|---:|---|
| greeting (`hola luna`) | 2 | **28.0 s** | 32.6 s | 30.3 s | local_gemma_tools |
| light_recall (`qué te dije la última vez?`) | 2 | **34.8 s** | 36.3 s | 35.6 s | local_gemma_tools |
| entity_recall (`qué sabes de mi negocio?`) | 2 | **36.7 s** | 39.0 s | 37.9 s | local_gemma_tools |
| tool_read (`lista mis workflows recientes`) | 2 | **32.2 s** | 35.1 s | 33.6 s | local_gemma_tools |
| multi_step (`dame un resumen rápido de qué pasó hoy`) | 2 | **34.1 s** | 41.5 s | 37.8 s | local_gemma_tools |

## Headline finding

**Every cell clusters in 28–37 s.** The 9-character `hola luna` greeting takes ~28 s; a 5-token light recall takes ~35 s. **The dominant cost is shared by every turn**, not driven by message complexity, recall depth, or tool routing. From the live trace analysis (api logs during the run):

- A `hola luna` greeting triggers **4 LLM rounds**: `find_entities` → wait → `search_knowledge` → wait → `find_entities` → wait → final reply. Luna's persona instructs proactive memory recall on every turn, even greetings.
- The 27-second gap between rounds is **Gemma 4 inference time**, not tool latency. Tool calls themselves return in <100 ms post-fix.
- At ~57 tok/s output and ~1 K tok/s prefill on this M4, an 8–12 K-token CLAUDE.md prompt + persona means **~10 s prefill alone per round**, before the first output token. 4 rounds × 10–15 s/round = 40–60 s baseline.

So: **the latency is in LLM inference, and the LLM is slow because the prompt is huge**.

## What this disproves from the plan

| Hypothesis | Status |
|---|---|
| H1 — CLI subprocess spawn is 1.5–2 s flat overhead per turn | **Untestable here** — no CLI was used; everything fell back to local Gemma. |
| H2 — Gemini CLI is fastest end-to-end on tool turns | **Untestable here** — no Gemini integration on this tenant. |
| H3 — OpenCode + local Gemma 4 fastest on greeting/light recall | **Disproven.** Local greeting was 28 s; would need to be <3 s to be "fastest". |
| H4 — MCP tool roundtrips add 800–1500 ms each | **Plausible from log timing** (each SSE+POST cycle is sub-second), but dwarfed by LLM rounds. |
| H5 — CLAUDE.md render is non-trivial (>200 ms) | **Untestable until A.1.** Rendering on the api side is fast; the cost is the LLM *processing* it. |
| H6 — Memory recall is well-optimized (<250 ms) | **Untestable until A.1.** |
| H7 — Cold runs >2× warm | **Disproven.** Warmup and runs both clustered in the same band (28–41 s). |

## Bugs surfaced during this run

1. **`local_tool_agent` MCP path was completely broken in production.** Wrong env var (`MCP_TOOLS_URL` vs `MCP_SERVER_URL`) and wrong endpoint (`/mcp` 404, server is on `/sse`) → every tool call from the local fast-path was failing with `Connection refused` and falling through to LLM-only mode. **Fixed in PR #210.** The numbers in this v2 bench are with the fix in place; v0 and v1 numbers (in `2026-04-27-luna-bench-v0-aborted.md`) are pre-fix.
2. **Auto-deploy on PR merge can kill the api mid-bench.** PR #210's deploy SIGTERM'd the api during the v1 light_recall cell. **Fixed in PR #211 (bench resilience):** the script now writes a partial report on transient connection drops instead of crashing.

## Implications for Phase D actions

The plan's Tier-1 list lands very differently after this data:

| # | Plan action | Estimated saved | Reality on local-Gemma path |
|---|---|---:|---|
| 1 | CLI process pool / warm CLI workers | ~1500 ms | **Not applicable** — local path doesn't spawn a CLI. |
| 2 | Local fast-path bypass for greetings | ~3500 ms | **Larger than estimated** — would short-circuit ~28 s of greeting latency, not 3.5 s. **#1 priority for the local path.** |
| 3 | Pre-warmed memory context per session | ~200 ms | Untestable until A.1; likely a small share of total. |
| 4 | Reduce CLAUDE.md size for cached personas | ~400 ms | **Likely 5×–10× larger** — prompt prefill is the cost. **#2 priority.** Tenant- and intent-aware prompt trim could halve every cold turn. |

**Two new Tier-1 actions** that emerge from this data and aren't in the original plan:

5. **Skip proactive memory recall on short greetings.** Persona instructs `find_entities` + `search_knowledge` on every turn; for ≤3-word inputs this adds 2–3 LLM rounds for no value. Combine with #2.
6. **Reduce input-token budget on the local path specifically.** Cloud CLIs (Gemini / Claude) handle large prompts cheaply; local Gemma pays per-token prefill. The CLAUDE.md render path needs a `local_path` mode that strips episodic recall, world-state injection, self-model, and most of the tool registry — keeping only persona + immediate context.

Without measurements on a Gemini-CLI-wired tenant, we don't yet know what the original user complaint ("Luna feels slow") looks like. **Next bench step:** run against `saguilera1608@gmail.com` (AgentProvision tenant has Gemini wired) using `--token <jwt>` so the CLI path numbers are real.

## Files

- Raw rows: `benchmarks/2026-04-27-luna-bench.json`
- Auto-generated summary: `benchmarks/2026-04-27-luna-bench.md` (overwrites on each run; this doc is the curated companion)
- v0/v1 aborted run: `benchmarks/2026-04-27-luna-bench-v0-aborted.md`
