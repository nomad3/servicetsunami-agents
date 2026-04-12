# Memory + RL Pipeline Fixes

**Date:** 2026-04-11
**Status:** Phase A in progress
**Author:** Live testing session

## Background

After running multi-turn coherent conversations through the chat UI (Aremko booking flow + HITL launch email task), DB inspection found that the entity extraction step is healthy but four downstream pipelines are broken or degraded.

### Symptoms observed (last 4 hours, both test tenants)

| Signal | Count | Status |
|---|---|---|
| `knowledge_entities` | 126 | working but **duplicated** |
| `knowledge_observations` | 126, all with embeddings | healthy |
| `knowledge_relations` | 33 | healthy |
| `memory_activities` (entity_created/observation_created/relation_created) | 120/120/13 | logged |
| `conversation_episodes` | **0** | episode summarization not running |
| `rl_experiences` (chat_response / response_generation) | **0** | chat-response RL never logged |
| `rl_experiences` (workflow_step / agent_routing) | 32 / 21 | logged, **all reward NULL** |

### Bugs catalogued

1. **`MemoryEvent` constructor mismatch** at `apps/api/app/workflows/activities/post_chat_memory_activities.py:112` — raises `TypeError: MemoryEvent.__init__() missing 4 required positional arguments: 'tenant_id', 'occurred_at', 'ingested_at', 'kind'` on every chat turn. The `update_world_state` activity is failing.
2. **Entity dedup broken** — same entity re-created every turn (Carla 8x, Puerto Varas 6x, AgentVoice 11x).
3. **Garbage entity from prompt leak** — tenant UUID extracted as a `concept` because system metadata leaks into the Gemma extraction prompt.
4. **Gemma JSON parse failures on markdown-fenced output** — `Failed to parse Gemma 4 knowledge extraction: Expecting ',' delimiter… Raw result: \`\`\`json …`. The parser doesn't strip fences.
5. **No `chat_response` RL experiences** — auto-quality scorer is wired into `chat.py:466` (calls `auto_quality_scorer.score_and_log_async`) and writes `decision_point="response_generation"`, but zero rows of either name exist. Likely silent exception inside the async thread.
6. **All RL experiences have `reward = NULL`** — only `auto_quality_scorer.assign_reward()` writes rewards. Workflow_step and agent_routing writers don't pass reward and nothing backfills them. This is partially by design for those decision points (rewards come from policy gradient updates), but the chat-response path is fully broken (see #5).
7. **`conversation_episodes` empty** — `embed_and_store_episode()` exists in `apps/api/app/workflows/activities/episode_activities.py`, dispatched as a child workflow from PostChatMemoryWorkflow via `maybe_trigger_episode()`. `IdleEpisodeScanWorkflow` is registered in `orchestration_worker.py:221` but no launcher ever starts it. The PostChatMemoryWorkflow path is also blocked by bug #1.

## Plan

### Phase A — Surgical fixes (small, independent)

#### A1. MemoryEvent constructor mismatch
**File:** `apps/api/app/workflows/activities/post_chat_memory_activities.py:112`
**Change:** Add the four missing required args.
```python
event = MemoryEvent(
    tenant_id=UUID(tenant_id),
    source_type="chat",
    source_id=user_message_id,
    occurred_at=datetime.utcnow(),
    ingested_at=datetime.utcnow(),
    kind="text",
    text=content,
    proposed_entities=raw_result.get("entities", []),
    proposed_observations=raw_result.get("observations", []),
    proposed_relations=raw_result.get("relations", []),
    proposed_commitments=[],
    confidence=0.9,
)
```
Also import `datetime` at top if missing. Sweep file for any other `MemoryEvent(...)` constructions and apply the same fix.

**Validation:** orchestration-worker logs no longer show the TypeError after a chat turn.

#### A2. Strip markdown code fences from Gemma JSON parser
**File:** `apps/api/app/services/knowledge_extraction.py` (the `extract_from_content` parser path).
**Change:** Before `json.loads(text)`, strip optional `\`\`\`json` and `\`\`\`` fences.
```python
text = text.strip()
if text.startswith("```"):
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```$", "", text)
```

**Validation:** orchestration-worker logs lose the *"Failed to parse Gemma 4 knowledge extraction"* warnings; new entities appear from previously-failing turns.

#### A3. Strip system metadata before Gemma extraction
**File:** Same `knowledge_extraction.py`, in the function that builds `content` for Gemma.
**Change:** Remove tenant headers / UUIDs / "tenant_id:" lines from the input. Easiest: only feed the user message + assistant message text, never the framing metadata.

**Validation:** No more `concept | <uuid>` rows. Run a chat turn, query `knowledge_entities WHERE name ~ '^[0-9a-f]{8}-'` — should be 0.

### Phase B — Dedup + RL writer (medium)

#### B1. Entity upsert (dedup)
**File:** `apps/api/app/memory/ingest.py` (`ingest_events` → entity insert path).
**Change:** Before inserting, SELECT by `(tenant_id, lower(name), entity_type)` and either:
- update existing row's `last_seen_at`, merge `attributes`, increment `mention_count`, OR
- skip insert if identical.

Keep observations as append-only (every observation is a new fact), but link them to the deduped entity.

