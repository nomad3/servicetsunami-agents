# Luna latency benchmark v6 — Tier-1 #2 (CLAUDE.md trim) validated

**Date:** 2026-04-27
**PR validated:** #218 (trim local-path prompt: anti-hallucination preamble + skill_body cap + tool result truncation)
**Tenant:** test@example.com (no Gemini/Claude/Codex; falls to `local_gemma_tools`)

## Results

| cell | v4 p50 (pre-fix) | v6 p50 (post-fix) | Δ | Δ % |
|---|---:|---:|---:|---:|
| greeting | 21,845 ms | **84 ms** | **−21,761 ms** | **−99.6 %** |
| light_recall | 33,452 ms | 16,057 ms | −17,395 ms | −52 % |
| entity_recall | 36,915 ms | 20,337 ms | −16,578 ms | −45 % |
| tool_read | 34,854 ms | 14,986 ms | −19,868 ms | −57 % |
| multi_step | 33,003 ms | 17,211 ms | −15,792 ms | −48 % |

**Average non-greeting cell improvement: −17.4 s / −51 %.**
Combined with the greeting fast-path, every user-facing prompt class is now materially faster.

## Bench session running tally (PRs)

| PR | What | Effect |
|---|---|---|
| #210 | local_tool_agent MCP-SSE repair | Tool calls actually work on local path |
| #211 | Phase A.1 outer-stage instrumentation + bench resilience | Per-stage timings persist into chat_messages.context |
| #213 | local_tool_agent inner LLM/tool/overhead split | Confirmed 99.5 %+ of latency is Gemma 4 inference |
| #215 | Greeting template fast-path | 22 s → 170 ms warm |
| #216 | Keyword fallback for cold-start race | Fast-path actually fires after api restart |
| #218 | Local-path prompt trim | −17 s/turn on every non-greeting cell |
| (next) | Ollama keep_alive (Tier-1 #3 / cold-start kill) | Eliminates 50–70 s warmup cost |

## Plan target audit

The original plan's success criteria after Tier-1 lands:

| Metric | Original target | v6 actual |
|---|---:|---:|
| Greeting p50 | <1 s | **84 ms** ✅ (12× under target) |
| Light recall p50 | <4 s | 16 s (still >target) |
| Tool-call read p50 | <6 s | 15 s (still >target) |
| Tool-call write p50 | <8 s | (not tested — write paths skipped) |

The two remaining gaps (light_recall, tool_read) are now the next leverage:

1. **Skip proactive recall on short non-greeting turns** — every turn still makes 2 Gemma rounds because Luna's persona forces `find_entities` + `search_knowledge` even when the user just said "ok 👍" or "gracias". For ≤3-word non-greetings, drop to 1 round → ~50 % more save on those = brings light_recall (5 words) closer to 8 s.
2. **Pre-warm Gemma model after deploy** — first request after deploy hit 70 s warmup in v5/v6. Ollama `keep_alive` parameter (queued in this session, ready to ship).
3. **Re-bench against AgentProvision tenant via `--token`** — original user complaint was on the Gemini-CLI path, not the local-Gemma path. Numbers there will be very different and need their own optimization queue.

## What's permanently dropped from the original plan

- **Tier-1 #1 (CLI process pool / warm CLI workers — 16 eng hours):** zero ms saved on the local path. **Unfunded.**
- **Tier-2 #4 (reduce CLAUDE.md size — 400 ms estimate):** under-estimated by 40×. Became Tier-1 #2 above with a measured 15–20 s save per turn.

## Cold-start anomaly

The greeting cell warmup hit 70 s even though `platform=template`. The template path itself is sub-millisecond; the cost is somewhere else in the chat path on the very first request after a deploy. Ollama model reload + initial DB pool warmup is the most likely cause. The next-up Ollama `keep_alive: 30m` change should kill most of this.

## Files

- `benchmarks/2026-04-27-luna-bench-v6.json` — raw rows.
- `benchmarks/2026-04-27-luna-bench-v6-raw.md` — auto-generated harness output.
- (this file) — curated narrative + comparison.
