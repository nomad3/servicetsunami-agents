# MCP Server Integration Guide

Connect any external MCP server to AgentProvision. Agents get access to all discovered tools automatically through Luna and the CLI orchestrator.

## How It Works

```
User message → Luna → CLI Orchestrator (Claude Code · Codex · Gemini · Copilot CLI)
                         ├── mcp__agentprovision__*  (built-in, 90+ tools)
                         ├── mcp__integral-sre__*    (external, 71 tools)
                         └── mcp__your-server__*     (external, N tools)
```

1. **Register** an MCP server via API or UI (`MCPServerConnector`)
2. **Discover** tools — AgentProvision connects via MCP protocol, performs handshake, caches tool list
3. **Inject** — `generate_mcp_config()` adds all enabled connectors to each CLI session
4. **Auto-approve** — `--allowedTools` includes `mcp__{server-key}__*` for all connected servers
5. **Call** — the CLI agent calls tools directly, or the proxy service calls them via JSON-RPC

## Supported Transports

| Transport | Protocol | When to use |
|-----------|----------|-------------|
| `sse` | MCP over Server-Sent Events | Most common. Standard MCP SDK `SseServerTransport`. |
| `streamable-http` | MCP over HTTP | Newer MCP SDK (1.2+). Synchronous request-response. |
| `stdio` | MCP over stdin/stdout | Local-only. Not supported for remote connectors (skipped in CLI injection). |

### SSE Protocol Flow

The SSE transport is asynchronous. AgentProvision implements the full handshake:

```
1. GET  /mcp/sse                          → SSE stream opens
2. SSE: event: endpoint
        data: /mcp/messages?session_id=xxx  ← server provides session URL
3. POST /mcp/messages?session_id=xxx       → initialize (JSON-RPC)
   SSE: event: message
        data: {capabilities, serverInfo}    ← server responds via stream
4. POST → notifications/initialized
5. POST → tools/list or tools/call          ← actual requests
   SSE: event: message
        data: {result: {tools: [...]}}      ← responses via stream
```

All JSON-RPC responses arrive through the SSE stream, not the HTTP response body (which returns `202 Accepted`).

## Adding an MCP Server

### Via API

```bash
# Create connector
curl -X POST /api/v1/mcp-servers \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "my-mcp-server",
    "description": "My custom MCP server with 10 tools",
    "server_url": "http://my-server:8080/mcp/sse",
    "transport": "sse",
    "auth_type": "none"
  }'

# Discover tools
curl -X POST /api/v1/mcp-servers/{connector_id}/discover \
  -H "Authorization: Bearer $TOKEN"

# Health check
curl -X POST /api/v1/mcp-servers/{connector_id}/health \
  -H "Authorization: Bearer $TOKEN"

# Call a tool directly (proxy)
curl -X POST /api/v1/mcp-servers/{connector_id}/call \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"tool_name": "my_tool", "arguments": {"param": "value"}}'
```

### Via Seed Script

```python
from app.models.mcp_server_connector import MCPServerConnector

connector = MCPServerConnector(
    tenant_id=tenant.id,
    name="my-mcp-server",           # becomes the MCP server key in CLI config
    server_url="http://host:8080/mcp/sse",
    transport="sse",                 # sse, streamable-http
    auth_type="none",                # none, bearer, api_key, basic
    status="connected",
    enabled=True,
)
db.add(connector)
```

## Authentication

| auth_type | auth_token value | Header sent |
|-----------|-----------------|-------------|
| `none` | — | No auth header |
| `bearer` | The token | `{auth_header or "Authorization"}: Bearer {token}` |
| `api_key` | The key | `{auth_header or "X-API-Key"}: {token}` |
| `basic` | Base64 credentials | `{auth_header or "Authorization"}: Basic {token}` |

`custom_headers` (JSON) is merged into every request for additional headers.

## CLI Session Injection

When a chat message is processed, `generate_mcp_config()` in `cli_session_manager.py` queries all connectors for the tenant where `enabled=True` and `status="connected"`, then builds the MCP config:

