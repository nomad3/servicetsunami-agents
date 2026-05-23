# Phase 4 Plan — CLI Orchestrator: Leaf-MCP Auth + Agent-Token

**Status:** 🔄 In implementation as of 2026-05-10 (sub-agent `acb63077` building 10 commits)
**Branch:** `feat/cli-orchestrator-phase-4-leaf-mcp-auth`
**Goal:** activate the §3.1 recursion gate and §10.3 leaf-inbound surface by minting agent-scoped JWTs at task dispatch, injecting them into leaf subprocesses, adding a third auth tier on the FastMCP server, and shipping the `dispatch_agent` / `request_human_approval` MCP tools plus `POST /api/v1/tasks/dispatch` endpoint.

> Plan-agent output (sub-agent `a4efcb55`) preserved verbatim below.

## Architecture

new `apps/api/app/services/agent_token.py` mint/verify helpers (HS256, signed with existing `SECRET_KEY`); third tier in `apps/mcp-server/src/mcp_auth.py` decoded by FastMCP request handler before existing tenant-id resolution; `dispatch_agent` MCP tool builds `parent_chain` from claims and POSTs to the new dispatch endpoint that the §3.1 gate will check; code-worker writes `.claude.json` + `.claude/hooks/{PreToolUse,PostToolUse}.sh` to leaf workdir + injects `AGENTPROVISION_*` env trio.

**Tech stack:** Python 3.11/3.14, FastAPI + python-jose (HS256 already used), FastMCP SSE transport (port 8086), Temporal Python SDK, SQLAlchemy 2.x, pytest.

## §1 — Recon findings (line-anchored)

**JWT plumbing today** (`apps/api/app/core/security.py:9-43`)
- `create_access_token(subject, expires_delta, additional_claims, iat)` mints HS256 with `settings.SECRET_KEY` + `ALGORITHM="HS256"`
- Decode side: `jose.jwt.decode(token, settings.SECRET_KEY, algorithms=[settings.ALGORITHM])` (`apps/api/app/api/deps.py:34-44`)
- `additional_claims` is free-form — agent claims (`kind`, `tenant_id`, `agent_id`, `task_id`, `parent_workflow_id`, `scope`, `parent_chain`) can ride this without a new mint helper. Still want a dedicated wrapper to centralise verify-side claim-shape validation.

**`agent_policy.allowed_tools` does NOT exist** *(historical — `agent_policies` deleted in P0b 2026-05-23)*
- *Historical recon:* the `agent_policies` table held `policy_type` ∈ {`output_filter`, `input_filter`, `data_access`, `rate_limit`} with a free-form `config` JSONB. **No `allowed_tools` field.** The original design (§8 step 1) said `scope: <agent_policy.allowed_tools as array>` — incorrect to schema even at the time.
- Actual allowed-tools set is computed from `Agent.tool_groups` (`apps/api/app/models/agent.py:30`, JSONB list of group names) → `tool_groups.resolve_tool_names()` (`apps/api/app/services/tool_groups.py:215-226`) → flat list of MCP tool names. This is what the chat hot path already uses to populate `--allowedTools`.
- **Decision (vindicated):** the agent-token `scope` claim is populated from `resolve_tool_names(agent.tool_groups)`; if `tool_groups is None`, scope is `None` meaning "all tools" (matches the established §3.1.1 invariant in `tool_groups.py:218-221`). Phase 4 correctly chose `tool_groups` over the (nonexistent) `agent_policies.allowed_tools` for the scope claim — a choice now permanent because P0b (2026-05-23) deleted the `agent_policies` table entirely after it shipped with zero enforcement and zero rows across 42 tenants. See `docs/plans/2026-05-23-p0b-agent-policy-decision.md`.

**`execution_trace` schema lacks `parent_task_id`** (`apps/api/app/models/execution_trace.py:23-28`)
- `task_id` exists (FK to `agent_tasks.id`), and `parent_step_id` exists (self-FK for nested steps within ONE trace tree). **No `parent_task_id` column** linking THIS trace row to a different agent_task that dispatched the agent producing this trace.
- `agent_tasks.parent_task_id` already exists (`apps/api/app/models/agent_task.py:39`) — it links subtasks to their dispatcher. **This is the source of truth.** The Phase 4 audit-linkage requirement is satisfied by:
  1. The `dispatch_agent` MCP tool creates a new `agent_tasks` row with `parent_task_id = <agent_token.task_id claim>`
  2. Every execution_trace inserted from a leaf MCP call uses `task_id = <agent_token.task_id claim>` — the trace tree's lineage is recoverable via `agent_tasks.parent_task_id`
