# Higgsfield end-to-end validation — smoke test plan
**Date:** 2026-05-18
**Branch:** `feat/higgsfield-end-to-end-glue`
**Related PRs:** #550 (OAuth flow), #569 (code-worker CLI install), this PR (glue + migration 138 + intent)

## Scope

Validate that a user can connect Higgsfield via the `/integrations` panel and
then drive image / video generation from chat through Luna with **zero**
extra config.

The dispatch path proved out by code-read is:

```
chat ingress (apps/api/app/api/v1/chat.py:151 post_message)
  → chat_service.post_user_message
  → _generate_agentic_response (apps/api/app/services/chat.py:319)
  → agent_router.route_and_execute
  → cli_session_manager.generate_mcp_config (line 441)
      ↳ queries MCPServerConnector rows for tenant
      ↳ injects higgsfield connector w/ bearer-auth headers
  → resolve_tool_names(agent.tool_groups) → format_allowed_tools(...)
      ↳ for "higgsfield" group, emits `mcp__higgsfield__*` wildcard
  → ChatCliWorkflow (Temporal) → code-worker
  → CLI invokes `mcp__higgsfield__<tool>` via SSE+bearer
  → Higgsfield MCP server → Higgsfield API
  → asset URL returned in tool_result → chat renders inline
```

## Pre-conditions

| # | Check | How |
| - | ----- | --- |
| 1 | API is up | `curl -fsS http://localhost:8000/health` returns 200 |
| 2 | code-worker has the higgsfield CLI binary | `docker compose exec code-worker which higgsfield` returns a path (post #569 merge) |
| 3 | OAuth env vars set | `docker compose exec api env | grep HIGGSFIELD_OAUTH` shows `_CLIENT_ID`, `_CLIENT_SECRET`, `_REDIRECT_URI` |
| 4 | Migration 138 applied | `docker compose exec db psql -U postgres -d agentprovision -c "SELECT 1 FROM _migrations WHERE filename='138_luna_higgsfield_tool_group.sql'"` returns 1 row |
| 5 | Luna has `higgsfield` group | `... -c "SELECT name, tool_groups FROM agents WHERE name ILIKE 'luna%' LIMIT 3"` shows `"higgsfield"` in the array |
| 6 | User logged in to the web app | `localStorage.access_token` is fresh (≤30 min) |

## Steps

1. Open the web app, navigate to **/integrations**.
2. Locate the **Higgsfield** card. Click **Connect**.
3. Browser pops a new tab to `higgsfield.ai/oauth/authorize?...`. Authorize.
4. Copy the device code from the Higgsfield page, paste into the
   `IntegrationsPanel` input, click **Submit**.
5. Card flips green: `Connected with Higgsfield — Higgsfield MCP source
   registered for this tenant`.
6. Hit `GET /api/v1/mcp-servers` (browser DevTools or curl with the JWT) —
   expect a row `{ "name": "higgsfield", "transport": "sse", "enabled": true,
   "status": "connected" }`.
7. Open `/chat`. New chat session. Confirm the bound agent is Luna (default).
8. Send the message:
   > Generate an image of a forest at dawn with Higgsfield.
9. Wait 10–30s for the streaming response.
10. Expect: an `mcp__higgsfield__soul` (or whichever tool live-discovery
    surfaced) tool-call event in the SSE stream, then a tool_result with
    an HTTPS image URL, then a final assistant message that includes the
    image rendered inline via the existing markdown image renderer.

## Expected outcomes

* `GET /api/v1/chat/sessions/<id>/events` returns at least one
  `tool_call` event whose `name` starts with `mcp__higgsfield__`.
* `chat_messages` row for the assistant turn carries an `image_url`
  that resolves (200) and renders.
* `mcp_call_logs` (if enabled) shows one row for the tenant with
  `connector_name='higgsfield'`, `status='ok'`, latency < 30s.
* Higgsfield credit balance: 70 → 69 (1 credit per image, basic plan).

## Chrome DevTools — what to verify in the Network tab

| Request | Expected |
| ------- | -------- |
| `POST /api/v1/chat/sessions/<id>/messages` | 202, body `{ "queued": true }` |
| `GET /api/v2/sessions/<id>/events` (EventSource) | open and streaming SSE chunks |
| First chunk type `tool_call` | `{ "name": "mcp__higgsfield__soul", "args": { "prompt": "..." } }` |
| Subsequent `tool_result` | non-error, contains `https://*.higgsfield.ai/...` URL |
| Final `message` chunk | role=`assistant`, body has the image URL |

## Failure modes

| Symptom | Likely cause | Where to look |
| ------- | ------------ | ------------- |
| `POST /higgsfield-auth/start` → 503 | OAuth env vars unset on the api container | `docker compose logs api | grep HIGGSFIELD_OAUTH` |
| Card connects but tool never fires | Luna's tool_groups missing `higgsfield` (migration 138 not applied) | run check #4 above |
| Tool name visible to CLI but call returns 401 | Stored bearer token expired; no refresh worker yet | `mcp_call_logs.error_message`; manually disconnect + reconnect Higgsfield |
| Tool fires but returns `{"error": ...}` from Higgsfield | credits exhausted (70 → 0) or rate-limit | hit Higgsfield dashboard, or `higgsfield account status` from the host CLI |
| Image URL renders broken | URL is signed + expired before the user clicked; check `expires_in` query arg | curl the URL and look for 403 |
| MCP connection times out | `HIGGSFIELD_MCP_URL` env override needed because `_DEFAULT_MCP_URL` guess is wrong | `docker compose logs api | grep -i "mcp.*higgsfield"` |
| `mcp__higgsfield__*` allow-list entry missing from CLI invocation | Stale build of api — `format_allowed_tools` fix (commit 74898682 on this branch) not deployed yet | tail api logs for "Dispatching ChatCliWorkflow", inspect `--allowedTools` arg |

## Cannot verify without a live Higgsfield tenant

* Actual OAuth endpoints (`HIGGSFIELD_AUTH_URL` / `HIGGSFIELD_TOKEN_URL` defaults are educated guesses).
* Actual MCP server URL (`_DEFAULT_MCP_URL = "https://api.higgsfield.ai/mcp"` is a guess).
* Real tool names — `HIGGSFIELD_TOOL_NAMES` is the documented surface; first live `discover_mcp_tools` will surface the truth. The wildcard `mcp__higgsfield__*` allow-list survives any rename.
* Token refresh — the blob carries `refresh_token` but no scheduled refresh worker exists yet. First 401 → manual reconnect.

## Rollback

* If migration 138 misbehaves: `UPDATE agents SET tool_groups = tool_groups - 'higgsfield' WHERE name ILIKE 'luna%'; DELETE FROM _migrations WHERE filename='138_luna_higgsfield_tool_group.sql';`
* If `format_allowed_tools` fix regresses unrelated allow-list: revert commit `74898682`.
* If the intent for "generate image / video" steers chat traffic incorrectly: drop the new dict entry in `INTENT_DEFINITIONS` (last entry, embedding_service.py).
