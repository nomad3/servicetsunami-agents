# Dynamic Workflows — Visual Builder, Migration & Full RL Lifecycle

> A tree-based visual workflow builder, full migration of 20 static workflows to dynamic JSON, Luna as the conversational simple mode, and full lifecycle RL wiring across creation, execution, and optimization.

**Date:** 2026-04-03
**Status:** Design
**Depends on:** `2026-03-21-dynamic-workflows-design.md`, `2026-03-21-dynamic-workflows-implementation-plan.md`
**Backend status:** Phase 1-3 complete (models, executor, API, MCP tools, templates)
**Frontend status:** Basic CRUD list only — no visual builder yet

---

## 1. Problem

The dynamic workflows backend is fully operational: JSON definitions, Temporal executor with 8 step types, 13 API endpoints, 6 MCP tools for Luna, 5 native templates. But:

- **No visual builder** — users can't create or edit workflow steps without writing JSON
- **20 static Python workflows** still hardcoded — they don't appear in the dynamic system, can't be edited by users, and require code deploys to modify (excludes `DynamicWorkflowExecutor` itself)
- **Luna can create workflows** via MCP tools, but the frontend has no way to visualize, edit, or debug them
- **No execution visualization** — run history is a flat list, no tree view, no step-level audit
- **No RL lifecycle** — workflow runs aren't scored, creation patterns aren't tracked, no optimization suggestions

## 2. Design Decisions

| Decision | Choice | Rationale |
|----------|--------|-----------|
| Builder type | ReactFlow tree canvas (top-to-bottom) | Workflows are trees with branches, not flat lists |
| Simple mode | Luna conversational (no simplified builder UI) | If you open the builder, you want full power |
| Migration scope | All 20 static workflows | Single execution engine, no dual maintenance |
| Luna CRUD depth | Full — create, edit, delete, run, debug | Two equal paths to the same data |
| RL tracking | Full lifecycle — run + step + creation patterns | Already have RL infra, maximize learning |

---

## 3. Visual Builder Architecture

### 3.1 Component Tree

```
WorkflowBuilder (main container)
├── BuilderToolbar
│   ├── Workflow name (editable)
│   ├── Status badge (draft/active/paused)
│   ├── Integration pill ("Integrations: 3/4" — expandable)
│   ├── JSON toggle button
│   ├── Test button
│   ├── Save button
│   └── Activate button
├── StepPalette (left sidebar, collapsible)
│   ├── Triggers (cron, webhook, event, manual)
│   ├── Tools (81+ MCP tools, grouped by category)
│   ├── Agents (Luna, Code, Data, etc.)
│   ├── Logic (condition, for_each, parallel)
│   ├── Flow (wait, human_approval, webhook_trigger)
│   └── Sub-workflows
├── WorkflowCanvas (ReactFlow, dagre auto-layout)
│   ├── TriggerNode (root)
│   ├── StepNode (mcp_tool, agent, transform)
│   ├── ConditionNode (diamond, then/else branches)
│   ├── ForEachNode (contains sub-tree)
│   ├── ParallelNode (fans out, merge node below)
│   └── ApprovalNode (human approval with state)
├── StepInspector (right panel, appears on node selection)
│   ├── ToolPicker (searchable, 81+ tools)
│   ├── AgentPicker (Luna, Code, Data)
│   ├── ParamEditor (dynamic fields from tool schema)
│   ├── VariableBrowser (insert {{step.output}} references)
│   ├── ExpressionBuilder (for conditions)
│   └── IntegrationStatus (required service + connected/disconnected)
└── TestConsole (bottom panel)
    ├── Step-by-step dry-run results
    ├── Per-step input/output/duration/cost
    └── Error display with suggestions
```

### 3.2 Key Interactions

- **Drag** from palette onto canvas to add a step
- **Click** a node to open the inspector
- **Drag edges** between nodes to connect them
- **Right-click** node for context menu (delete, duplicate, wrap in for_each, wrap in condition)
- **Auto-layout** (dagre algorithm via ReactFlow) keeps the tree tidy
- **JSON toggle** in toolbar to see/edit raw definition directly
- **Undo/redo** via Ctrl+Z / Ctrl+Shift+Z (canvas state history)

### 3.3 Data Flow & State Management

**Source of truth**: The `WorkflowDefinition` JSON schema is the canonical source. ReactFlow nodes/edges are a derived view.

