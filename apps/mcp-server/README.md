# apps/mcp-server

FastMCP tool server. Serves **130+ tools across 26 modules** to CLI agent runtimes (Claude Code, Codex, Gemini, Copilot CLI) and the in-process local tool agent over MCP SSE.

For full architecture see [`../../CLAUDE.md`](../../CLAUDE.md).

## Modules

```
src/mcp_tools/
├── ads.py                  # Meta / Google / TikTok campaigns + ad libraries
├── agent_messaging.py      # A2A handoffs / coalition messaging
├── analytics.py            # calculate / compare periods / forecast
├── aremko.py               # Aremko-tenant business ops
├── calendar.py             # list events / create event
├── competitor.py           # competitor entities + reports
├── connectors.py           # data source queries
├── copilot_studio.py       # DirectLine agent proxy
├── data.py                 # SQL / datasets / schema / insights
├── devices.py              # IoT registry + camera
├── drive.py                # Google Drive search / read / create
├── dynamic_workflows.py    # Luna CRUD for workflows
├── email.py                # Gmail search / send / scan
├── github.py               # repos / issues / PRs (multi-account aware, #249)
├── jira.py                 # search / get / create / update issues
├── knowledge.py            # entity / relation / observation CRUD + search
├── mcp_servers.py          # external MCP server registry
├── memory_continuity.py    # cross-session recall
├── monitor.py              # inbox + competitor monitor control
├── reports.py              # document extraction / Excel generation
├── sales.py                # lead qualification / pipeline / proposals
├── shell.py                # execute commands / deploy
├── skills.py               # update_skill / update_agent / read_library_skill
├── supermarket.py          # tenant-specific
├── unsupervised_learning.py
└── webhooks.py
```

## Run locally

```bash
cd apps/mcp-server
pip install -e ".[dev]"
FASTMCP_PORT=8086 python -m src.server   # http://localhost:8086
```

`FASTMCP_PORT` defaults to `8000` if unset. The Helm chart pins it to `8086` (and the `mcp-tools` compose service inherits the same). Use `8086` locally to match.

Or via compose: the `mcp-tools` service.

## Test

```bash
pytest tests/ -v
```

## Auth

Every tool call requires both headers:

| Header | Purpose |
|--------|---------|
| `X-Internal-Key` | accepts `API_INTERNAL_KEY` or `MCP_API_KEY` |
| `X-Tenant-Id` | enforced inside each tool — calls fail without it |

Internal key rotation footgun: in `docker-compose.yml`, `environment:` overrides `env_file`. After rotating, recreate the service: `docker compose up -d --force-recreate mcp-tools`.

## Tenant isolation

Every tool receives `tenant_id` and **must** scope all reads/writes to that tenant. Returning data from another tenant is a multi-tenancy break.

```python
@mcp.tool()
async def my_tool(tenant_id: str, ...):
    db = get_db()
    rows = db.query(Model).filter(Model.tenant_id == tenant_id).all()
    ...
```

## Adding a tool

1. Pick the module that fits (or add a new one + register in `src/server.py`).
2. Define the tool with `@mcp.tool()` decorator. Type-hint params; FastMCP generates the JSON-Schema.
3. Always accept `tenant_id` first.
4. Calls into the API use `/api/v1/*/internal/*` endpoints with the `X-Internal-Key` header. As of #207 these are blocked from the public internet but still reachable in-cluster.
5. Add a test in `tests/`.

## Container image

Built from `Dockerfile`. Built and deployed via CI — don't build locally.