- **Decision:** **no migration**. We do not add `parent_task_id` to `execution_trace`; the link goes through `agent_tasks.parent_task_id`. The design phrasing "execution_trace row contains parent_task_id" is satisfied transitively.

**MCP server auth shape** (`apps/mcp-server/src/mcp_auth.py:1-71`, `tool_audit.py:135-260`)
- Today: per-tool `_get_header(ctx, "X-Tenant-Id")` resolution, `verify_internal_key()` checks `X-Internal-Key`. **NO bearer-JWT decoding anywhere on the MCP server.**
- The audit middleware (`tool_audit.install_audit`) wraps `CallToolRequest` and resolves tenant inside the wrapper — this is the **correct seam** for the third tier: decode `Authorization: Bearer <jwt>` once before tenant resolution, populate per-call context dict, and let `resolve_tenant_id` consult `agent_token.tenant_id` first.
- **Decision:** new `apps/mcp-server/src/agent_token_verify.py` module. The tier discrimination lives in `mcp_auth.py` extended with `resolve_auth_context(ctx) -> AuthContext` returning a typed object with fields `(tier, tenant_id, agent_id, task_id, scope, parent_chain)`.

**MCP `/dispatch` endpoint does NOT exist** (`apps/api/app/api/v1/agent_tasks.py:22-285`)
- Only `POST /` (create), `POST /{id}/approve`, `POST /{id}/workflow-approve` exist. The §10.3(a) endpoint must be implemented as part of Phase 4.
- Router prefix is `/tasks` not `/agent-tasks` (`routes.py:115`). **The design's `/api/v1/agent-tasks/dispatch` is a notation slip — the real path becomes `POST /api/v1/tasks/dispatch`.**

## §2 — Decisions

**(D1) JWT mint location → new module `apps/api/app/services/agent_token.py`.** Not extending `core/security.py`. Different claim shape, different verification, different consumers. Reuses `settings.SECRET_KEY` + `ALGORITHM` (no new secret per SR-2).

**(D2) Scope claim source → `Agent.tool_groups` resolved via `tool_groups.resolve_tool_names()`.** Not `agent_policy.allowed_tools` (never existed; the `agent_policies` table itself was deleted in P0b 2026-05-23). When `tool_groups is None`, `scope` is `None` in the claim, meaning "no scope restriction".

**(D3) Auth-tier discrimination location → `mcp_auth.resolve_auth_context()`, called once per tool by the audit-wrapped handler.** Not a per-tool decorator. Existing call sites' `resolve_tenant_id(ctx)` becomes thin wrapper delegating to `resolve_auth_context().tenant_id`.

**(D4) Scope enforcement gate → centralised in audit handler.** When `tier == "agent_token"` AND tool name is not in `auth_context.scope` (and scope is not None), raise FastMCP 403-equivalent BEFORE dispatching. Individual tool modules need ZERO changes for scope enforcement.

**(D5) Tenancy-precedence audit-log → rate-limited via in-process LRU cache, TTL 60s, key `(tenant_id, agent_id, header_value)`.** Not infinite. Cache hits silently drop; misses write audit row.

**(D6) `.claude.json` write semantics → 0600 mode, into per-task workspace, deleted in `try/finally` at end of activity.** `os.open(path, O_CREAT|O_WRONLY|O_TRUNC, 0o600)` for umask-safe creation.

**(D7) Hook env var propagation surface → match `_run_long_command(extra_env=...)` shape.** Add `AGENTPROVISION_AGENT_TOKEN`, `AGENTPROVISION_TASK_ID`, `AGENTPROVISION_PARENT_WORKFLOW_ID`, `AGENTPROVISION_ALLOWED_TOOLS`, `AGENTPROVISION_API`. They merge into `os.environ` for the leaf subprocess and inherit into hook subprocesses naturally. Token leak to grand-children acknowledged — see SR-5.

**(D8) `parent_chain` JWT size budget → cap at MAX_FALLBACK_DEPTH (3) elements**, each a stringified UUID. ~112 chars. Scope claim ~20 tools × ~32 chars = ~700 bytes. Total claim payload ~1.5KB pre-base64; well under 8KB header limit.

**(D9) Recursion-gate wiring → at the dispatch endpoint, NOT inside the MCP tool.** `dispatch_agent` extracts `parent_chain` from agent_token claims, appends `agent_id`, POSTs to `/tasks/dispatch` with `parent_chain` in body. The dispatch endpoint constructs `ExecutionRequest(parent_chain=...)` and the §3.1 gate fires inside `ResilientExecutor.execute(req)`. Avoids duplicating gate logic.

## §3 — File tree + signatures

### New files (10)

