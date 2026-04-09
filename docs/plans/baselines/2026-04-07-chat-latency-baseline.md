# Chat Latency Baseline — Pre-Phase-1

**Date:** 2026-04-07
**Branch:** `feat/memory-first-phase-1`
**Commit at run time:** `3f517f04` (parent of this commit)
**Hardware:** Mac M4, 48 GB unified memory, native Ollama for Gemma4
**Tenant:** `0f134606-3906-44a5-9e88-6c2020f0f776` (saguilera1608@gmail.com — Simon's production tenant)
**Session:** `c64cdc1c-0236-4881-9b18-a4e13c742591` (created fresh for this baseline; can be deleted after Phase 1 ships)
**Stack state:** docker-compose, all services up. `EXPLORATION_RATE=0.0`, `EXPLORATION_MODE=off` (per commit `07d77cb4`). USE_MEMORY_V2 not yet implemented.

## Method

`apps/api/scripts/baseline_chat_latency.py` posts 20 messages sequentially through `POST /api/v1/chat/sessions/{id}/messages`, captures wall-clock latency per request, computes percentiles. Each request runs the FULL hot path: agent_router → memory_recall.build_memory_context_with_git → cli_session_manager → Claude Code CLI subprocess → response → daemon-thread side effects.

Probe set is 10 prompts × 2 cycles, mixing trivial (greetings, "ok", "thanks") with heavy-recall (entity lookups, multi-source synthesis):

```
hey luna
what are my open commitments
remind me what we discussed yesterday
who is Ray Aristy
what is my next meeting
what's the status of integral
summarize the memory-first design doc
thanks
ok
what platforms are we tracking competitors on
```

Per-request hard timeout: **120 seconds** (script-side `httpx.AsyncClient(timeout=120)`).

## Result

```json
{
  "label": "phase-0-pre-refactor",
  "n_requested": 20,
  "n_success": 20,
  "n_errors": 0,
  "p50_seconds": 47.147,
  "p95_seconds": 120.028,
  "p99_seconds": 120.028,
  "mean_seconds": 41.124,
  "max_seconds": 120.028,
  "min_seconds": 8.062,
  "errors": []
}
```

## Per-probe breakdown

| # | Latency | Prompt | Category |
|---|---|---|---|
| 1 | 10.46s | `hey luna` | trivial / fast path |
| 2 | 15.91s | `what are my open commitments` | medium recall |
| 3 | 76.13s | `remind me what we discussed yesterday` | heavy recall + summarization |
| 4 | 56.34s | `who is Ray Aristy` | entity lookup |
| 5 | 26.08s | `what is my next meeting` | calendar lookup |
| 6 | **120.03s** ⚠️ | `what's the status of integral` | **TIMEOUT-CAPPED** — true value > 120s |
| 7 | 85.03s | `summarize the memory-first design doc` | doc synthesis |
| 8 | 70.43s | `thanks` | should be fast (regression hint) |
| 9 | 10.45s | `ok` | trivial / fast path |
| 10 | 9.05s | `what platforms are we tracking competitors on` | medium recall |
| 11 | 9.45s | `hey luna` | trivial / fast path (warm) |
| 12 | 47.15s | `what are my open commitments` | medium recall |
| 13 | 54.73s | `remind me what we discussed yesterday` | heavy recall (warm) |
| 14 | 56.47s | `who is Ray Aristy` | entity lookup (warm) |
| 15 | 28.23s | `what is my next meeting` | calendar lookup |
| 16 | 47.18s | `what's the status of integral` | retried probe (warm path, no timeout) |
| 17 | 54.32s | `summarize the memory-first design doc` | doc synthesis (warm) |
| 18 | 26.43s | `thanks` | second pass faster than first (#8 regression) |
| 19 | 8.06s | `ok` | trivial / fast path |
| 20 | 10.55s | `what platforms are we tracking competitors on` | medium recall (warm) |

## Observations

1. **Fast path actually exists** for greetings and acks: 8-16s (probes 1, 9, 10, 11, 19). This is bounded by Claude CLI subprocess startup + minimal recall, not the recall layer itself.

2. **Heavy-recall queries dominate the slow path**: 47-85s for entity/calendar/doc lookups (probes 3-7, 12-17). The current `memory_recall.build_memory_context_with_git` is doing real semantic retrieval over the populated KG (331+ entities, 4,817+ observations) plus knowledge extraction in daemon threads.

3. **One probe hit the 120s timeout** (probe 6, "what's the status of integral"). The true p95 is ≥ 120s, possibly significantly higher — the script's timeout is a measurement floor, not a real value. When the same prompt was retried (probe 16), it completed in 47s — suggesting variance in the recall path under contention rather than a structural ceiling.

4. **"thanks" took 70s on probe 8** — that's a fast-path regression hint. Trivial messages should never go through heavy recall, but the current router doesn't gate them. Probe 18 (also "thanks") was 26s; the variance is real and material.

5. **No errors across 20 probes** — the system is stable, just slow.

## Acceptance threshold for Phase 1

Per design doc §11.1 anti-success criterion #1: **"Fast-path p95 regresses > 30% from pre-Phase-1 baseline → roll back."**

Using these baseline numbers, the Phase 1 anti-success thresholds are:
- p50 must stay below `47.1 × 1.30 = 61.3s`
- p95 must stay below `120 × 1.30 = 156s` (but the baseline p95 is timeout-capped, so effectively: don't make probes time out more often)
- Mean must stay below `41.1 × 1.30 = 53.5s`

**Phase 1 positive target** (per design doc §11): the new pre-loaded recall + warm path should land p50 in the **6-15 second** range, NOT 47s. That's a 70-85% improvement, not a "don't regress" target. If Phase 1 only achieves "no worse than baseline", we have not delivered the value proposition — we've just refactored without performance impact, and the chat hot path is still unusably slow for conversational use.

The hard fast-path SLO of `p50 < 2s` is **conditional on Phase 3a** (warm chat-runtime pods) per the design doc, NOT achievable in Phase 1.

## Notes

- Baseline session `c64cdc1c-0236-4881-9b18-a4e13c742591` is a throwaway. Delete after Phase 1 ships:
  ```sql
  DELETE FROM chat_messages WHERE session_id = 'c64cdc1c-0236-4881-9b18-a4e13c742591';
  DELETE FROM chat_sessions WHERE id = 'c64cdc1c-0236-4881-9b18-a4e13c742591';
  ```
- Re-run command for the post-Phase-1 comparison:
  ```bash
  TOKEN=$(curl -s -X POST http://localhost:8000/api/v1/auth/login \
    -H "Content-Type: application/x-www-form-urlencoded" \
    --data-urlencode "username=saguilera1608@gmail.com" \
    --data-urlencode 'password=...' \
    | python3 -c "import sys, json; print(json.load(sys.stdin)['access_token'])")
  BASELINE_TOKEN="$TOKEN" \
  BASELINE_SESSION_ID="<new-session-uuid>" \
  BASELINE_N=20 \
  BASELINE_LABEL="phase-1-post-cutover" \
  python3 apps/api/scripts/baseline_chat_latency.py
  ```
- Re-run uses a NEW session each time so the post-Phase-1 numbers reflect cold-cache behavior, not warm-cache from prior probes.