DB-level safeguard: add a partial unique index `CREATE UNIQUE INDEX CONCURRENTLY ... ON knowledge_entities (tenant_id, lower(name), entity_type)` so races can't double-insert. Migration in `apps/api/migrations/`.

**Validation:** Run a 4-turn chat. Before fix: 4 rows for "Carla". After fix: 1 row for "Carla", 4+ observations linking to it.

#### B2. Investigate auto_quality_scorer silent failure
The Explore agent confirmed `chat.py:466` calls `auto_quality_scorer.score_and_log_async`, and the writer uses `decision_point="response_generation"`. But `rl_experiences` shows zero of those. Two possibilities:
- The async thread is throwing an unhandled exception that's swallowed.
- The thread is being killed before it writes (uvicorn worker recycled, or call returns before persistence).

**Action:**
1. Read `auto_quality_scorer.py` `_score_and_log` and look for try/except that swallows errors.
2. Add `logger.exception(...)` inside that catch if missing.
3. Run one chat turn; check API logs for the exception.
4. Fix root cause (likely a Gemma JSON parse failure of its own, or a missing column).

**Validation:** After a chat turn, `SELECT decision_point, COUNT(*) FROM rl_experiences WHERE created_at > now() - interval '5 min'` shows ≥1 `response_generation` row.

#### B3. Reward assignment for non-chat experiences
Currently only `auto_quality_scorer.assign_reward()` writes rewards. `workflow_step` and `agent_routing` writers don't pass reward and nothing backfills.

**Choice:** Either accept those decision points will have NULL until policy updates compute them lazily, OR add a periodic reward backfill job. **Recommendation: do nothing** — by design for the RL system (rewards come from policy gradient updates, not direct scoring). Document and move on.

**Validation:** After B2, new `response_generation` rows have non-NULL rewards. workflow_step / agent_routing remain NULL — expected.

### Phase C — Episode summarization

#### C1. Verify Phase A1 unblocks the post-chat workflow
After A1, run a chat turn and check `PostChatMemoryWorkflow` runs to completion in Temporal. If `maybe_trigger_episode` returns `should_trigger=true`, the child `EpisodeWorkflow` should fire and write a row to `conversation_episodes`.

#### C2. Read `maybe_trigger_episode` threshold
**File:** `apps/api/app/workflows/activities/post_chat_memory_activities.py` (the activity that decides whether to dispatch).
Current threshold may require N messages or M minutes idle. If too high (e.g. 20 turns), it'll never trigger in normal sessions. Lower to ~6 turns OR add an "always trigger on session close" path.

**Validation:** Run a 6-turn conversation, see exactly 1 row land in `conversation_episodes` after the 6th turn.

#### C3. Bootstrap `IdleEpisodeScanWorkflow`
The workflow is registered in `orchestration_worker.py:221` but nothing ever launches it. Need a one-shot starter:
- API startup hook calls `client.start_workflow("IdleEpisodeScanWorkflow", id="idle-episode-scan-singleton", ...)` with `WorkflowIDReusePolicy.REJECT_DUPLICATE` so it's idempotent.
- OR a Temporal Schedule (cron) defined in the same startup path.

**File:** `apps/api/app/main.py` (startup hook) or new `apps/api/app/workflows/bootstrap.py`.
**Validation:** `temporal workflow list` shows `idle-episode-scan-singleton` running. After leaving a session idle for an hour, an episode row appears.

### Phase D — Validation harness

After all fixes deploy:
1. Register a fresh tenant (clean slate).
2. Connect Gmail + Gemini CLI.
3. Run a 6-turn booking conversation through chat UI.
4. Run a 6-turn HITL email task.
5. Query in one go:
```sql
SELECT 'entities' k, COUNT(*) c FROM knowledge_entities WHERE tenant_id = $1
UNION ALL SELECT 'observations', COUNT(*) FROM knowledge_observations WHERE tenant_id = $1
UNION ALL SELECT 'relations', COUNT(*) FROM knowledge_relations WHERE tenant_id = $1
UNION ALL SELECT 'episodes', COUNT(*) FROM conversation_episodes WHERE tenant_id = $1
UNION ALL SELECT 'response_gen RL', COUNT(*) FROM rl_experiences
  WHERE tenant_id = $1 AND decision_point = 'response_generation'
UNION ALL SELECT 'response_gen RL with reward', COUNT(reward) FROM rl_experiences
  WHERE tenant_id = $1 AND decision_point = 'response_generation';
```
6. Confirm: entities deduped, observations populated, ≥1 episode, ≥1 chat-response RL row with non-NULL reward, no UUID-as-entity, no orchestration-worker errors.

## Execution order

- **Day 1 (small):** A1 → A2 → A3 → deploy → re-test memory pipeline. Likely unblocks half the symptoms by itself.
- **Day 2 (medium):** B1 (dedup with migration) → B2 (debug auto-scorer silent failure) → deploy.
- **Day 3 (cleanup):** C1/C2/C3 if not already resolved by A1 → deploy → run Phase D harness → ship.

## Open questions

- Does PostChatMemoryWorkflow continue past `update_world_state` failure or abort the whole workflow? Need to check Temporal workflow definition. If it aborts, A1 alone should resolve C2.
- Does `assign_reward` actually exist in `rl_experience_service.py` (Explore agent claims line 63-80)? Need to verify before B2.