```
apps/api/app/services/agent_token.py                 (~120 LOC)
apps/api/app/api/v1/internal_agent_tokens.py         (~50 LOC)  — internal mint endpoint for code-worker
apps/api/app/api/v1/internal_agent_heartbeat.py      (~40 LOC)  — POST /api/v1/agents/internal/heartbeat
apps/mcp-server/src/agent_token_verify.py            (~80 LOC)
apps/mcp-server/src/mcp_tools/agents.py              (~80 LOC)  — dispatch_agent + request_human_approval
apps/code-worker/hook_templates.py                   (~140 LOC)

apps/api/tests/services/test_agent_token.py          (~150 LOC)
apps/api/tests/api/v1/test_tasks_dispatch.py         (~200 LOC)
apps/api/tests/api/v1/test_agents_internal_heartbeat.py (~80 LOC)
apps/api/tests/cli_orchestrator/test_recursion_gate_dispatch.py (~120 LOC)
apps/mcp-server/tests/test_agent_token_auth.py       (~250 LOC)
apps/mcp-server/tests/test_dispatch_agent_tool.py    (~150 LOC)
apps/code-worker/tests/test_agent_token_injection.py (~180 LOC)
apps/code-worker/tests/test_hook_templates.py        (~120 LOC)
apps/api/tests/integration/test_phase4_ship_gate.py  (~250 LOC)
```

### Modified files (8)

```
apps/api/app/api/v1/agent_tasks.py             — add POST /dispatch route
apps/api/app/services/cli_session_manager.py   — call mint_agent_token() in run_agent_session, plumb through generate_mcp_config
apps/code-worker/workflows.py                  — call mint helper in execute_code_task; write .claude.json + hooks; extend extra_env
apps/mcp-server/src/mcp_auth.py                — add resolve_auth_context(); keep resolve_tenant_id() as compatibility shim
apps/mcp-server/src/tool_audit.py              — scope-enforcement gate before dispatch
apps/mcp-server/src/mcp_tools/__init__.py      — register agents tool module
apps/code-worker/cli_runtime.py                — extend run_cli_with_heartbeat docstring; no behaviour change
apps/api/app/api/v1/routes.py                  — mount new internal endpoints
```

### Key signatures

```python
# apps/api/app/services/agent_token.py
def mint_agent_token(
    *,
    tenant_id: uuid.UUID,
    agent_id: uuid.UUID,
    task_id: uuid.UUID,
    parent_workflow_id: str | None,
    scope: list[str] | None,           # None == no scope restriction
    parent_chain: tuple[str, ...] = (),
    heartbeat_timeout_seconds: int = 240,
) -> str: ...

class AgentTokenClaims(TypedDict):
    sub: str               # "agent:<agent_id>"
    kind: Literal["agent_token"]
    tenant_id: str
    agent_id: str
    task_id: str
    parent_workflow_id: str | None
    scope: list[str] | None
    parent_chain: list[str]
    iat: int
    exp: int

def verify_agent_token(token: str) -> AgentTokenClaims:
    """Decode + validate. Raises ValueError on bad shape, ExpiredSignatureError on expiry."""

# apps/mcp-server/src/agent_token_verify.py
class AuthContext(TypedDict, total=False):
    tier: Literal["agent_token", "tenant_jwt", "internal_key", "anonymous"]
    tenant_id: str | None
    agent_id: str | None
    task_id: str | None
    scope: list[str] | None
    parent_chain: list[str]
    tenancy_mismatch_logged: bool

def decode_agent_token_if_present(authorization_header: str | None) -> AuthContext | None: ...

# apps/code-worker/hook_templates.py
def render_pretooluse_hook() -> str: ...           # static template
def render_posttooluse_hook() -> str: ...          # static template
def write_claude_hooks(workdir: Path) -> None:     # writes both + sets +x
def write_claude_mcp_config(workdir: Path, agent_token: str, mcp_url: str) -> None:
    """Writes .claude.json with 0600 mode. Idempotent."""

# apps/api/app/api/v1/agent_tasks.py — new route
class DispatchRequest(BaseModel):
    task_type: Literal["code", "delegate"]
    objective: str
    target_agent_id: uuid.UUID | None = None
    repo: str | None = None
    branch: str | None = None
    parent_chain: list[uuid.UUID] = []     # populated by dispatch_agent MCP tool

@router.post("/dispatch", status_code=201)
async def dispatch_task(body, db, current_user) -> dict: ...
```

## §4 — Implementation order — 10 commits

