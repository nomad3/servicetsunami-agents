# Integral + AgentProvision Integration Design

**Date:** 2026-03-30
**Status:** Approved
**Scope:** New tenant onboarding, agent skills, MCP connectivity, Jenkins/Nexus tools, on-premise deployment

## Overview

Integrate Integral's existing SRE Control Plane (51 MCP tools, ChromaDB knowledge base, 455 servers across 6 datacenters) with AgentProvision's orchestration layer. Deploy AgentProvision on-premise inside Integral's private network. Create three specialized agents orchestrated by Luna, with new Jenkins and Nexus MCP tools built in the SRE project.

## Architecture

### Deployment Topology

On-premise deployment inside Integral's private network (e.g., mvfxiadp45 or dedicated host):

```
Integral Private Network
├── AgentProvision Stack (Docker Compose)
│   ├── api (FastAPI, port 8001)
│   ├── web (React, port 8002)
│   ├── db (PostgreSQL + pgvector, port 8003)
│   ├── mcp-tools (FastMCP, port 8087) — unchanged, no new tools here
│   ├── code-worker (Claude Code CLI via Temporal)
│   ├── temporal (port 7233)
│   └── ollama (Gemma 4 scoring, port 11434)
│
├── Integral SRE Stack (already running)
│   ├── control-plane-api (port 8080) ← MCP connector target
│   ├── control-plane-web (port 5173)
│   └── ChromaDB (321K+ docs knowledge base)
│
├── Jenkins Instances (internal)
│   ├── nyjenkin.integral.com (NY4)
│   ├── ldnjenkin.integral.com (LD4)
│   ├── sgjenkin.integral.com (SG)
│   ├── tyojenkin.integral.com (TY3)
│   └── uatjenkin.integral.com (UAT)
│
└── Nexus Registry
    ├── nexus.sca.dc.integral.net:8081 (push)
    └── nexus.integral.com:8081 (pull)
```

### MCP Connectivity (Hybrid Approach)

- **SRE tools (51 existing + 14 new Jenkins/Nexus):** Built natively in `infra-control-plane-center`. AgentProvision connects via `MCPServerConnector` (HTTP transport, internal network).
- **AgentProvision FastMCP (81 tools):** Unchanged. No new tools added here.
- **Discovery:** AgentProvision's `MCPServerConnector.discover_tools()` sends JSON-RPC `tools/list` to SRE server, automatically discovers all 65 tools.

### Networking

Both stacks on shared Docker network or host networking. SRE MCP server reachable at `http://control-plane-api:8080` (shared network) or `http://localhost:8080` (host networking). All traffic stays inside private network.

- Claude Code CLI requires outbound internet (Anthropic API).
- Ollama runs fully local — no outbound needed.

## Agent Hierarchy

Tenant: **Integral** — AgentKit type: `hierarchy`, Luna as supervisor.

```
Luna (Supervisor / Entry Point)
├── integral-sre (Technical Support)
├── integral-devops (Release Operations)
└── integral-business-support (Operations Intelligence)
```

All agents are **skill definitions** (skill.md files), not hardcoded logic. The CLI orchestrator (Claude Code / Codex CLI / Gemini CLI) reads the skill prompt and has access to the MCP tools via the session's MCP config.

### Agent Skill Definitions

#### 1. integral-sre (Technical Support)

**File:** `apps/api/app/skills/native/integral-sre/skill.md` (top-level under `native/` — required by `SkillManager.scan()` which only iterates one level deep)
**MCP tools:** All 65 SRE tools via remote connector
**Role:** Infrastructure monitoring, alert investigation, incident triage, SSH operations, runbook execution
**Personality:** Technical, concise, SRE vocabulary
**Autonomy:** Full for read-only ops, supervised for SSH commands

#### 2. integral-devops (Release Operations)

**File:** `apps/api/app/skills/native/integral-devops/skill.md`
**MCP tools:** Jenkins + Nexus tools (subset of 65 SRE tools via remote connector)
**Role:** CI/CD pipeline management, build triggering, deployment orchestration, artifact management, release checklists
**Personality:** Process-oriented, safety-conscious, confirms before destructive actions
**Autonomy:** Supervised (builds require confirmation before trigger)

#### 3. integral-business-support (Operations Intelligence)

**File:** `apps/api/app/skills/native/integral-business-support/skill.md`
**MCP tools:** All 65 SRE tools via remote connector (full read access)
**Role:** Transaction tracing, alert translation, system health for non-technical users, self-service troubleshooting
**Personality:** Business-friendly, forex domain language, translates technical data into business impact, no jargon
**Autonomy:** Full (all read-only)

**Note on platform selection:** Platform affinity is NOT set per-skill (the `FileSkill` schema does not parse it). Platform is controlled by `TenantFeatures.default_cli_platform` at the tenant level. Integral's tenant will default to `claude_code`.

**Special capability — Forex Transaction Trace:**

The agent follows this trace path when investigating failed/delayed transactions:

1. **Client → FIX Session** — `check_fix_session` — Is the FIX session connected? Any reconnects?
2. **FIX → Matching Engine** — `check_latency_metrics` — Latency between client and matching engine?
3. **Matching → LP** — `check_lp_status` — Is the Liquidity Provider reachable? Quoting?
4. **LP → Execution** — `query_opentsdb` — FXCloudWatch execution metrics, fill rates, rejects?
5. **Execution → Settlement** — `check_server_health` + `correlate_alerts` — Settlement service health, any related alerts?

At each step, the agent translates findings into business language:
> "Transaction delayed at step 3 — Liquidity Provider CityFX is showing 340ms latency (normally 12ms), likely causing the fill delay."

### Agent Routing

**Current routing flow:** The agent slug is resolved from the chat session's AgentKit config (`chat.py`). If not set, defaults to "luna" via `CHANNEL_AGENT_MAP`. The existing `_TASK_TYPE_KEYWORDS` maps keywords to task types for RL context only — it does NOT influence agent selection.

**New behavior:** Add a **sub-agent routing** step AFTER the primary agent slug is resolved. When the resolved agent is Luna (supervisor) and the tenant has specialist agents configured, Luna's skill prompt includes instructions to delegate to the appropriate sub-agent based on the message content. This is NOT keyword routing in `agent_router.py` — it's **prompt-level delegation** within Luna's skill body.

Luna's skill prompt for Integral includes:
```
When you receive a message, determine which specialist to delegate to:
- Infrastructure/monitoring/alerts/SSH → use integral-sre tools
- Build/deploy/release/Jenkins/Nexus → use integral-devops tools
- Transaction tracing/business impact/FIX/LP → use integral-business-support tools
- General questions → handle directly
```

This approach:
- Requires **zero changes** to `agent_router.py` — no new routing dimension
- Leverages the CLI's native intelligence to pick the right tool subset
- Coexists cleanly with RL policy routing (RL picks the platform, Luna picks the sub-context)
- All specialist "agents" share the same MCP tools (SRE server) — the differentiation is in the prompt, not the tool access

## Jenkins & Nexus MCP Tools (SRE Project)

**Location:** `infra-control-plane-center` — new handler files following existing patterns.

### New Files in SRE Project

- `src/integral_mcp_server/handlers/jenkins.py` — 8 tools
- `src/integral_mcp_server/handlers/nexus.py` — 6 tools
- Tool definitions added to `src/integral_mcp_server/legacy_tools.py`
- Handler routing added to `src/integral_mcp_server/mcp_server.py`

### Authentication

Configured in SRE server's `.env`:

```bash
JENKINS_API_USER=<service-account>
JENKINS_API_TOKEN=<api-token-from-ldap-user>
JENKINS_URLS_NY4=http://nyjenkin.integral.com
JENKINS_URLS_LD4=http://ldnjenkin.integral.com
JENKINS_URLS_SG=http://sgjenkin.integral.com
JENKINS_URLS_TY3=http://tyojenkin.integral.com
JENKINS_URLS_UAT=http://uatjenkin.integral.com
NEXUS_URL=nexus.sca.dc.integral.net:8081
NEXUS_API_USER=<service-account>
NEXUS_API_TOKEN=<token>
```

### Jenkins Tools (8)

| Tool | HTTP | Endpoint | Description |
|---|---|---|---|
| `list_jenkins_jobs` | GET | `/api/json` | List jobs with status, folder navigation |
| `get_jenkins_job_status` | GET | `/job/{name}/api/json` | Last build result, duration, health |
| `trigger_jenkins_build` | POST | `/job/{name}/build` | Trigger with parameters, returns queue URL |
| `get_jenkins_build_log` | GET | `/job/{name}/{build}/consoleText` | Console output, tail support for large logs |
| `get_jenkins_build_artifacts` | GET | `/job/{name}/{build}/api/json` | List artifacts with download URLs |
| `abort_jenkins_build` | POST | `/job/{name}/{build}/stop` | Cancel running build |
| `list_jenkins_pipelines` | GET | `/api/json?tree=jobs[...]` | Multibranch pipeline views, nested folders |
| `get_jenkins_queue` | GET | `/queue/api/json` | Queued builds, wait reasons |

All Jenkins tools accept a `region` parameter (NY4, LD4, SG, TY3, UAT) that resolves to the correct Jenkins URL from env config.

### Nexus Tools (6)

| Tool | HTTP | Endpoint | Description |
|---|---|---|---|
| `search_nexus_artifacts` | GET | `/service/rest/v1/search` | Search by name, group, version, format |
| `get_nexus_artifact_info` | GET | `/service/rest/v1/components/{id}` | Metadata, checksums, upload date, size |
| `list_nexus_repositories` | GET | `/service/rest/v1/repositories` | All repos with type, format, health |
| `get_nexus_component_versions` | GET | `/service/rest/v1/search?name={name}` | All versions, sorted by date |
| `promote_nexus_artifact` | POST | `/service/rest/v1/staging/move` | Move from snapshots to releases |
| `check_nexus_health` | GET | `/service/rest/v1/status` | Storage, repo health, blob store stats |

