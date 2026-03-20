# Codex CLI Integration Plan

> Goal: integrate Codex into the existing CLI orchestrator so ServiceTsunami can run agent chat through OpenAI Codex using the same tenant-scoped integration pattern already used for Claude Code.

## Objective

Reuse the current orchestration shape:

`Chat Service -> Agent Router -> CLI Session Manager -> Temporal -> worker -> CLI binary -> MCP tools`

The Codex integration should:

- follow the existing Integrations page pattern
- store per-tenant credentials in `integration_configs` + credential vault
- use the tenant's ChatGPT/Codex subscription authentication, not shared platform credentials
- support web chat and WhatsApp the same way Claude Code does
- remain feature-flagged through `tenant_features.default_cli_platform`

## Current Baseline

The platform already has the core pattern for Claude Code:

- integration registry entry: `claude_code`
- tenant feature flag: `default_cli_platform = "claude_code"`
- chat routing: `chat.py -> agent_router.py -> cli_session_manager.py`
- Temporal execution: `ChatCliWorkflow` on `servicetsunami-code`
- worker fetches tenant token from internal API
- worker invokes `claude -p ... --output-format json --mcp-config ...`

Codex should be added as the second CLI platform, not as a separate architecture.

## Planning Assumptions

These are the assumptions this plan is built on:

1. Codex authentication will be tenant-owned and subscription-backed, analogous to Claude Code.
2. The stored credential will be treated as a vault secret under a Codex integration, and the primary artifact is the contents of `~/.codex/auth.json`, including headless/device-auth flows.
3. The local `codex` CLI supports:
   - non-interactive execution via `codex exec`
   - device auth via `codex login --device-auth`
   - API-key login as a fallback via `codex login --with-api-key`
4. MCP support may differ from Claude Code. If native MCP is not sufficient for our use case, we add a Codex adapter layer in the worker without changing the tenant-facing integration contract.

## Target Product Shape

### Tenant-facing

Add one new integration card:

- `codex`
- display name: `Codex`
- description: `Connect your ChatGPT / Codex subscription for AI agent chat`

The initial UX should mirror Claude Code:

- connect credential in Integrations
- set `default_cli_platform` to `codex` for a tenant
- chat requests route to Codex instead of Claude Code

### Runtime-facing

Generalize the current Claude-only path into a platform adapter model:

- `claude_code`
- `codex`

Both platforms should share:

- skill selection
- tenant memory injection
- MCP config generation
- Temporal dispatch
- credential-vault lookup

The platform-specific parts should be isolated:

- CLI command construction
- auth environment variables / credential materialization
- output parsing
- session continuity behavior

## Implementation Phases

### Phase 1: Integration Registry + Credential Contract

Add Codex to the integration registry following the Claude Code pattern.

Files:

- `apps/api/app/api/v1/integration_configs.py`
- `apps/web/src/components/IntegrationsPanel.js`

Plan:

1. Add `codex` integration entry to `INTEGRATION_CREDENTIAL_SCHEMAS`.
2. Use a single secret field initially:
   - `auth_json`
   - label: `ChatGPT Auth JSON`
3. Add help text that explains the credential comes from the tenant's own ChatGPT/Codex login flow.
4. Add Codex brand color/icon treatment in the integrations panel.

Decision:

- Use `auth_json` as the tenant-facing key, but accept legacy `session_token` values at runtime for backward compatibility.

### Phase 2: Tenant Feature Flag Support

Extend platform selection to include Codex.

Files:

- `apps/api/app/models/tenant_features.py`
- `apps/api/app/schemas/tenant_features.py`
- any frontend settings page that edits `default_cli_platform`

Plan:

1. Allow `default_cli_platform = "codex"`.
2. Keep `claude_code` as the default for existing tenants until Codex is validated.
3. Add Codex as an explicit selectable platform in the UI if platform selection is exposed.

Decision:

- Codex is opt-in first, not the default.

### Phase 3: Generalize CLI Session Manager

Refactor `cli_session_manager.py` from a Claude-only implementation into a provider-driven runner.

Files:

- `apps/api/app/services/cli_session_manager.py`
- `apps/api/app/services/agent_router.py`

