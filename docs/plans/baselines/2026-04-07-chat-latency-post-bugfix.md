# Chat Latency — Post-Bugfix (Migration 089) — INCOMPLETE

**Date:** 2026-04-07
**Branch:** `feat/memory-first-phase-1` (rebased onto main with PR #131 merged)
**Commit at run time:** `a4d28cdb` (Plan Task 10 done, PR #131 squash-merge `02945fcc` rebased in)
**Hardware:** Mac M4, 48 GB unified memory
**Tenant:** `0f134606-3906-44a5-9e88-6c2020f0f776` (production)
**Stack state:** docker-compose, all services up. `EXPLORATION_RATE=0.0`, `EXPLORATION_MODE=off`. **Migration 089 applied** — `embeddings.content_type` orphans renamed (`knowledge_entity` → `entity`, `knowledge_observation` → `observation`). 344 entity embeddings now searchable (was 0). 5,700 observation embeddings now searchable (was 883). **USE_MEMORY_V2 still false** — chat hot path still calls the legacy `memory_recall.build_memory_context_with_git()`, not the new `memory.recall()`.

## Status: INCOMPLETE — investigation deferred

Two baseline attempts both hit errors. Reporting both honestly so the next clean run has prior context.

## Attempt 1 — corrupted by CI redeploy mid-run

**Session:** `fef50c81-2a66-4925-8568-bd0dfa1829aa` (deleted)
**Result:** 5/20 success, 15 errors

Errors started at probe 5 with `RemoteProtocolError: Server disconnected without sending a response.`, then `ReadError` for the remainder. Root cause: merging PR #131 to main triggered the local-deploy CI workflow, which restarted the api container at probe ~5. The 5 successful probes:

| # | Latency | Prompt | Notes |
|---|---|---|---|
| 1 | 12.71s | `hey luna` | warm fast path |
| 2 | 53.98s | `what are my open commitments` | recall now hits real entities |
| 3 | 37.24s | `remind me what we discussed yesterday` | episode + entity recall |
| 4 | 21.07s | `who is Ray Aristy` | **vs 56.3s in Phase 0** — semantic entity search now returns rows |
| 5 | 54.22s | `what is my next meeting` | calendar lookup |

Then container restart → 15 errors → run aborted.

## Attempt 2 — clean session, post-redeploy

**Session:** `29806365-47b7-41b1-a2f6-624adbd75ddc` (still in DB at write time, will be cleaned up)
**Result:** 13/20 success, 7 errors (4 ReadTimeout, 1 HTTP 500, 2 unrecorded)

```json
{
  "label": "post-bugfix-clean-run",
  "n_requested": 20,
  "n_success": 13,
  "n_errors": 7,
  "p50_seconds": 32.203,
  "p95_seconds": 86.71,
  "p99_seconds": 86.71,
  "mean_seconds": 43.395,
  "max_seconds": 86.71,
  "min_seconds": 9.136
}
```

| # | Latency | Prompt | Notes |
|---|---|---|---|
| 1 | 20.31s | `hey luna` | colder than attempt 1's 12.71s — fresh container |
| 2 | 26.39s | `what are my open commitments` | |
| 3 | 75.12s | `remind me what we discussed yesterday` | |
| 4 | 47.20s | `who is Ray Aristy` | (attempt 1 hit 21s — high variance) |
| 5 | 19.88s | `what is my next meeting` | |
| 6 | 71.74s | `what's the status of integral` | (Phase 0 hit the 120s cap on this exact prompt) |
| 7 | 58.73s | `summarize the memory-first design doc` | |
| 8 | 9.14s | `thanks` | fast path |
| 9 | 86.71s | `ok` | ⚠️ trivial message took 86s — same regression hint as Phase 0 probe 8 |
| 10 | 28.40s | `what platforms are we tracking competitors on` | |
| 11 | 9.90s | `hey luna` | warm fast path |
| 12-14 | TIMEOUT | (multiple) | 4 ReadTimeouts in a row — server hung |
| 15 | 32.20s | `what is my next meeting` | recovered |
| 16 | HTTP 500 | (unknown prompt) | Internal Server Error |
| 19 | 78.43s | `ok` | another trivial-message regression |

## Comparison vs Phase 0 baseline

| Metric | Phase 0 (pre-bugfix) | Post-bugfix attempt 2 | Δ |
|---|---|---|---|
| Success rate | 20/20 (100%) | 13/20 (65%) | -35pp ⚠️ |
| p50 | 47.1s | **32.2s** | **-32% ✅** |
| p95 | 120s (capped) | 86.7s | **-28%** (but smaller sample) |
| Mean | 41.1s | 43.4s | +6% (within noise) |
| Min | 8.1s | 9.1s | +12% (within noise) |
| Max | 120s (capped) | 86.7s | -28% |

**Directional finding:** the bugfix made successful probes meaningfully faster on heavy-recall queries (`who is Ray Aristy` 21s/47s vs 56s; `what's the status of integral` 71s vs 120s timeout). Semantic entity search is now actually working.

**Concerning finding:** the post-bugfix run had a 35% error rate. Phase 0 had 0%. This is NOT caused by the memory-first refactor — chat hot path still calls `memory_recall.build_memory_context_with_git()` because `USE_MEMORY_V2` is false. Plausible causes (untested):

1. **Semantic recall now returns 344 entities** (was 0). The downstream prompt-building step now has more data to chew through, which may push some turns past Claude API's per-call limits or cause prompt-inflation timeouts.
2. **Post-redeploy warmup** — the container had 28s uptime when the baseline started. Some lazy-loaded resources (embedding model? RL routing tables?) may have warmup spikes.
3. **Background workers** — orchestration-worker's continue_as_new cycles might be contending with the chat path.
4. **Ollama contention** — if Gemma4 is being called for both chat scoring AND auto-extraction more often now that recall returns entities, the GPU may be saturating.

## Decision: data is too messy to set as the Phase 1 anti-success threshold

This baseline is **NOT ready** to replace the Phase 0 numbers as the regression threshold. Two options:

1. **Investigate the error rate first** before re-baselining. The error pattern (clustered timeouts, 1 HTTP 500, then recovery) suggests a transient resource contention rather than a structural bug. Worth a 30-min look at api/orchestration-worker logs from the run window before assuming it's noise.
2. **Re-run after the system has been up >10 minutes** and document if the error rate persists. If a third clean run also gets 30%+ errors, the bugfix may have created a real downstream pressure that needs fixing before Phase 1.7 cutover.

**Phase 0 baseline remains the active anti-success threshold** until we have a clean post-bugfix run.

## Sessions to clean up

```sql
-- Both throwaway baseline sessions, safe to delete
DELETE FROM execution_traces WHERE session_id IN (
  'fef50c81-2a66-4925-8568-bd0dfa1829aa',
  '29806365-47b7-41b1-a2f6-624adbd75ddc'
);
DELETE FROM chat_messages WHERE session_id IN (
  'fef50c81-2a66-4925-8568-bd0dfa1829aa',
  '29806365-47b7-41b1-a2f6-624adbd75ddc'
);
DELETE FROM chat_sessions WHERE id IN (
  'fef50c81-2a66-4925-8568-bd0dfa1829aa',
  '29806365-47b7-41b1-a2f6-624adbd75ddc'
);
```
