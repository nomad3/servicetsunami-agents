# Codex MCP Transport Mismatch — Research

**Date:** 2026-05-16
**Status:** Research only, no code changes
**Predecessor:** `docs/plans/2026-05-16-codex-mcp-tool-access-fix.md` (PR #516, rmcp opt-in)
**Symptom:** Codex agent in saguilera tenant connects to MCP server, gets HTTP 405, `rmcp::transport::worker` dies with `UnexpectedContentType(Some("text/plain; charset=utf-8; body: Method Not Allowed"))`.

---

## TL;DR

PR #516 added `experimental_use_rmcp_client = true` correctly. That flag now activates the rmcp client, but the rmcp client **does not have an SSE transport at all** — it only has `Stdio` and `StreamableHttp`. The `transport = "sse"` TOML key is **silently ignored** by serde (untagged enum, field-based discrimination), so the URL gets dispatched through the `StreamableHttp` transport, which POSTs JSON-RPC to the configured URL. Our FastMCP `/sse` Starlette route is `methods=["GET"]` only, so it returns 405, and the rmcp worker tears down the connection.

Diagnosis is **option A** from the brief (with a twist): switch the MCP server to expose streamable-http on a separate URL/path and point Codex at it. Claude Code and Gemini stay on the existing `/sse` endpoint untouched.

**No upstream Codex changes required.** Fix is entirely server-side + one URL change in our Codex config emitter.

---

## 1. What Codex's rmcp client actually speaks

### 1.1 Transport schema (upstream source of truth)

`codex-rs/config/src/mcp_types.rs` (openai/codex@main):

```rust
#[derive(Serialize, Deserialize, Debug, Clone, PartialEq, JsonSchema)]
#[serde(untagged, deny_unknown_fields, rename_all = "snake_case")]
pub enum McpServerTransportConfig {
    Stdio {
        command: String,
        #[serde(default)] args: Vec<String>,
        ...
    },
    StreamableHttp {
        url: String,
        #[serde(default, skip_serializing_if = "Option::is_none")]
        bearer_token_env_var: Option<String>,
        #[serde(default, skip_serializing_if = "Option::is_none")]
        http_headers: Option<HashMap<String, String>>,
        ...
    },
}
```

Key facts:
- **No `Sse` variant exists.** Only `Stdio` and `StreamableHttp`.
- `#[serde(untagged)]` → no `transport` discriminator field. Variant is chosen by **field presence**: `command` → Stdio; `url` → StreamableHttp.
- `#[serde(deny_unknown_fields)]` *on the variants* — but the `transport = "sse"` key is at the `McpServerConfig` parent level, which uses `#[serde(flatten)]` for the transport. So `transport` gets flattened into the untagged enum and rejected by `deny_unknown_fields` on whichever variant it tries. In practice the parser still succeeds because the variant picker hits `StreamableHttp { url, http_headers, ... }` first — and any extra unrecognized key like `transport` triggers an error path, BUT serde's `untagged` tries each variant silently and discards failures. Net effect: `transport = "sse"` is **silently dropped or causes the entry to be dropped**.
- Default when only `url` is present: **`StreamableHttp`**. The rmcp client then POSTs JSON-RPC bodies directly to that URL.

### 1.2 What StreamableHttp does on the wire

`modelcontextprotocol/rust-sdk` crate `rmcp::transport::streamable_http_client::StreamableHttpClientTransport`:
- POSTs JSON-RPC messages **directly to the configured URI**. No endpoint discovery (no `endpoint` SSE event consumed).
- GETs the same URI expecting `text/event-stream` for the server-push stream.
- DELETEs the URI for session cleanup.
- No fallback to legacy SSE transport.
- Error variant: `UnexpectedContentType` — matches the observed log line exactly.

### 1.3 Codex CLI version in the container

```
$ docker exec agentprovision-agents-code-worker-1 codex --version
codex-cli 0.130.0
```

This version is well past the rmcp client introduction. The Dockerfile pulls `@openai/codex` unpinned (`npm install -g`) — for reproducibility we should pin it, but the version that's deployed today is the right one and the rmcp flag is honored.

### 1.4 Live `~/.codex/config.toml` on the worker

```toml
experimental_use_rmcp_client = true

[projects."/workspace"]
trust_level = "trusted"

[projects."/home/codeworker/st_sessions/.../"]
trust_level = "trusted"

[mcp_servers.agentprovision]
transport = "sse"                                # ← INERT
url = "http://mcp-tools:8086/sse"                # ← drives StreamableHttp
http_headers = { "X-Internal-Key" = "...", "X-Tenant-Id" = "...", "X-User-Id" = "..." }
```

The `transport = "sse"` line is cosmetic / inert. The `url` ending in `/sse` is what determines behavior — rmcp's `StreamableHttp` will POST JSON-RPC to `http://mcp-tools:8086/sse`.

---

## 2. What our FastMCP server actually exposes

`apps/mcp-server/src/mcp_serve.py:18` runs `mcp.run(transport="sse")` against `mcp==1.27.1` (installed in `agentprovision-agents-mcp-tools-1`).

FastMCP's `sse_app()` (path: `/usr/local/lib/python3.11/site-packages/mcp/server/fastmcp/server.py`, lines 818-940):

```python
routes.append(
    Route(
        self.settings.sse_path,          # default "/sse"
        endpoint=sse_endpoint,
        methods=["GET"],                 # ← GET only
    )
)
routes.append(
    Mount(
        self.settings.message_path,      # default "/messages/"
        app=sse.handle_post_message,
    )
)
```

The legacy SSE transport uses **two separate paths**: `GET /sse` for the event stream + `POST /messages/?session_id=...` for client→server messages. The session-bound message URL is dispatched to the client via an SSE `endpoint` event at connection time.

The streamable-http app is a different ASGI tree, served at `/mcp` by default, that accepts POST + GET on a single path.

### Live probe of our running server

```
POST /sse        → 405  (Method Not Allowed)     ← exactly the observed error
GET  /sse        → 200  (event stream)
POST /mcp        → 404  (streamable-http not mounted)
GET  /messages/  → 400  (missing session_id; route exists)
```

`POST /mcp = 404` is the key. **We are not exposing the streamable-http endpoint today**, only the legacy SSE pair.

---

## 3. Why Claude and Gemini work but Codex doesn't

| Client | Config field | Transport implementation |
|---|---|---|
| Claude Code | `"type": "sse"` in `mcp.json` | Legacy SSE: `GET /sse` for stream + reads `endpoint` event + POSTs to `/messages/?session_id=...` |
| Gemini CLI | `"type": "sse"` in `settings.json` | Same legacy SSE pattern |
| Codex (rmcp_client) | `url` only — `transport = "sse"` ignored | Streamable-HTTP: POSTs JSON-RPC directly to the configured URL |

Claude/Gemini follow the legacy SSE handshake (which our FastMCP server supports). Codex's rmcp client only knows streamable-http, so it just POSTs to whatever URL it was given. That URL happens to be `/sse`, which only accepts GET → 405 → rmcp worker dies.

---

## 4. Fix options ranked

### Option A — Recommended: dual-transport on the mcp-tools server

Run BOTH the legacy SSE app (current) AND the streamable-http app, then point Codex at the streamable-http URL.

**Server change (`apps/mcp-server/src/mcp_serve.py`)** — ~10 lines:

```python
import uvicorn
from starlette.applications import Starlette
from starlette.routing import Mount

import src.mcp_tools  # noqa: F401
from src.mcp_app import mcp
from src.tool_audit import install_audit

install_audit(mcp)

if __name__ == "__main__":
    # Mount BOTH transports so legacy SSE clients (Claude, Gemini) keep
    # working while rmcp/streamable-http clients (Codex 0.20+) get a
    # native endpoint.
    app = Starlette(
        routes=[
            Mount("/", app=mcp.sse_app()),               # /sse + /messages/
            Mount("/mcp", app=mcp.streamable_http_app()), # /mcp/ (POST+GET)
        ]
    )
    uvicorn.run(app, host="0.0.0.0", port=8086)
```

(Exact mount nesting needs verification — `sse_app()` already returns a full Starlette app with its own routes. Two possibilities: (a) nest both under one parent Starlette with disjoint mount paths; (b) bind sse_app() at `/` and add a single Route for streamable-http at `/mcp`. Either works.)

**Client change (`apps/code-worker/workflows.py::_codex_mcp_config_lines`)** — ~3 lines:

When emitting the Codex TOML, override the URL when the source is SSE-style: rewrite `http://mcp-tools:8086/sse` → `http://mcp-tools:8086/mcp` (or accept a separate `codex_url` field in the shared MCP config). Also stop emitting the inert `transport = "sse"` line — or change it to `transport = "streamable_http"` purely for human readability (it's still ignored by serde).

**Replicate to:** `helm/`, `terraform/`, `apps/mcp-server/Dockerfile` (no port changes needed; same 8086).

**Pros:**
- One server process, one port. No new service.
- Legacy clients (Claude, Gemini) untouched.
- Future-proof: any new rmcp-based client (any future Codex, any Anthropic-MCP-CLI tooling) gets a clean endpoint.

**Cons:**
- Requires verifying that `streamable_http_app()` and `sse_app()` can coexist in one FastMCP instance. The session managers are independent (different `StreamableHTTPSessionManager` instance vs. the SSE in-memory dict), so they should not collide. The `mcp.run(...)` path explicitly only runs ONE — we have to bypass that and build the Starlette app ourselves (shown above).

**Complexity:** ~10 lines server-side, ~3 lines client-side, zero upstream changes. **This is the right answer.**

### Option B — Two ports (rejected)

Run a second `mcp_serve_streamable.py` on port 8087. Doubles container processes for no architectural benefit. Skip.

### Option C — Stdio bridge for Codex (rejected)

Install a Node-based `mcp-remote` stdio→HTTP bridge in the code-worker image and emit:

```toml
[mcp_servers.agentprovision]
command = "npx"
args = ["-y", "mcp-remote", "http://mcp-tools:8086/sse"]
```

Pros: Codex never has to learn streamable-http; bridge handles the legacy SSE handshake. Works without server changes.

Cons: per-session subprocess, extra failure mode, npm-pinning hell in code-worker image, no way to forward agent-scoped JWT through subprocess env easily without exposing it on the command line. Operationally worse than Option A. Skip unless A turns out to be infeasible.

### Option D — Find a magic config flag (rejected)

There is none. We read the upstream source. `McpServerTransportConfig` literally has no SSE variant. Confirmed.

### Option E — Drop the `/sse` suffix (rejected)

If we configure `url = "http://mcp-tools:8086"`, the rmcp client will POST to `/`, which FastMCP's SSE app doesn't expose at all → 404. Doesn't help.

---

## 5. Recommended fix — concrete file-level edits

| File | Change | LOC |
|---|---|---|
| `apps/mcp-server/src/mcp_serve.py` | Replace `mcp.run(transport="sse")` with a hand-built Starlette app mounting both `sse_app()` and `streamable_http_app()` | ~10 |
| `apps/code-worker/workflows.py::_codex_mcp_config_lines` | Rewrite the URL when emitting Codex TOML: if URL ends in `/sse`, swap to `/mcp/` (or accept a per-CLI URL override in the shared MCP config builder). Drop or relabel the inert `transport = "sse"` line. | ~5 |
| `apps/mcp-server/Dockerfile` | No change (same port) | 0 |
| `apps/code-worker/Dockerfile` | Pin `@openai/codex@0.130.0` for reproducibility (orthogonal hygiene, not required for this fix) | 1 |
| `helm/values.yaml` + `helm/templates/mcp-tools.yaml` | No port changes; if probes reference `/sse` keep them; optionally add a `/mcp/` liveness alongside | 0–2 |
| `terraform/` | No infra change | 0 |
| `apps/code-worker/tests/test_workflows_helpers.py` | Update `TestCodexMcpConfigLines` to assert the URL gets rewritten and the inert key is gone | ~10 |

**Total:** ~30 lines across 3 files, no new service, no upstream changes.

---

## 6. Risk + rollback

- **Blast radius:** If `streamable_http_app()` cannot coexist with `sse_app()` (unlikely, but unverified in this environment), only the streamable-http endpoint fails — legacy SSE clients keep working. Codex remains broken until rolled back, same state as today. Recoverable.
- **Rollback:** revert the mcp-tools change; revert the workflows URL rewrite. Codex returns to today's broken state, Claude/Gemini unaffected.
- **Auth headers:** `http_headers` is forwarded by rmcp's `StreamableHttpClientTransport` on every POST. `mcp_auth.py` middleware must run on the streamable-http path the same way it runs on `/sse`. Verify by mounting `mcp_auth`'s middleware on the parent Starlette app, not on either sub-app, so both transports go through the same auth gate.
- **Header bleed on legacy clients:** none — Claude/Gemini's URL stays `/sse`; only Codex's emitted URL changes.

---

## 7. Open verification before merge

1. Spike: confirm `Starlette(routes=[Mount("/", mcp.sse_app()), Mount("/mcp", mcp.streamable_http_app())])` actually serves both. Easiest: build mcp-tools image locally, curl both endpoints.
2. Confirm `mcp_auth.py` middleware applies to both mounts. Today it's wrapped inside the SSE-app's middleware stack — may need to lift it to the parent app.
3. After rollout, `docker exec` into the worker mid-chat-turn and verify the emitted TOML uses `/mcp/`, not `/sse`.
4. Watch `mcp_tool_calls` table for tool fires from the Codex tenant — same SQL probe as in `2026-05-16-codex-mcp-tool-access-fix.md` §4.5.

---

## 8. Summary

| Question | Answer |
|---|---|
| Does Codex honor `transport = "sse"`? | No. Untagged enum, no SSE variant, key is silently dropped. |
| Default transport when only `url` is given? | `StreamableHttp` (rmcp client). |
| Where does the 405 come from? | FastMCP's `Route("/sse", methods=["GET"])` rejects rmcp's POST. Reproduced live. |
| Does our server expose streamable-http? | No. Only legacy SSE (`/sse` + `/messages/`). `POST /mcp` returns 404 today. |
| Fix shape | Option A: mount streamable-http alongside SSE on the same server, swap Codex's URL to `/mcp/`. |
| Complexity | ~30 LOC across 3 files. No new service. No upstream patch. |
| Upstream Codex changes? | None. |