Plan:

1. Introduce a platform adapter map, for example:
   - integration name
   - binary name
   - auth strategy
   - invocation builder
   - output parser
2. Replace `_get_claude_code_token()` with a generic credential fetcher:
   - `_get_cli_platform_token(db, tenant_id, integration_name)`
3. Split current Claude-only helpers into generic + provider-specific helpers:
   - generic instruction generation
   - generic MCP config generation
   - generic memory context assembly
   - provider-specific command/env building
4. Update `run_agent_session()` to accept `platform`.
5. Update `agent_router.py` so:
   - `claude_code` -> Claude adapter
   - `codex` -> Codex adapter

Decision:

- Do not duplicate `run_agent_session()` into a second Codex-only codepath.

### Phase 4: Worker Support for Codex Execution

Extend the existing worker to run Codex on the same Temporal queue.

Files:

- `apps/code-worker/workflows.py`
- `apps/code-worker/worker.py`
- `apps/code-worker/Dockerfile`

Plan:

1. Keep the same worker service and Temporal queue for now.
2. Add Codex execution activity support in the worker.
3. Install the Codex CLI in the worker image alongside Claude Code.
4. Add provider-specific execution branches:
   - `claude` command path
   - `codex exec` command path
5. Add a generic workflow input that includes:
   - `platform`
   - `tenant_id`
   - `message`
   - `instruction content`
   - `mcp config`
   - optional image payload
   - optional session identifier

Decision:

- Prefer renaming the workflow input/result to platform-neutral names now if the refactor is still fresh.
- Keep the app directory as `apps/code-worker/` initially to avoid deployment churn.

### Phase 5: Codex Auth Materialization

Codex auth needs to be executed headlessly inside the worker from tenant vault credentials.

Files:

- `apps/code-worker/workflows.py`
- `apps/api/app/api/v1/oauth.py` if internal token responses need extension

Plan:

1. Add internal credential fetch support for `codex`.
2. Decide the exact runtime materialization strategy:
   - direct env var injection if Codex supports it
   - login bootstrap into a temp config dir if Codex requires persisted auth state
3. Keep the vault contract tenant-specific and symmetric with Claude Code.

Recommended path:

1. Support `codex login` on developer machines and `codex login --device-auth` for headless machines.
2. Copy the resulting `~/.codex/auth.json` into the tenant vault.
3. Generate a tenant-scoped local auth store in the worker temp session dir before invoking `codex exec`.

Decision:

- The plan should assume device-auth-backed tenant credentials are stored in the vault and transformed into the format the CLI expects at runtime.

### Phase 6: MCP Compatibility Layer

Codex must be able to use the same ServiceTsunami MCP tools.

Files:

- `apps/api/app/services/cli_session_manager.py`
- `apps/code-worker/workflows.py`
- possibly `apps/mcp-server/` only if a compatibility shim is required

Plan:

1. Test whether Codex can consume the generated MCP config directly.
2. If yes, reuse the existing MCP path unchanged.
3. If no, add a worker-side adapter that translates ServiceTsunami tool access into whatever Codex expects.

Decision:

- Preserve the MCP server as the single tool backend.
- Do not fork tool implementations by platform.

### Phase 7: UI and Product Wiring

Expose Codex cleanly in the app.

Files:

- `apps/web/src/components/IntegrationsPanel.js`
- settings / platform selectors if present
- any workflow or landing content that lists supported CLI platforms

Plan:

1. Add Codex card next to Claude Code.
2. Add status badges consistent with the existing integration pattern.
3. If platform selection is tenant-admin configurable, add `Codex`.
4. Update product copy from "Claude Code only" to "Claude Code + Codex" where appropriate.

### Phase 8: Observability and Failure Handling

Codex should emit the same level of operational detail as Claude Code.

Files:

- `apps/api/app/services/cli_session_manager.py`
- `apps/code-worker/workflows.py`
- any logging/metrics surfaces

Plan:

1. Include `platform=codex` in all routing and workflow logs.
2. Standardize metadata returned from worker to API:
   - `platform`
   - `model`
   - `input_tokens`
   - `output_tokens`
   - `error`
   - `session_id` if applicable