**Adapter layer** (`workflowAdapter.js`): Translates between the two formats:
- `definitionToFlow(definition)` — converts nested JSON (with `for_each.steps[]`, `parallel.steps[]`) into flat ReactFlow nodes + edges. Nested structures become **group nodes** containing their child nodes.
- `flowToDefinition(nodes, edges)` — reconstructs the nested JSON from the flat node/edge graph. Nodes inside a group node become sub-steps of the parent's `steps[]` array.
- This adapter is the single place where the two formats are reconciled.

**State flow:**
- User edits on canvas -> update ReactFlow state -> adapter converts to JSON -> stored in component state
- JSON editor changes -> parse JSON -> adapter converts to ReactFlow nodes/edges -> canvas re-renders
- Save -> PUT `/api/v1/workflows/dynamic/{id}` with the JSON definition
- Test -> POST `/api/v1/workflows/dynamic/{id}/run` with `dry_run: true` (new backend parameter, see Section 11)
- Activate -> POST `/api/v1/workflows/dynamic/{id}/activate`

**`dry_run` semantics** (new backend parameter):
- Validates the definition structure (step IDs unique, referenced outputs exist, tool names valid)
- Resolves all `{{template}}` variables with sample data to verify expressions parse correctly
- Returns the **execution plan**: ordered list of steps with resolved params, expected step types, and detected branches — without executing any step
- No MCP tools are called, no agents are invoked, no side effects occur
- Cost: zero tokens, zero USD — purely structural validation
- Response includes: `{steps_planned: [...], branches_detected: [...], integrations_required: [...], validation_errors: [...]}`

---

## 4. Node Types & Visual Language

### 4.1 Node Design

| Node Type | Shape | Color | Icon | Label Example |
|-----------|-------|-------|------|---------------|
| Trigger | Rounded pill | Ocean blue | Clock/webhook/bolt | "Every day at 8am" |
| MCP Tool | Rectangle | Teal | Tool icon | "Search Emails" |
| Agent | Rectangle | Purple | Brain/bot | "Luna: Summarize" |
| Condition | Diamond | Amber | Git-branch | "Score >= 70?" |
| For Each | Rounded rect + loop badge | Green | Repeat | "For each contact" |
| Parallel | Hexagon | Cyan | Split arrows | "Run in parallel" |
| Wait | Dashed rect | Gray | Hourglass | "Wait 5 minutes" |
| Human Approval | Rect + badge | Orange | Hand/check | "Approve email?" |
| Transform | Small rect | Light gray | Code brackets | "Extract names" |
| Sub-workflow | Double-border rect | Blue | Layers | "Run Lead Pipeline" |

### 4.2 Edge Styling

- Default: solid line with arrow, top-to-bottom data flow
- Condition "then" branch: solid green edge
- Condition "else" branch: dashed red edge
- Parallel fan-out: multiple solid lines from one node
- Parallel merge: converge into auto-inserted merge node

### 4.3 Node Content

Each node displays:
- Step name (editable inline by double-clicking)
- Step type icon + label
- Brief param summary (e.g., "search_emails: is:unread")
- Output variable name as a small chip
- Status indicator during test/execution runs
- Integration badge: small service icon + green/red connection dot

### 4.4 Ocean Theme Integration

- Glassmorphic node backgrounds with subtle backdrop blur
- Dark canvas background with subtle grid dots
- Matches existing sidebar/card aesthetic
- Subtle Y-axis hover transitions on nodes (6px lift)

---

## 5. Integration Awareness Layer

Workflows orchestrate external services. The builder must surface integration dependencies clearly.

### 5.1 Tool-to-Integration Mapping

Each MCP tool in the registry maps to an integration:
- `search_emails`, `send_email`, `read_email` -> Gmail
- `create_jira_issue`, `search_jira_issues` -> Jira
- `list_calendar_events`, `create_calendar_event` -> Google Calendar
- `search_drive_files`, `read_drive_file` -> Google Drive
- `score_entity`, `create_entity`, `find_entities` -> Knowledge Graph (built-in, always connected)
- `execute_shell` -> Shell (built-in)
- `search_github_code`, `create_github_issue` -> GitHub
- Agent steps (Luna, Code) -> respective CLI platform integration

### 5.2 Backend: Integration Status Endpoints (New)

The builder needs to know which integrations are connected. Two new endpoints required:

**`GET /api/v1/integrations/status`** — returns tenant's integration connection status:
```json
{
  "gmail": {"connected": true, "name": "Gmail", "icon": "mail"},
  "jira": {"connected": false, "name": "Jira", "icon": "clipboard"},
  "google_calendar": {"connected": true, "name": "Google Calendar", "icon": "calendar"},
  "github": {"connected": true, "name": "GitHub", "icon": "github"}
}
```
Implementation: queries `integration_credentials` table for the current tenant, checks which integration names have valid (non-expired) credentials stored.

**`GET /api/v1/integrations/tool-mapping`** — returns MCP tool -> required integration mapping:
```json
{
  "search_emails": "gmail",
  "send_email": "gmail",
  "create_jira_issue": "jira",
  "list_calendar_events": "google_calendar",
  "create_entity": null,
  "score_entity": null
}
```
Implementation: static dictionary maintained in the codebase (same pattern as Section 5.1 mapping). Tools with `null` integration are built-in and always available. Cached — changes only when new MCP tools are added.

The activation endpoint (`POST /activate`) must also validate integration dependencies server-side: parse the workflow definition, collect all tool names, check each against the mapping, verify all required integrations are connected. Return 400 with missing integrations list if any are disconnected.

### 5.3 Surface Points

- **Node badge**: small service icon + connection dot (green=connected, red=disconnected) on every tool/agent node
- **Inspector panel**: "Requires: Gmail (connected)" or "Requires: Jira (not connected) — [Connect]" link that navigates to Integrations page
- **Toolbar pill**: "Integrations: 3/4" — click to expand dropdown listing all required services with status
- **Activation gate**: cannot activate workflow if any required integration is disconnected. Clear error message listing what needs to be connected. Enforced both client-side (builder UI) and server-side (activate endpoint).
- **Built-in services** (Knowledge Graph, Shell, RL, Memory) always show as connected

---

## 6. Workflows Page Restructure

### 6.1 New Tab Structure

| Tab | Content |
|-----|---------|
| **My Workflows** | All workflows (migrated static + user-created) in unified list. Status badges, stats, quick actions (run/pause/edit/delete). Filters by status, tags, trigger type. |
| **Templates** | Marketplace: native (5 bundled, expanding to 8 post-migration) + community (GitHub import) + shared custom. One-click install. Search/filter by category. |
| **Runs** | Unified execution history across all workflows. Tree visualization, step-level trace, cost tracking. Re-run failed runs. |
| **Builder** | Not a browseable tab — navigated to when creating new or editing existing workflow. |

### 6.2 Navigation Flows

- My Workflows -> "Edit" -> Builder opens with workflow loaded
- My Workflows -> "New Workflow" -> Builder opens with empty canvas + trigger picker
- Templates -> "Install" -> copies definition to My Workflows as draft
- Templates -> "Preview" -> read-only builder view
- Runs -> click a run -> expands to execution tree view
- Luna chat -> creates workflow -> appears in My Workflows

### 6.3 Removal of Legacy Tabs

The current "Designs" tab (read-only static workflow display) and "Executions" tab merge into the new structure. Once all 20 static workflows are migrated, there's no separate static/dynamic concept.

---

## 7. Execution UI — Enterprise Audit Grade

### 7.1 Live Tree Visualization

When viewing a run, the same canvas renders in **read-only execution mode**:
- Nodes light up as they execute:
  - Gray: pending
  - Blue pulse: running
  - Green: success
  - Red: failed
  - Yellow: waiting for approval
- Edges animate to show data flowing between steps
- Current step highlighted with a glow effect
- Real-time updates via polling (fallback) or WebSocket (preferred)

### 7.2 Step Detail Panel

Click any node in the execution tree to see:
- Input data sent to the step
- Output data returned
- Duration (ms)
- Tokens used + cost (USD)
- Retry count (if retried)
- Error message + stack trace (if failed)
- Platform used (Claude/Gemini/Codex for agent steps)
- MCP server called (for tool steps)

### 7.3 Run Summary Bar

Top of run detail view:
- Total duration
- Total tokens / total cost USD
- Steps completed: 5/7 with progress bar
- Trigger type + input data that started the run
- RL score (once auto-scored)
- Re-run / Cancel buttons

### 7.4 Enterprise Audit Trail