1. `feat(agent-token): mint + verify primitives`
2. `feat(api): POST /tasks/dispatch endpoint`
3. `feat(api): wire mint_agent_token into chat hot path`
4. `feat(code-worker): hook templates + .claude.json injection`
5. `feat(api): internal agent-token mint endpoint + worker wiring`
6. `feat(mcp-server): third auth tier resolver`
7. `feat(mcp-server): scope enforcement at audit boundary`
8. `feat(mcp-server): dispatch_agent + request_human_approval MCP tools + heartbeat endpoint`
9. `feat(api): activate §3.1 recursion gate via dispatch endpoint`
10. `feat(cli-orchestrator): Phase 4 ship-gate integration test + docs`

Each commit is its own logical unit. Tests-first per TDD.

## §5 — Self-review (12 SR items)

### Critical (would block merge)

**SR-1 — Audit linkage claim was wrong; corrected.**
The brief says "every MCP call from a leaf writes an `execution_trace` row with `parent_task_id`". But `execution_trace` has no `parent_task_id`; it has `task_id` (FK to agent_tasks) and `parent_step_id` (intra-trace nesting). My initial draft proposed migration 122. **Resolution:** `agent_tasks.parent_task_id` ALREADY exists; setting `execution_trace.task_id = claim.task_id` walks back to the parent via `agent_tasks.parent_task_id`. **No migration needed.**

**SR-2 — JWT secret rotation.**
Concern: introducing a new secret without rotation plumbing. **Resolution:** `agent_token.py` reuses `settings.SECRET_KEY`. Rotation: in-flight agent tokens fail verify and the leaf falls back to no-op (token `exp=480s` so blast radius bounded). Acceptable; SECRET_KEY rotation already requires a controlled deploy window.

### Important (must fix before ship)

**SR-3 — JWT size budget.** parent_chain hard-capped at MAX_FALLBACK_DEPTH=3. scope capped implicitly by tool_groups dictionary (~50 tools max). At ~1.5 KB the claim is well under any header limit. Unit test asserts mint output < 4 KB.

**SR-4 — `.claude.json` file mode.** 0600 mode via `os.open(path, O_CREAT|O_WRONLY|O_TRUNC, 0o600)`. Test asserts `os.stat(path).st_mode & 0o777 == 0o600`.

**SR-5 — Token leak via grand-children processes.** Acknowledged — token bounded to `exp=480s`. PostToolUse hook intentionally inherits the env. Doc note in `hook_templates.py`. **No mitigation in Phase 4.** Phase 5 could swap env-var for unix-socket auth proxy.

**SR-6 — Tenancy-mismatch audit-log volume.** In-process LRU cache TTL 60s, keyed by `(tenant_id, agent_id, header_value)`. Bounded at 1024 entries. Test: mock 100 mismatches in 1s, assert exactly 1 audit row.

### Minor (note for follow-up)

**SR-7 — Recursion-gate firing site.** Initial draft put gate inside the MCP tool. Wrong layer — gate is on the EXECUTOR. MCP tool just propagates `parent_chain`; executor's existing `_check_recursion_gate` does the actual reject.

**SR-8 — Heartbeat endpoint deferral.** §10.3(c) `POST /api/v1/agents/internal/heartbeat` was unlisted in user's brief but required for PostToolUse hook to do anything useful. **Resolution:** include as part of commit 8 (sibling).

**SR-9 — `apps/mcp-server/src/mcp_tools/agents.py` does NOT exist.** Confirmed by `ls`. New file — not a modification.

**SR-10 — `dispatch_agent` MCP tool calling `/tasks/dispatch` introduces a circular dependency at deploy time.** MCP server (8086) → API (8000) → Temporal → code-worker → leaf → MCP. This is an existing pattern (every MCP tool already calls API via `_internal()`). No new circularity.

**SR-11 — `kind` claim discriminator.** Without a `kind` field, the verify path can't distinguish user vs agent tokens. Add explicit assertion in `verify_agent_token` that `sub.startswith("agent:")` AND `kind == "agent_token"`.

**SR-12 — Test scaffolding for cross-cutting integration.** Phase 4 integration test (commit 10) uses `TestClient` for api (in-process), mocks FastMCP transport, stubs Temporal client. End-to-end exercise lives in `scripts/e2e_test_production.sh` post-merge.

## Plan deviations from the brief (3, all justified)

1. **Endpoint path** is `/api/v1/tasks/dispatch` not `/api/v1/agent-tasks/dispatch` (router prefix `/tasks` per `routes.py:115`)
2. **`scope` claim source** is `Agent.tool_groups` resolved via `tool_groups.resolve_tool_names()`, not `agent_policy.allowed_tools` which does not exist
3. **No execution_trace migration** — audit linkage walks through existing `agent_tasks.parent_task_id`

These corrections were necessary; the brief's wording reflected the design doc, which had the same schema slip.

## Outcome

To be appended after Phase 4 implementation + independent review + final review + merge. Implementation in flight as of 2026-05-10.