3. Make tenant-facing failures actionable:
   - Codex not connected
   - token expired
   - CLI missing
   - MCP connection failed

### Phase 9: Tests

Add focused tests instead of relying on manual chat checks.

Files:

- `apps/api/tests/`
- worker tests under `apps/code-worker/` if present or to be created

Plan:

1. Registry test: `codex` appears in integration registry.
2. Router test: `default_cli_platform="codex"` dispatches Codex path.
3. Credential lookup test: tenant token fetched from vault for `codex`.
4. Worker command-construction test: Codex invocation contains expected args.
5. Error-path tests:
   - missing Codex credential
   - malformed auth material
   - Codex CLI failure
   - MCP config failure

## Concrete Task Breakdown

### Task 1: Add Codex integration card

Modify:

- `apps/api/app/api/v1/integration_configs.py`
- `apps/web/src/components/IntegrationsPanel.js`

Deliverable:

- `codex` appears on Integrations with `auth_json`-based credential storage

### Task 2: Add Codex as a valid CLI platform

Modify:

- `apps/api/app/models/tenant_features.py`
- `apps/api/app/schemas/tenant_features.py`
- relevant UI settings surfaces

Deliverable:

- tenants can select Codex as their CLI platform

### Task 3: Refactor CLI session manager into provider adapters

Modify:

- `apps/api/app/services/cli_session_manager.py`
- `apps/api/app/services/agent_router.py`

Deliverable:

- one generic `run_agent_session()` path with provider-specific adapters

### Task 4: Extend code-worker for Codex execution

Modify:

- `apps/code-worker/workflows.py`
- `apps/code-worker/worker.py`
- `apps/code-worker/Dockerfile`

Deliverable:

- worker can execute either Claude Code or Codex based on workflow input

### Task 5: Add Codex credential fetch + runtime auth bootstrap

Modify:

- worker token fetch logic
- internal token endpoint only if needed

Deliverable:

- worker can fetch tenant Codex credential and authenticate headlessly

### Task 6: Validate MCP interoperability

Modify if needed:

- `apps/api/app/services/cli_session_manager.py`
- `apps/code-worker/workflows.py`

Deliverable:

- Codex can use the same ServiceTsunami MCP tools

### Task 7: Add tests and rollout guardrails

Modify:

- `apps/api/tests/`
- worker tests

Deliverable:

- Codex path has basic automated coverage before rollout

## Rollout Strategy

### Stage 1: Local only

- connect Codex credential locally
- set one tenant to `default_cli_platform = "codex"`
- verify web chat
- verify WhatsApp
- verify MCP tool access

### Stage 2: Internal tenant

- enable Codex for one internal tenant only
- compare response quality, tool reliability, and latency against Claude Code

### Stage 3: Limited beta

- expose Codex card broadly
- keep Claude Code as default
- let specific tenants opt in

## Open Decisions

These need explicit confirmation during implementation:

1. Integration name:
   - preferred: `codex`
   - alternative: `codex_cli`

2. Credential key:
   - preferred: `auth_json`
   - runtime compatibility: also accept legacy `session_token`

3. Worker naming:
   - keep `code-worker` as the shared CLI worker now
   - rename later only if it becomes multi-provider enough to justify churn

4. MCP support:
   - direct support is best
   - adapter fallback is acceptable

## Recommended Decisions

To keep this implementation low-risk and aligned with the current platform:

- use integration name `codex`
- store the tenant credential under `auth_json`
- keep `apps/code-worker/` and `ChatCliWorkflow` but generalize their inputs
- keep Claude Code as the default platform initially
- add Codex as an opt-in platform behind the existing tenant feature control

## Acceptance Criteria

The Codex plan is complete when all of the following are true:

1. Tenant can connect Codex from Integrations.
2. Tenant can switch `default_cli_platform` to Codex.
3. Chat requests route to Codex through the same orchestration pipeline.
4. Codex can use ServiceTsunami MCP tools with tenant scoping.
5. Worker authenticates using tenant-owned subscription credentials.
6. Failures are visible and actionable.
7. Claude Code continues to work unchanged.