```json
{
  "mcpServers": {
    "agentprovision": {
      "type": "http",
      "url": "http://mcp-tools:8000/mcp",
      "headers": { "X-Internal-Key": "...", "X-Tenant-Id": "..." }
    },
    "integral-sre": {
      "type": "sse",
      "url": "http://host.docker.internal:8090/mcp/sse",
      "headers": {}
    }
  }
}
```

The server key is derived from the connector name (slugified: lowercase, spaces/underscores to hyphens).

The code-worker's `--allowedTools` flag dynamically includes `mcp__{key}__*` for every server in the config, so tool calls are auto-approved.

## API Reference

### Authenticated Endpoints (JWT required)

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/v1/mcp-servers` | List connectors for tenant |
| POST | `/api/v1/mcp-servers` | Create connector |
| GET | `/api/v1/mcp-servers/{id}` | Get connector details |
| PUT | `/api/v1/mcp-servers/{id}` | Update connector |
| DELETE | `/api/v1/mcp-servers/{id}` | Delete connector |
| POST | `/api/v1/mcp-servers/{id}/discover` | Discover tools (MCP handshake + tools/list) |
| POST | `/api/v1/mcp-servers/{id}/call` | Call a tool (proxied via JSON-RPC) |
| POST | `/api/v1/mcp-servers/{id}/health` | Health check (MCP initialize) |
| GET | `/api/v1/mcp-servers/{id}/logs` | View call logs |

### Internal Endpoints (no JWT, requires tenant_id query param)

Used by MCP tools and background workers:

| Method | Path | Description |
|--------|------|-------------|
| POST | `/api/v1/mcp-servers/internal/create` | Create connector |
| GET | `/api/v1/mcp-servers/internal/list?tenant_id=` | List connectors |
| POST | `/api/v1/mcp-servers/internal/{id}/discover` | Discover tools |
| POST | `/api/v1/mcp-servers/internal/{id}/call` | Call tool |
| POST | `/api/v1/mcp-servers/internal/{id}/health` | Health check |

## Data Model

### MCPServerConnector

| Column | Type | Description |
|--------|------|-------------|
| `id` | UUID | Primary key |
| `tenant_id` | UUID FK | Tenant isolation |
| `name` | String | Server name (used as CLI config key) |
| `server_url` | String | MCP server endpoint URL |
| `transport` | String | `sse`, `streamable-http`, `stdio` |
| `auth_type` | String | `none`, `bearer`, `api_key`, `basic` |
| `auth_token` | String | Credential value |
| `auth_header` | String | Custom header name (default: Authorization) |
| `custom_headers` | JSON | Additional HTTP headers |
| `tools_discovered` | JSON | Cached tool list from last discovery |
| `tool_count` | Integer | Number of discovered tools |
| `enabled` | Boolean | Active/inactive toggle (respected by CLI injection) |
| `status` | String | `pending`, `connected`, `error`, `disconnected` |
| `call_count` | Integer | Total successful calls |
| `error_count` | Integer | Total failed calls |

### MCPServerCallLog

| Column | Type | Description |
|--------|------|-------------|
| `tool_name` | String | Tool that was called |
| `arguments` | JSON | Arguments passed |
| `result` | JSON | Tool response |
| `success` | Boolean | Pass/fail |
| `error_message` | String | Error details if failed |
| `duration_ms` | Integer | Call duration |

## Building an MCP Server for AgentProvision

Any MCP server that implements the standard protocol works. Minimal example using the MCP Python SDK:

### 1. Create the server

```python
from mcp.server import Server
from mcp.types import Tool, TextContent

server = Server("my-server")

@server.list_tools()
async def list_tools():
    return [
        Tool(
            name="my_tool",
            description="Does something useful",
            inputSchema={
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "The query"},
                },
                "required": ["query"],
            },
        )
    ]

@server.call_tool()
async def call_tool(name, arguments):
    if name == "my_tool":
        result = do_something(arguments["query"])
        return [TextContent(type="text", text=result)]
```

### 2. Add SSE transport (for remote access)

```python
from mcp.server.sse import SseServerTransport
from starlette.applications import Starlette
from starlette.routing import Route, Mount

