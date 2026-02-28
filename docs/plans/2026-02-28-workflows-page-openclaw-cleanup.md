# Workflows Page + OpenClaw Cleanup

## Context

The platform lacks visibility into what each Temporal workflow does structurally. The user wants a unified Workflows page that shows **visual flowcharts** of all 8 workflows AND embeds the existing execution audit (TaskConsolePage) under it. Also: remove the orphaned DataPipelinesPage and clean up stale OpenClaw references.

## Part 1: New WorkflowsPage

### Files to Create
- `apps/web/src/pages/WorkflowsPage.js` — Main page with two tabs: "Designs" and "Executions"
- `apps/web/src/pages/WorkflowsPage.css` — Styles for flowchart nodes, connectors, cards

### Files to Modify
- `apps/web/src/App.js` — Add `/workflows` route, remove TaskConsolePage import (absorbed)
- `apps/web/src/components/Layout.js` — Replace `/task-console` "Workflow Audit" with `/workflows` "Workflows"

### Design

**Tab 1: "Designs"** — Visual structure of all 8 workflows as interactive flowchart cards.

Each workflow gets a card with:
- Name, task queue badge, description
- Expandable flowchart showing steps as connected nodes (pure CSS/SVG, no external lib)
- Each node: step name, timeout, retry policy
- Visual indicators: branching (diamond), loops (circular arrow), child workflows (nested), timers (clock)

Workflow data is hardcoded as a static JSON structure in the component (not from API — these are code-defined structures that don't change at runtime). The 8 workflows:

1. **TaskExecutionWorkflow** — 5 sequential steps: dispatch → recall_memory → execute → persist_entities → evaluate (queue: orchestration)
2. **DatasetSyncWorkflow** — 3 steps: sync_to_bronze → transform_to_silver → update_metadata (queue: databricks)
3. **DataSourceSyncWorkflow** — 4 steps: extract → load_bronze → load_silver → update_metadata (queue: databricks)
4. **ScheduledSyncWorkflow** — Parent: loops DataSourceSyncWorkflow per table (queue: databricks)
5. **KnowledgeExtractionWorkflow** — 1 step: extract_knowledge_from_session (queue: databricks)
6. **AgentKitExecutionWorkflow** — 1 step: execute_agent_kit_activity (queue: databricks)
7. **ChannelHealthMonitorWorkflow** — 3 steps + reconnect loop + continue_as_new (queue: orchestration)
8. **FollowUpWorkflow** — sleep(delay) → execute_followup_action with action branching (queue: orchestration)

**Tab 2: "Executions"** — The current TaskConsolePage content, moved here as-is.

Extract the core content from `TaskConsolePage.js` (everything inside `<Layout>`) into a reusable component or inline it directly. The existing filters, tabs (All, Agent Tasks, Chat, Provisioning, Data Pipelines, Data Sync), refresh controls, and detail modal all remain unchanged.

### Implementation Steps

1. Create `WORKFLOW_DEFINITIONS` constant — static array with workflow metadata and steps
2. Build `WorkflowCard` component — collapsible card with flowchart nodes rendered as flex/grid items with CSS connectors (arrows via `::after` pseudo-elements or SVG lines)
3. Build `WorkflowsPage` with two tabs using React Bootstrap `Nav`/`Tab`
4. Move TaskConsolePage content into the "Executions" tab
5. Update routing: `/workflows` → WorkflowsPage, remove `/task-console` route (or redirect it to `/workflows?tab=executions`)
6. Update Layout sidebar nav

## Part 2: Remove DataPipelinesPage

### Files to Delete
- `apps/web/src/pages/DataPipelinesPage.js`
- `apps/web/src/pages/DataPipelinesPage.css`

### Files to Keep (backend active, used by scheduler worker)
- All backend models, services, routes, schemas for data_pipeline — stay as-is

### Notes
- DataPipelinesPage is already not routed in App.js and not in sidebar nav
- The `dataPipeline.js` service file can stay (backend API still active)
- Just delete the orphaned frontend page + CSS files

## Part 3: Clean Up OpenClaw References

OpenClaw was already removed from live code. Remaining references are mostly in CLAUDE.md and one stub file.

### HIGH priority (active code/config)
- `apps/api/app/services/orchestration/skill_router.py` — Stub with "OpenClaw removed" messages. Clean up docstrings to remove OpenClaw mentions
- `CLAUDE.md` — Remove/update ~14 stale sections: "Managed OpenClaw Instances", "OpenClaw Gateway Protocol", env vars (OPENCLAW_CHART_PATH, OPENCLAW_GATEWAY_TOKEN), component refs (OpenClawInstanceCard.js), workflow refs (openclaw_provision.py), model refs (tenant_instance.py)

### LOW priority (leave as historical record)
- Migration SQL files (027, 034) — Never modify executed migrations
- `docs/plans/` design docs — Historical, leave as-is
- `.claude/settings.local.json` — Local settings, harmless

## Verification

1. `cd apps/web && npm start` — verify `/workflows` loads with both tabs
2. Designs tab shows all 8 workflow cards with expandable flowcharts
3. Executions tab shows the full TaskConsolePage audit content
4. `/task-console` redirects to `/workflows`
5. Sidebar nav shows "Workflows" under AI OPERATIONS
6. No OpenClaw references remain in CLAUDE.md or active Python code
7. DataPipelinesPage files are deleted
