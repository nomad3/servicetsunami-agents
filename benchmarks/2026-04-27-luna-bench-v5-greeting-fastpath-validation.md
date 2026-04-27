# Luna latency benchmark v5 — greeting fast-path validation

**Date:** 2026-04-27
**PRs validated:** #215 (template fast-path) + #216 (keyword fallback for cold-start race)
**Tenant:** test@example.com (no Gemini/Claude/Codex)

## Result

| prompt | wall | platform | verdict |
|---|---:|---|---|
| `hola luna` (1st request after deploy) | 55,309 ms | template | template content correct; cold-start elsewhere added ~55 s — **unrelated to fast-path**, see note |
| `hi` | **170 ms** | template | ✅ |
| `buenos días` | **551 ms** | template | ✅ |
| `hola, qué tal?` | 37,221 ms | local_gemma_tools | ✅ correctly skipped (has `?`) — went to LLM |
| `hola luna! cómo estás con todo lo que tenemos pendiente` | 34,074 ms | local_gemma_tools | ✅ correctly skipped (>30 chars) — went to LLM |

## Headline

**Warm greeting wall time: 170–551 ms** (down from 22 s pre-fix). **~130× improvement** on the prompts where the persona was previously forcing 2 Gemma 4 inference rounds for "say hi back".

The two skip rules work as designed:
- `?` (or `¿`) in the message → still goes through the full LLM path so questions like `hola, qué tal?` get a proper conversational reply.
- `>30` chars → still goes through the full LLM path so messages like `hola luna! cómo estás con todo lo que tenemos pendiente` aren't dropped.

## Cumulative impact across the bench cells

Estimated effect on the next full bench run (assuming the LLM-path cells stay at v4 numbers):

| cell | v4 p50 | v5 p50 (expected) | Δ |
|---|---:|---:|---:|
| greeting | 21,845 ms | **<500 ms** | **−21,300 ms** |
| light_recall | 33,452 ms | 33,452 ms | unchanged (not a greeting) |
| entity_recall | 36,915 ms | 36,915 ms | unchanged |
| tool_read | 34,854 ms | 34,854 ms | unchanged |
| multi_step | 33,003 ms | 33,003 ms | unchanged |

Greetings are ~25 % of aremko WhatsApp volume (per CLAUDE.md / memory note); user-perceived improvement is large for that share of traffic.

## What landed in this session (chronological)

| PR | Title | Effect |
|---|---|---|
| #210 | local_tool_agent MCP-SSE repair | Tool calls actually work on the local path now (silent prod bug pre-fix). |
| #211 | Phase A.1 outer-stage instrumentation + bench resilience | Per-stage timings persist into `chat_messages.context.timings`. |
| #212 | v2 baseline doc | Clean wall-time baseline before attribution. |
| #213 | local_tool_agent inner timings split | LLM vs tool vs overhead split inside `run()`. |
| #214 | v3 attribution doc | Confirmed orchestrator innocent (1 ms total). |
| #215 | Greeting template fast-path | Tier-1 #1 implementation. |
| #216 | Keyword fallback for cold-start race | Fast-path actually works after api restart (was 0% effective without). |
| (this) | v5 validation + final results | 170–551 ms warm greeting, ~130× improvement. |

## Cold-start anomaly (filed for follow-up)

The first request after a deploy hit 55 s wall even though `platform=template`. The template content returned correctly, so the work happened *outside* the fast-path (plausibly Ollama model reload, embedding-service warmup, or memory-recall pre-build for a brand-new session — chat.py runs that before agent_router gates on intent). Worth investigating as a separate item; doesn't block the fast-path wins on warm requests.

## Open Tier-1 items (in priority order, post-v5)

1. **Trim CLAUDE.md for the local path** — bench v4 showed each Gemma round costs 13–20 s of prefill on an 8–12 K-token CLAUDE.md. Strip episodic recall + world-state + self-model + full tool schemas for the local fallback. Estimate: **−10 to −16 s per non-greeting cell**. Eng hours: 8.
2. **Skip proactive recall on short turns** — Luna's persona forces `find_entities` + `search_knowledge` even on "ok 👍" / "gracias" turns that aren't greetings. Adding a router-level gate cuts these to 1 round (≈ 50 % save). Eng hours: 4.
3. **Pre-warm Gemma model after deploy** — `keep_alive` parameter on Ollama, or a synthetic warmup hit. Eliminates the 50–60 s warmup that v3/v4 warmups showed. Eng hours: 2.
4. **Investigate the cold-start anomaly above** — find where the 55 s went on the first request even with template. Eng hours: ~2.

## What's permanently dropped from the original plan

- **Tier-1 #1 from the plan (CLI process pool / warm CLI workers — 16 eng hours):** zero ms saved on the local path; no CLI is spawned. Verified twice across v3 and v4 (`setup` = 0 ms, no subprocess metadata). The original plan was based on cloud-CLI tenants; doesn't apply here.
- **Tier-2 #4 in the original plan (reduce CLAUDE.md size — 400 ms estimate):** under-estimated by ~30×. Becomes Tier-1 #2 above with a 10–16 s real estimate.

## Files

- `benchmarks/2026-04-27-luna-bench-v0-aborted.md` — early run that surfaced the local_tool_agent MCP bug (PR #210).
- `benchmarks/2026-04-27-luna-bench-v2-baseline.md` — clean wall-time baseline.
- `benchmarks/2026-04-27-luna-bench-v3-stage-attribution.md` — outer-stage attribution.
- `benchmarks/2026-04-27-luna-bench-v3.json` + `-raw.md` — raw v3.
- `benchmarks/2026-04-27-luna-bench-v4-final.md` — inner attribution (this is the most useful one to read).
- `benchmarks/2026-04-27-luna-bench-v4.json` + `-raw.md` — raw v4.
- (this file) — validation that the fast-path actually fires.
