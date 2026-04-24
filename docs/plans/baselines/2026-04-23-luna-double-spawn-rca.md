# Luna Double-Spawn — Root Cause Analysis

**Date:** 2026-04-24 00:05 UTC
**Tied to:** `2026-04-23-luna-v4-baseline.md` (finding #1)
**Conclusion:** This is a real ~2× latency tax and we know why.

## Evidence chain

1. **Two `Executing chat CLI` log lines per turn**, ~30 ms apart, same `tenant_id`, same platform. Captured in code-worker logs at 22:47:17.640 / .660 and 23:46:54.365 / .396.
2. **Both subprocesses ran to completion** — code-worker logs show `Gemini CLI exit code: 0` for each.
3. **Temporal workflow list confirms two distinct workflows per turn:**
   - `chat-cli-e619f0f2-…` (the user-visible response path)
   - `coalition-f6eb07d1-…-step-0` (a child of `CoalitionWorkflow`)
4. **The coalition workflow's response was the smoking gun.** Querying Temporal for the coalition output of the 23:46:54 turn:

   > "This request has been triaged as a **P4 Service Request** within the Levi's MDM context. While it deviates from standard system incident protocols…"

   The user asked about building a diversified investment portfolio. The coalition replied with a Levi's MDM incident triage. The pattern is `incident_investigation`. Wrong pattern, wrong audience, wrong content.

5. **CoalitionWorkflow dispatch site found:** `apps/api/app/services/agent_router.py:327-330`

   ```python
   intent = match_intent(message)
   if intent and any(tag in (intent.get("tools") or []) for tag in ["github", "shell", "data", "reports"]):
       if _presence_sid:
           logger.info("Triggering CoalitionWorkflow for complex task: %s", intent["name"])
           dispatch_coalition(tenant_id, _presence_sid, message)
   ```

   The router auto-fires a coalition any time the semantic intent classifier matches one of these tags. The user's "build a diversified portfolio…" semantically matches `forecast revenue or predict trends` → `tools=["data","reports"]` → coalition fires.

## Why this hurts user latency

`dispatch_coalition` is `threading.Thread(daemon=True).start()` — fire-and-forget. So in theory it runs in the background and doesn't block the user response. In practice it absolutely does, because both CLI subprocesses are spawned within 30 ms and contend for:

- **Native Ollama GPU** on M4 (Gemma 4 is 14 GB resident, no second-tenant slot).
- **MCP tools server** (FastMCP SSE; limited concurrency before the unhealthy probe trips).
- **Code-worker thread pool** (Temporal activities run in a shared thread pool).
- **Database connection pool** (each subprocess pulls memory context, integration creds, persona).

The result is what we measured: 19.5–48.6 s greetings, 82–131 s tool turns. The first subprocess is what the user sees; the second one runs in parallel and steals from the first.

## Secondary findings (related)

- The coalition's content is inappropriate for the chat audience — it's hard-coded to the `incident_investigation` pattern (Levi's MDM demo seed). For a personal chat, the output is gibberish and is then *discarded* (the user never sees it). We are paying the full cost for a thrown-away response.
- The trigger threshold (`similarity >= 0.4` in `match_intent`) is loose. Anything tangentially financial/analytical clears it. False-positive rate is high enough to fire on most non-trivial messages.
- The `Triggering CoalitionWorkflow` log line at agent_router.py:329 *should* have appeared in `docker compose logs api` for these turns. It didn't. Either the api log buffer rolled (we saw ~3 hour gap in non-routine logs) or INFO-level routing logs are being swallowed somewhere. Worth a separate look later.

## Options to fix (sorted by ROI vs risk)

| # | Action | ms saved per turn (est) | Risk | Effort |
|---|--------|--------------------------|------|--------|
| **A** | **Disable the router auto-trigger.** Keep `@coalition` prefix and the explicit API endpoint as the only coalition entry points. One-line change at `agent_router.py:327-330` (delete the `if`-block). | **~50% of turn time** on matched intents (huge), 0 ms on unmatched | low — restores the explicit-only behavior the prefix already provides | <30 min |
| B | Tighten the trigger: only fire for `mutation: True` intents *and* require similarity ≥ 0.6. Keeps auto-trigger for genuine "build me an X" intents but drops the read-side false positives. | Modest — still pays cost when triggered | low | ~1 hr |
| C | Make coalition truly async: spawn it AFTER the user response is sent (in the post-chat workflow), so it can't contend with the foreground turn. | Eliminates contention but doubles compute usage / cost | medium | ~3 hr |
| D | Decommission router auto-trigger entirely until the A2A demo is being actively pitched, then re-enable behind a per-tenant feature flag. | Same as A but with a re-enable path | low | ~1 hr |

My recommendation: **A** for now, with a note in CLAUDE.md that A2A coalition is `@coalition`-only. If the Levi's demo needs auto-trigger, switch to D and gate it on a feature flag for the demo tenant.

## Independent reliability follow-ups (not blocking, but bundled would be cheap)

- `apps/api/app/workflows/activities/post_chat_memory_activities.py:47` — missing `resolve_primary_agent_slug` import. Every commitment-classified turn dies with NameError.
- `apps/api/app/memory/ingest.py:64` — `prop["name"]` on `KnowledgeEntity` object → TypeError. No chat-extracted entities are being recorded.
- `docker-compose.yml` — add `condition: service_healthy` on `api.depends_on.embedding-service` to fix the cold-start race that kills intent embedding init.

## Predicted impact

If we ship Option A and re-baseline on the same tenant:
- Greeting p50: 19.5 s → **~10 s** (no contention, but still 2× the April-10 number — there's more regression to find)
- Light recall p50: 35.4 s → **~18 s**
- Tool turn p50: 82–131 s → **~50–70 s**

That brings us back roughly halfway to the April-10 baseline. The remaining gap is Phase A.1 territory (need stage timers to know whether it's CLI spawn, MCP, recall, or LLM cost).