sse = SseServerTransport("/mcp/messages")

async def handle_sse(request):
    async with sse.connect_sse(request.scope, request.receive, request._send) as streams:
        await server.run(streams[0], streams[1], server.create_initialization_options())

app = Starlette(routes=[
    Mount("/mcp", routes=[
        Route("/sse", endpoint=handle_sse),
        Route("/messages", endpoint=sse.handle_post_message, methods=["POST"]),
    ]),
])

# Run: uvicorn my_server:app --port 8080
```

### 3. Register in AgentProvision

```bash
curl -X POST /api/v1/mcp-servers \
  -d '{"name":"my-server","server_url":"http://host:8080/mcp/sse","transport":"sse","auth_type":"none"}'
```

### 4. Discover and use

```bash
curl -X POST /api/v1/mcp-servers/{id}/discover
# → {"status":"connected","tool_count":1,"tools":[{"name":"my_tool",...}]}
```

The tools are now available to all agents for this tenant. Luna will use them automatically based on the skill prompts and user messages.

## Troubleshooting

| Symptom | Cause | Fix |
|---------|-------|-----|
| Discovery returns 0 tools, status "connected" | Missing MCP `initialize` handshake | Ensure server implements `initialize` method (standard in MCP SDK) |
| Discovery returns "HTTP 307" | Trailing slash redirect | AgentProvision follows redirects automatically since v1.0 |
| Tools discovered but CLI says "tool not approved" | `--allowedTools` missing the server prefix | Redeploy code-worker — it now auto-derives from MCP config |
| "Network unreachable" from container | `host.docker.internal` not resolving | Ensure Docker supports host.docker.internal (macOS/Windows default, Linux needs `--add-host`) |
| "Invalid session ID" on tool call | SSE session expired or closed | Each call opens a fresh SSE session — check server isn't rate-limiting |
| Tools work in proxy but not in CLI | CLI MCP config not injected | Check `generate_mcp_config()` runs with `db` param and connector is `enabled=True` + `status="connected"` |

## Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                    AgentProvision API                            │
│                                                                 │
│  ┌──────────────────┐  ┌─────────────────────────────────────┐  │
│  │  MCP Connectors  │  │  CLI Session Manager                │  │
│  │  Service          │  │  generate_mcp_config()             │  │
│  │  - discover()    │  │  - queries MCPServerConnector table │  │
│  │  - call_tool()   │  │  - injects into CLI MCP config     │  │
│  │  - health_check()│  │  - sets --allowedTools wildcards    │  │
│  └────────┬─────────┘  └──────────────┬──────────────────────┘  │
│           │                           │                          │
└───────────┼───────────────────────────┼──────────────────────────┘
            │ SSE JSON-RPC              │ MCP config JSON
            ▼                           ▼
┌──────────────────────┐  ┌──────────────────────────────────────┐
│  External MCP Server │  │  Code Worker (Claude Code CLI)       │
│  (e.g., Integral SRE)│  │  - reads MCP config                 │
│  - 71 tools          │  │  - connects to all MCP servers       │
│  - SSE transport     │  │  - auto-approves mcp__{key}__* tools │
│  - /mcp/sse endpoint │  │  - calls tools during conversation   │
└──────────────────────┘  └──────────────────────────────────────┘
```

## Key Files

| File | Purpose |
|------|---------|
| `apps/api/app/models/mcp_server_connector.py` | Data model (connector + call logs) |
| `apps/api/app/services/mcp_server_connectors.py` | Service layer (CRUD, SSE client, discover, call, health) |
| `apps/api/app/api/v1/mcp_server_connectors.py` | API routes (authenticated + internal) |
| `apps/api/app/services/cli_session_manager.py` | `generate_mcp_config()` — injects connectors into CLI sessions |
| `apps/code-worker/session_manager.py` | `_build_allowed_tools()` — derives tool approval wildcards from MCP config |
| `apps/code-worker/workflows.py` | `_build_allowed_tools_from_mcp()` — same for workflow-mode execution |
| `mcp-tools/src/mcp_tools/mcp_servers.py` | `call_mcp_tool` — MCP tool that proxies calls through connectors |