- **Who triggered**: user ID, Luna session ID, cron scheduler, webhook source (IP + headers)
- **Tenant context**: tenant_id, user role at time of execution
- **Step-level actor**: which agent/platform handled each step, which MCP server was called, response time
- **Data lineage**: for each step, trace where each input value came from (which prior step's output)
- **Approval audit**: who approved/rejected, when, from which session
- **Export**: download full run audit as JSON or CSV for compliance review
- **Retention**: configurable per-tenant (default 90 days, enterprise unlimited)

### 7.5 Run List Enhancements

The Runs tab table:
- Status column with colored badges
- Workflow name + version
- Trigger type icon
- Duration + cost columns
- Mini step progress bar (green/red segments per step)
- Filter by: workflow, status, date range, trigger type
- Bulk re-run for failed runs

### 7.6 Step Timeline (Alternative View)

Vertical timeline below the tree (reuses `TaskTimeline` component pattern):
- Each step as a row: icon | name | status badge | duration | cost
- Expandable to show input/output JSON
- Useful for linear workflows where tree view is visual overkill

---

## 8. Luna Full CRUD via Chat

Luna is the simple mode. No simplified builder UI needed — if you want simple, talk to Luna.

### 8.1 MCP Tool Prerequisites

The existing 6 MCP tools (`create_dynamic_workflow`, `list_dynamic_workflows`, `run_dynamic_workflow`, `get_workflow_run_status`, `activate_dynamic_workflow`, `install_workflow_template`) are missing two tools required for full CRUD:

- **`update_dynamic_workflow`** (NEW):
  - Parameters: `workflow_id` (str, required), `name` (str, optional), `description` (str, optional), `trigger_type` (str, optional — "cron"/"interval"/"webhook"/"event"/"manual"), `trigger_schedule` (str, optional — cron expression or interval minutes), `definition` (dict, optional — full `WorkflowDefinition` replacement)
  - Behavior: fetches current workflow, merges provided fields, PUTs the full result. For step-level edits, Luna constructs the updated full definition and sends the complete replacement via the `definition` field. Always full replacement — no surgical patches.
- **`delete_dynamic_workflow`** (NEW):
  - Parameters: `workflow_id` (str, required)
  - Behavior: calls `DELETE /api/v1/dynamic-workflows/{workflow_id}`. Returns confirmation message.

### 8.2 Operations

| User Says | Luna Does | MCP Tool |
|-----------|-----------|----------|
| "Every morning scan my inbox and score leads" | Creates workflow JSON, confirms steps in plain language, saves as draft | `create_dynamic_workflow` |
| "What workflows do I have?" | Lists workflows with status + last run stats | `list_dynamic_workflows` |
| "Change my inbox scanner to run every 30 minutes" | Updates trigger config, confirms change | `update_dynamic_workflow` (NEW) |
| "Add a Jira ticket step after lead scoring" | Fetches current definition, inserts step, sends full replacement, confirms | `update_dynamic_workflow` (NEW) |
| "Run my lead pipeline now" | Triggers manual execution | `run_dynamic_workflow` |
| "Why did my last inbox scan fail?" | Reads step logs, explains in plain language | `get_workflow_run_status` |
| "Pause the competitor watch" | Sets status to paused | `activate_dynamic_workflow` (with pause) |
| "Delete the weekly report" | Confirms, then deletes | `delete_dynamic_workflow` (NEW) |

### 8.3 Integration Awareness

Before creating a workflow, Luna checks required integrations:
- "This workflow needs Gmail access. Want me to walk you through connecting it?"
- "You'll need Jira connected for the ticket creation step. It's not set up yet — head to Integrations to connect it."

### 8.4 Smart Suggestions

Based on RL creation patterns:
- "Users who set up inbox scanners usually add a lead scoring step — want me to include that?"
- "Your competitor watch would work better with a weekly digest instead of daily alerts based on how similar workflows perform."

---

## 9. Static-to-Dynamic Migration

### 9.1 Migration Tiers

**Tier 1 — Linear workflows (5):**
`follow_up.py`, `monthly_billing.py`, `dataset_sync.py`, `data_source_sync.py`, `embedding_backfill.py`

Straightforward: each Python activity becomes an `mcp_tool` or `agent` step in sequence. No `continue_as_new`, no complex branching. (`embedding_backfill` is a one-shot batch workflow, not long-running.)

**Tier 2 — Branching workflows (4):**
`deal_pipeline.py`, `prospecting_pipeline.py`, `remedia_order.py`, `auto_action.py`

Moderate: conditions map to `condition` nodes, loops map to `for_each`, approval steps map to `human_approval`. No `continue_as_new`.

**Tier 3 — Long-running / continue_as_new workflows (7):**
`competitor_monitor.py`, `aremko_monitor.py`, `inbox_monitor.py`, `channel_health.py`, `goal_review.py`, `memory_consolidation.py`, `autonomous_learning.py`

These all use `continue_as_new` for infinite-duration execution. Need the `continue_as_new` step type and careful handling of restart state. `autonomous_learning` also has deep infrastructure hooks but its primary complexity is the `continue_as_new` pattern.

**Tier 4 — Deep infrastructure workflows (4):**
`task_execution.py`, `knowledge_extraction.py`, `code_task.py`, `rl_policy_update.py`

Deepest platform integration. Need `cli_execute` and `internal_api` step types. These workflows have tight coupling to internal services that must be preserved.

**Out of migration scope:**
- `DynamicWorkflowExecutor` — the executor itself, not a target
- `ChatCliWorkflow` (in `apps/code-worker/workflows.py`) — internal CLI session management, not user-facing
- `ProviderReviewWorkflow` (in `apps/code-worker/workflows.py`) — internal quality review infrastructure, not user-facing
- `ScheduledSyncWorkflow` (in `data_source_sync.py`) — secondary class, migrates alongside `data_source_sync`

**Note on `code_task`:** `CodeTaskWorkflow` also lives in `apps/code-worker/workflows.py`, but unlike `ChatCliWorkflow` and `ProviderReviewWorkflow`, it IS user-facing — users explicitly dispatch code tasks via chat. It's included in Tier 4 and migrated using the `cli_execute` step type, which dispatches a child workflow to the `servicetsunami-code` queue.

**Total: 20 workflows across 4 tiers (5 + 4 + 7 + 4)**

### 9.2 New Step Types

| Step Type | Purpose | Used By | Details |
|-----------|---------|---------|---------|
| `continue_as_new` | Infinite-duration workflows that restart periodically | Tier 3 (7 workflows) | The dynamic executor detects this step type at the end of a definition and calls `workflow.continue_as_new()` with the current context, resetting the Temporal history. Config: `interval_seconds` (restart period), `max_iterations` (optional safety limit). |
| `cli_execute` | Run Claude Code CLI in isolated code-worker pod | code_task | Dispatches a **child workflow** on the `servicetsunami-code` task queue (not an activity on `servicetsunami-orchestration`). The child workflow's first activity fetches the tenant's OAuth token via `GET /api/v1/oauth/internal/token/claude_code?tenant_id=<uuid>`, sets it as `CLAUDE_CODE_OAUTH_TOKEN` env var, then runs `claude -p` subprocess. Returns: commit log, files changed, PR URL. |
| `internal_api` | Call internal API endpoints directly (not MCP) | task_execution, knowledge_extraction | For steps that need internal service calls: memory recall, entity persistence, RL scoring, evaluation. Config: `method` (GET/POST/PUT), `path` (internal API path), `body` (template-resolved JSON). Example for task_execution migration: step 1 = `internal_api` (recall memory), step 2 = `agent` (execute task), step 3 = `internal_api` (persist entities), step 4 = `internal_api` (log RL score). |

### 9.3 Migration Process (Per Workflow)

1. Write the JSON definition equivalent
2. Create as native-tier dynamic workflow (bundled, read-only base)
3. **Validate via test runs**: run the dynamic version with representative sample inputs and compare outputs against known-good results from the static version. For workflows with side effects (sending emails, creating entities), use `dry_run: true` or validate against a staging tenant.
4. Feature-flag the workflow to route to dynamic executor (`USE_DYNAMIC_EXECUTOR_{workflow_name} = true`)
5. Monitor for 1 week: compare success rate, duration, and cost via RL scoring
6. If stable, remove Python class + worker registration
7. Log migration as RL experience

### 9.4 Rollback Safety

- Keep Python classes in `workflows/legacy/` for 30 days post-migration
- Feature flag `USE_DYNAMIC_EXECUTOR` per-workflow ID to flip back instantly
- Monitor error rates via RL scoring — auto-rollback if success rate drops below threshold

---

## 10. RL + Memory — Full Lifecycle Wiring

### 10.1 Layer 1: Run-Level RL

Every workflow execution logs an RL experience:
- **decision_point**: `workflow_execution`
- **state**: workflow_id, step_count, trigger_type, required_integrations
- **action**: platforms used, agents invoked, tools called, total steps executed
- **reward**: composite — success/failure (40%), duration vs avg (20%), cost efficiency (20%), user satisfaction (20%)
- **reward_components**: `{success, duration_ms, cost_usd, steps_completed, steps_total, trigger_type}`

### 10.2 Layer 2: Step-Level RL

Every step within a run logs its own RL experience:
- **decision_point**: `workflow_step`
- **state**: step_type, tool_name/agent_slug, input_data_shape, position_in_workflow
- **action**: platform chosen, params used, retry_count
- **reward**: step success (50%), duration vs step-type avg (25%), tokens/cost (25%)
- **reward_components**: `{success, duration_ms, tokens, cost_usd, platform, error_type}`
- **Cross-platform learning**: same step type on different platforms -> RL learns optimal routing

### 10.3 Layer 3: Creation Pattern RL (Future Phase)

Deferred until there's sufficient data (requires many user-created workflows to mine patterns). Log the raw events now for future training:

When a workflow is created (via Luna or builder):
- **decision_point**: `workflow_creation`
- **state**: trigger_type, step_types_used, step_count, template_source
- **action**: step sequence, tools chosen, agent assignments
- **reward** (computed later): first-run success rate, user kept it active (didn't delete/pause within 24h)
- **Pattern mining** (future): cluster similar definitions -> suggest improvements

### 10.4 Memory Integration

- **Entity creation**: each workflow becomes a knowledge entity (category="workflow") with observations tracking performance
- **Relation mapping**: workflow -> uses_integration -> Gmail, workflow -> created_by -> user
- **Memory activity log**: workflow_created, workflow_activated, workflow_failed events in `memory_activities`
- **Context for Luna**: recall similar workflows + their performance when user asks to create new ones
- **Auto-observations**: after every 10 runs, generate observation summarizing performance trends

### 10.5 Auto-Optimization Suggestions

Surfaced as notifications, Luna proactive suggestions, and inline builder hints (lightbulb icon on optimizable nodes):
- "Your inbox scanner runs 3x faster if you batch emails in groups of 10"
- "Switching summarization from Claude to Gemini saves 40% cost with same quality for this workflow"
- "Adding a dedup step before lead scoring would prevent duplicate notifications"

---

## 11. Dependencies

### Frontend (New)
- `reactflow` — canvas rendering + node/edge management
- `dagre` — automatic tree layout algorithm
- `@reactflow/node-resizer` — optional, for resizable nodes

### Frontend (Existing, Reused)
- React 18, React Bootstrap, React Icons, Axios, Recharts
- `TaskTimeline` component pattern for step timeline view
- `IntegrationsPanel` pattern for integration status display
- Ocean theme glassmorphic styling

### Backend (Existing)
- 13 API endpoints already built
- 6 MCP tools for Luna already built
- `DynamicWorkflowExecutor` Temporal workflow already handles 8 step types
- 5 native templates seeded

### Backend (New — Builder Support)
- `dry_run` parameter on `POST /workflows/dynamic/{id}/run` endpoint for test console
- `GET /api/v1/integrations/status` — tenant integration connection status
- `GET /api/v1/integrations/tool-mapping` — MCP tool -> required integration mapping
- Server-side integration validation on `POST /activate` endpoint

### Backend (New — Luna CRUD)
- `update_dynamic_workflow` MCP tool — fetch current definition, apply changes, PUT full replacement
- `delete_dynamic_workflow` MCP tool — confirm + delete workflow

### Backend (New — Migration)
- 3 new step types: `continue_as_new`, `cli_execute`, `internal_api`
- Feature flag system (`USE_DYNAMIC_EXECUTOR_{name}`) for per-workflow rollback
- `cli_execute` child workflow dispatch to `servicetsunami-code` queue

---

## 12. Out of Scope

- Real-time WebSocket for execution updates (polling is sufficient for v1, WebSocket is a future optimization)
- Collaborative editing (multiple users editing same workflow simultaneously)
- Version diff viewer (comparing workflow versions side-by-side)
- Mobile-optimized builder (canvas requires desktop viewport)
- Marketplace billing (template creator royalties — Wolfpoint Protocol future)
- Provider council reviews for workflow runs (existing `ProviderReviewWorkflow` could trigger on workflow runs with side-effect tools, but deferred to avoid coupling two complex systems during initial rollout — revisit once migration is stable)
- Creation pattern RL mining (Layer 3 — log events now, mine patterns later when sufficient data exists)