## AgentProvision Platform Changes

### What Changes

1. **3 agent skill files** — `apps/api/app/skills/native/integral-{sre,devops,business-support}/skill.md` (top-level under `native/`, one level deep for scanner compatibility)

2. **CLI session MCP config injection** — `apps/api/app/services/cli_session_manager.py`:
   - `generate_mcp_config()` is currently static (only includes built-in ServiceTsunami MCP server). Must be extended to:
     - Accept `db: Session` and `tenant_id: UUID` parameters
     - Query `MCPServerConnector` entries for the tenant
     - For each connected server, add an entry to the MCP config with the server's URL and transport type
     - For auth: if `auth_type` is `bearer` or `api_key`, inject the token into the MCP config's `headers` field
     - For Integral's case (`auth_type: "none"`, internal network), no auth headers needed
   - The SRE MCP server appears as an additional MCP server entry alongside the built-in one. The CLI can then call SRE tools directly (lower latency than proxying through `call_mcp_tool`).
   - **Observability trade-off:** Direct injection bypasses `MCPServerConnector.call_tool()` logging. Accept this for Phase 1; can add CLI-side telemetry later if needed.
   - Callers of `generate_mcp_config()` in `cli_session_manager.py` must pass the new params. The code-worker receives the serialized config as workflow input — no changes needed there.

3. **One-time seed script** — `apps/api/scripts/seed_integral_tenant.py` — creates tenant, admin user, AgentKit, MCPServerConnector, integration credentials. Run once and discard.

4. **No changes to `agent_router.py`** — routing delegation handled in Luna's skill prompt (see Agent Routing section above).

### What Does NOT Change

- FastMCP server (`mcp-tools/`) — untouched
- Database schema — no new tables or migrations
- Web frontend — existing agent/chat UI works as-is
- Code worker — same CLI execution path
- Temporal workflows — existing `ChatCliWorkflow` handles orchestration

### Seed Script Creates

- Tenant: "Integral"
- Admin user: configurable email/password
- TenantFeatures: `default_cli_platform: "claude_code"`
- AgentKit: Luna supervisor, `kit_type: "hierarchy"`, 3 agent skills linked
- MCPServerConnector: `name: "integral-sre"`, `server_url: "http://control-plane-api:8080"`, `transport: "streamable-http"`, `auth_type: "none"` (internal network)
- 3 Agent records with skills attached

## ChromaDB Knowledge Base Integration

The SRE project's ChromaDB (321K+ docs across 9 collections) is already exposed via SRE MCP tools — `search_knowledge`, `unified_search`, `lookup_server_info`, `search_ops_messages`, `search_inventory`, etc. AgentProvision agents access this data through the remote MCP connector — no separate ChromaDB integration needed. The SRE MCP server is the abstraction layer.

No data sync to AgentProvision's pgvector is planned. The two knowledge stores serve different purposes:
- **ChromaDB (SRE):** Infrastructure operational knowledge (alerts, runbooks, server configs, ops history)
- **pgvector (AgentProvision):** Business entities, relations, observations, memory activities

## On-Premise Deployment Considerations

- Replace Cloudflare Tunnel with internal DNS/reverse proxy
- Ollama models (gemma4, gemma4, gemma4) total ~3GB and run on CPU. GPU not required but improves scoring latency (~2s CPU vs ~200ms GPU for 1.5b model).
- Claude Code CLI needs outbound internet for Anthropic API
- All other traffic stays inside private network

### Docker Networking

The two Docker Compose stacks (AgentProvision and SRE) need a shared external Docker network:

```yaml
# In both docker-compose files:
networks:
  integral-internal:
    external: true

# Create once: docker network create integral-internal
```

AgentProvision's MCPServerConnector URL: `http://control-plane-api:8080` (using container name resolution on shared network).

### Phase 1 Authentication

Admin creates user accounts via seed script. Multiple Integral users can have individual accounts for proper audit trails. No shared/generic accounts.

### Deferred (Phase 2)

- SSO/LDAP integration for AgentProvision login
- High availability / multi-node deployment
- Monitoring AgentProvision via Integral's existing Prometheus
- Dynamic Workflows for complex release orchestration (multi-agent)
- CLI-side telemetry for MCP tool call observability (bypassed by direct injection)

## Implementation Scope

### SRE Project (`infra-control-plane-center`)
- 2 new handler files (jenkins.py, nexus.py)
- 14 new tool definitions in legacy_tools.py
- Handler routing in mcp_server.py
- Environment variable additions

### AgentProvision Project (`servicetsunami-agents`)
- 3 skill.md files (under `apps/api/app/skills/native/`)
- CLI session manager: extend `generate_mcp_config()` to inject tenant's MCPServerConnector entries
- One-time seed script (`apps/api/scripts/seed_integral_tenant.py`)
