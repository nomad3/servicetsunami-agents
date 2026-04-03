# Dynamic Workflows — Visual Builder, Migration & RL Lifecycle Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship a tree-based visual workflow builder (ReactFlow), migrate all 20 static workflows to dynamic JSON, wire Luna full CRUD via chat, build enterprise-grade execution visualization, and connect the RL lifecycle.

**Architecture:** ReactFlow canvas with dagre auto-layout renders workflow JSON definitions as interactive node trees. An adapter layer (`workflowAdapter.js`) translates between flat ReactFlow nodes/edges and nested workflow JSON. Backend additions: `dry_run` validation, integration status endpoints, internal auth for MCP tools, 3 new step types. All 20 static Temporal workflows converted to JSON definitions via 4 tiers.

**Tech Stack:** React 18, ReactFlow, dagre, React Bootstrap, Axios | FastAPI, SQLAlchemy, Temporal Python SDK, httpx | PostgreSQL + pgvector

**Spec:** `docs/plans/2026-04-03-dynamic-workflows-visual-builder-design.md`

---

## File Inventory

### New Frontend Files
| File | Responsibility |
|------|---------------|
| `apps/web/src/components/workflows/WorkflowBuilder.js` | Main builder container — toolbar, layout, state management |
| `apps/web/src/components/workflows/WorkflowCanvas.js` | ReactFlow canvas with dagre layout, node/edge rendering |
| `apps/web/src/components/workflows/StepPalette.js` | Left sidebar — draggable step type categories |
| `apps/web/src/components/workflows/StepInspector.js` | Right panel — selected node configuration form |
| `apps/web/src/components/workflows/nodes/TriggerNode.js` | Custom ReactFlow node — trigger (cron/webhook/event/manual) |
| `apps/web/src/components/workflows/nodes/StepNode.js` | Custom ReactFlow node — mcp_tool, agent, transform |
| `apps/web/src/components/workflows/nodes/ConditionNode.js` | Custom ReactFlow node — diamond shape, then/else edges |
| `apps/web/src/components/workflows/nodes/ForEachNode.js` | Custom ReactFlow node — loop with sub-tree |
| `apps/web/src/components/workflows/nodes/ParallelNode.js` | Custom ReactFlow node — fan-out with merge |
| `apps/web/src/components/workflows/nodes/ApprovalNode.js` | Custom ReactFlow node — human approval state |
| `apps/web/src/components/workflows/WorkflowAdapter.js` | Bidirectional converter: workflow JSON <-> ReactFlow nodes/edges |
| `apps/web/src/components/workflows/TestConsole.js` | Bottom panel — dry-run results per step |
| `apps/web/src/components/workflows/TemplatesTab.js` | Templates marketplace — browse, install, preview |
| `apps/web/src/components/workflows/RunsTab.js` | Unified runs list with tree visualization |
| `apps/web/src/components/workflows/RunTreeView.js` | Read-only execution tree with live status colors |
| `apps/web/src/components/workflows/RunStepDetail.js` | Step detail panel for execution view — input/output/cost/audit |

### New Backend Files
| File | Responsibility |
|------|---------------|
| `apps/api/app/services/integration_status.py` | Service: check connected integrations, tool-to-integration mapping |

### Modified Frontend Files
| File | Change |
|------|--------|
| `apps/web/package.json` | Add reactflow, @dagrejs/dagre |
| `apps/web/src/services/dynamicWorkflowService.js` | Add: update, delete, dryRun, getIntegrationStatus, getToolMapping, browseTemplates |
| `apps/web/src/pages/WorkflowsPage.js` | Restructure tabs: My Workflows, Templates, Runs. Remove legacy Designs/Executions. Route to builder. |
| `apps/web/src/App.js` | Add route `/workflows/builder/:id?` for builder view |

### Modified Backend Files
| File | Change |
|------|--------|
| `apps/api/app/api/v1/dynamic_workflows.py` | Add internal auth endpoints (MCP), dry_run param, activation gate |
| `apps/api/app/schemas/dynamic_workflow.py` | Add dry_run to WorkflowRunRequest, add new step types to schema |
| `apps/api/app/api/v1/integrations.py` | Add /status and /tool-mapping endpoints to existing integrations router |
| `apps/api/app/workflows/dynamic_executor.py` | Add continue_as_new step type handling |
| `apps/api/app/workflows/activities/dynamic_step.py` | Add cli_execute, internal_api step handlers |
| `apps/mcp-server/src/mcp_tools/dynamic_workflows.py` | Add update_dynamic_workflow, delete_dynamic_workflow tools |

---

## Phase 1: Backend Prerequisites

### Task 1: Fix MCP-to-API Auth (P0)

The MCP tools send `X-Internal-Key` + `X-Tenant-Id` headers but the dynamic workflow routes require JWT bearer auth. Add internal endpoints following the existing pattern in `data_sources.py`.

**Files:**
- Modify: `apps/api/app/api/v1/dynamic_workflows.py`

- [ ] **Step 1: Read the existing internal auth pattern**

Read `apps/api/app/api/v1/data_sources.py` lines with `/internal/` routes to understand the pattern. The pattern: check `X-Internal-Key` header against BOTH `settings.API_INTERNAL_KEY` and `settings.MCP_API_KEY`, extract tenant_id from `X-Tenant-Id` header.

- [ ] **Step 2: Add internal auth dependency helper**

Add at the top of `dynamic_workflows.py`. **CRITICAL**: Must accept both key types (the MCP server sends `MCP_API_KEY`, matching the pattern in `data_sources.py`):

```python
from fastapi import Header
from app.core.config import settings

def verify_internal_key(
    x_internal_key: Optional[str] = Header(None, alias="X-Internal-Key"),
    x_tenant_id: Optional[str] = Header(None, alias="X-Tenant-Id"),
    db: Session = Depends(deps.get_db),
):
    if x_internal_key not in (settings.API_INTERNAL_KEY, settings.MCP_API_KEY):
        raise HTTPException(status_code=401, detail="Invalid internal key")
    if not x_tenant_id:
        raise HTTPException(status_code=400, detail="X-Tenant-Id required")
    return uuid.UUID(x_tenant_id)
```

- [ ] **Step 3: Add internal CRUD endpoints**

**CRITICAL: Route ordering** — these `/internal/*` endpoints MUST be placed BEFORE the `/{workflow_id}` route in the file. Otherwise FastAPI will try to parse "internal" as a UUID and return 422. Add them near the top of the route definitions, after the `POST /` and `GET /` routes:

```python
# --- Internal endpoints (for MCP tools) --- MUST be before /{workflow_id} routes ---

@router.post("/internal/create")
def internal_create_workflow(workflow_in: DynamicWorkflowCreate, tenant_id: uuid.UUID = Depends(verify_internal_key), db: Session = Depends(deps.get_db)):
    # Same logic as create_workflow but tenant_id from header
    ...

@router.get("/internal/list")
def internal_list_workflows(status: Optional[str] = None, tenant_id: uuid.UUID = Depends(verify_internal_key), db: Session = Depends(deps.get_db)):
    ...

@router.get("/internal/runs/{run_id}")
def internal_get_run(run_id: uuid.UUID, tenant_id: uuid.UUID = Depends(verify_internal_key), db: Session = Depends(deps.get_db)):
    ...

@router.put("/internal/{workflow_id}")
def internal_update_workflow(workflow_id: uuid.UUID, workflow_in: DynamicWorkflowUpdate, tenant_id: uuid.UUID = Depends(verify_internal_key), db: Session = Depends(deps.get_db)):
    ...

@router.delete("/internal/{workflow_id}")
def internal_delete_workflow(workflow_id: uuid.UUID, tenant_id: uuid.UUID = Depends(verify_internal_key), db: Session = Depends(deps.get_db)):
    ...

@router.post("/internal/{workflow_id}/run")
def internal_run_workflow(workflow_id: uuid.UUID, run_request: WorkflowRunRequest = None, tenant_id: uuid.UUID = Depends(verify_internal_key), db: Session = Depends(deps.get_db)):
    ...

@router.post("/internal/{workflow_id}/activate")
def internal_activate_workflow(workflow_id: uuid.UUID, tenant_id: uuid.UUID = Depends(verify_internal_key), db: Session = Depends(deps.get_db)):
    ...

@router.post("/internal/{workflow_id}/pause")
def internal_pause_workflow(workflow_id: uuid.UUID, tenant_id: uuid.UUID = Depends(verify_internal_key), db: Session = Depends(deps.get_db)):
    ...

@router.get("/internal/{workflow_id}/runs")
def internal_list_runs(workflow_id: uuid.UUID, tenant_id: uuid.UUID = Depends(verify_internal_key), db: Session = Depends(deps.get_db)):
    ...

# --- End internal endpoints ---
```

- [ ] **Step 4: Update MCP tools to use internal paths**

In `apps/mcp-server/src/mcp_tools/dynamic_workflows.py`, update `_api_call` to prefix paths with `/internal`:

```python
async def _api_call(method: str, path: str, tenant_id: str, json_data: dict = None) -> dict:
    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await getattr(client, method)(
            f"{API_BASE_URL}/api/v1/dynamic-workflows/internal{path}",
            headers={"X-Internal-Key": API_INTERNAL_KEY, "X-Tenant-Id": tenant_id},
            json=json_data,
        )
        return resp.json()
```

- [ ] **Step 5: Verify MCP tools work end-to-end**

Test by calling the MCP tools via the ServiceTsunami platform or directly via HTTP to the MCP server.

- [ ] **Step 6: Commit**

```bash
git add apps/api/app/api/v1/dynamic_workflows.py apps/mcp-server/src/mcp_tools/dynamic_workflows.py
git commit -m "fix: add internal auth endpoints for dynamic workflow MCP tools"
```

---

### Task 2: Add `update_dynamic_workflow` and `delete_dynamic_workflow` MCP Tools

**Files:**
- Modify: `apps/mcp-server/src/mcp_tools/dynamic_workflows.py`

- [ ] **Step 1: Add update tool**

```python
@mcp.tool()
async def update_dynamic_workflow(
    workflow_id: str,
    tenant_id: str,
    name: str = None,
    description: str = None,
    trigger_type: str = None,
    trigger_schedule: str = None,
    definition: dict = None,
) -> str:
    """Update an existing dynamic workflow. Provide only the fields to change.
    For step-level edits, provide the full updated definition dict."""
    # Fetch current workflow
    current = await _api_call("get", f"/{workflow_id}", tenant_id)
    if "error" in current:
        return f"Error fetching workflow: {current['error']}"

    # Build update payload
    update_data = {}
    if name: update_data["name"] = name
    if description: update_data["description"] = description
    if trigger_type or trigger_schedule:
        trigger_config = current.get("trigger_config", {}) or {}
        if trigger_type: trigger_config["type"] = trigger_type
        if trigger_schedule: trigger_config["schedule"] = trigger_schedule
        update_data["trigger_config"] = trigger_config
    if definition: update_data["definition"] = definition

    result = await _api_call("put", f"/{workflow_id}", tenant_id, json_data=update_data)
    return f"Updated workflow '{result.get('name', workflow_id)}'"
```

- [ ] **Step 2: Add delete tool**

```python
@mcp.tool()
async def delete_dynamic_workflow(workflow_id: str, tenant_id: str) -> str:
    """Delete a dynamic workflow permanently."""
    result = await _api_call("delete", f"/{workflow_id}", tenant_id)
    return f"Workflow {workflow_id} deleted"
```

- [ ] **Step 3: Commit**

```bash
git add apps/mcp-server/src/mcp_tools/dynamic_workflows.py
git commit -m "feat: add update and delete MCP tools for dynamic workflows"
```

---

### Task 3: Add `dry_run` Parameter to Run Endpoint

**Files:**
- Modify: `apps/api/app/schemas/dynamic_workflow.py`
- Modify: `apps/api/app/api/v1/dynamic_workflows.py`

- [ ] **Step 1: Add dry_run to WorkflowRunRequest schema**

In `apps/api/app/schemas/dynamic_workflow.py`:

```python
class WorkflowRunRequest(BaseModel):
    input_data: Optional[dict] = None
    dry_run: Optional[bool] = False
```

- [ ] **Step 2: Add dry_run validation logic**

In `apps/api/app/services/dynamic_workflows.py` (or inline in the route), add a `validate_workflow_definition` function:

```python
def validate_workflow_definition(workflow: DynamicWorkflow, input_data: dict = None) -> dict:
    """Validate definition without executing. Returns execution plan."""
    definition = workflow.definition
    steps = definition.get("steps", [])
    step_ids = set()
    validation_errors = []
    steps_planned = []
    integrations_required = set()

    for step in steps:
        step_id = step.get("id")
        if step_id in step_ids:
            validation_errors.append(f"Duplicate step ID: {step_id}")
        step_ids.add(step_id)

        if step["type"] == "mcp_tool":
            tool_name = step.get("tool")
            integration = TOOL_INTEGRATION_MAP.get(tool_name)
            if integration:
                integrations_required.add(integration)

        steps_planned.append({
            "id": step_id,
            "type": step["type"],
            "tool": step.get("tool"),
            "agent": step.get("agent"),
        })

    return {
        "steps_planned": steps_planned,
        "integrations_required": list(integrations_required),
        "validation_errors": validation_errors,
        "step_count": len(steps),
    }
```

- [ ] **Step 3: Update run endpoint to handle dry_run**

In the `/run` and `/internal/run` endpoints, check `dry_run` flag:

```python
if run_request and run_request.dry_run:
    plan = validate_workflow_definition(workflow, run_request.input_data)
    return plan
# else: proceed with Temporal execution as before
```

- [ ] **Step 4: Commit**

```bash
git add apps/api/app/schemas/dynamic_workflow.py apps/api/app/api/v1/dynamic_workflows.py apps/api/app/services/dynamic_workflows.py
git commit -m "feat: add dry_run validation for workflow test console"
```

---

### Task 4: Integration Status Endpoints

**Files:**
- Create: `apps/api/app/services/integration_status.py`
- Create: `apps/api/app/api/v1/integration_status.py`
- Modify: `apps/api/app/api/v1/routes.py`

- [ ] **Step 1: Create the tool-to-integration mapping**

In `apps/api/app/services/integration_status.py`:

```python
import uuid
from sqlalchemy.orm import Session
from app.models.integration_config import IntegrationConfig
from app.models.integration_credential import IntegrationCredential

# Static mapping: MCP tool name -> integration name
TOOL_INTEGRATION_MAP = {
    # Gmail
    "search_emails": "gmail", "send_email": "gmail", "read_email": "gmail",
    "deep_scan_emails": "gmail", "download_attachment": "gmail",
    # Google Calendar
    "list_calendar_events": "google_calendar", "create_calendar_event": "google_calendar",
    # Google Drive
    "search_drive_files": "google_drive", "read_drive_file": "google_drive",
    "create_drive_file": "google_drive", "list_drive_folders": "google_drive",
    # Jira
    "create_jira_issue": "jira", "get_jira_issue": "jira",
    "update_jira_issue": "jira", "search_jira_issues": "jira", "list_jira_projects": "jira",
    # GitHub
    "search_github_code": "github", "list_github_repos": "github",
    "list_github_issues": "github", "get_github_issue": "github",
    "list_github_pull_requests": "github", "get_github_pull_request": "github",
    "get_github_repo": "github", "read_github_file": "github",
    # Meta Ads
    "list_meta_campaigns": "meta_ads", "get_meta_campaign_insights": "meta_ads",
    "pause_meta_campaign": "meta_ads", "search_meta_ad_library": "meta_ads",
    # Google Ads
    "list_google_campaigns": "google_ads", "get_google_campaign_metrics": "google_ads",
    "pause_google_campaign": "google_ads", "search_google_ads_transparency": "google_ads",
    # TikTok
    "list_tiktok_campaigns": "tiktok_ads", "get_tiktok_campaign_insights": "tiktok_ads",
    "pause_tiktok_campaign": "tiktok_ads", "search_tiktok_creative_center": "tiktok_ads",
    # Built-in (null = always available)
    "create_entity": None, "find_entities": None, "score_entity": None,
    "update_entity": None, "merge_entities": None, "find_relations": None,
    "create_relation": None, "search_knowledge": None, "ask_knowledge_graph": None,
    "record_observation": None, "recall_memory": None, "calculate": None,
    "query_sql": None, "execute_shell": None, "forecast": None,
}

INTEGRATION_DISPLAY = {
    "gmail": {"name": "Gmail", "icon": "mail"},
    "google_calendar": {"name": "Google Calendar", "icon": "calendar"},
    "google_drive": {"name": "Google Drive", "icon": "drive"},
    "jira": {"name": "Jira", "icon": "clipboard"},
    "github": {"name": "GitHub", "icon": "github"},
    "meta_ads": {"name": "Meta Ads", "icon": "meta"},
    "google_ads": {"name": "Google Ads", "icon": "google"},
    "tiktok_ads": {"name": "TikTok Ads", "icon": "tiktok"},
}


def get_connected_integrations(db: Session, tenant_id: uuid.UUID) -> dict:
    """Return connection status for all integrations."""
    configs = db.query(IntegrationConfig).filter(
        IntegrationConfig.tenant_id == tenant_id,
        IntegrationConfig.enabled == True,
    ).all()

    connected_names = set()
    for config in configs:
        creds = db.query(IntegrationCredential).filter(
            IntegrationCredential.integration_config_id == config.id,
            IntegrationCredential.status == "active",
        ).first()
        if creds:
            connected_names.add(config.integration_name)

    result = {}
    for int_name, display in INTEGRATION_DISPLAY.items():
        result[int_name] = {
            "connected": int_name in connected_names,
            "name": display["name"],
            "icon": display["icon"],
        }
    return result


def get_tool_mapping() -> dict:
    """Return MCP tool -> integration name mapping."""
    return TOOL_INTEGRATION_MAP


def check_workflow_integrations(db: Session, tenant_id: uuid.UUID, definition: dict) -> list:
    """Return list of disconnected integrations required by a workflow definition."""
    connected = get_connected_integrations(db, tenant_id)
    required = set()

    def collect_from_steps(steps):
        for step in steps:
            if step.get("type") == "mcp_tool":
                integration = TOOL_INTEGRATION_MAP.get(step.get("tool"))
                if integration:
                    required.add(integration)
            for sub_steps in [step.get("steps", [])]:
                collect_from_steps(sub_steps)

    collect_from_steps(definition.get("steps", []))

    missing = []
    for int_name in required:
        info = connected.get(int_name, {})
        if not info.get("connected"):
            missing.append({"integration": int_name, "name": info.get("name", int_name)})
    return missing
```

- [ ] **Step 2: Create API routes**

**Note:** `routes.py` already mounts an `integrations` router at `/integrations`. To avoid prefix conflicts, add the new endpoints directly to the existing integrations router.

In `apps/api/app/api/v1/integrations.py`, add at the bottom:

```python
from app.services.integration_status import get_connected_integrations, get_tool_mapping

@router.get("/status")
def get_integration_status(
    db: Session = Depends(deps.get_db),
    current_user=Depends(deps.get_current_active_user),
):
    return get_connected_integrations(db, current_user.tenant_id)


@router.get("/tool-mapping")
def get_integration_tool_mapping():
    return get_tool_mapping()
```

No new router file needed — no routes.py change needed. The existing `/integrations` prefix already provides the correct URL prefix.

- [ ] **Step 4: Add activation gate to activate endpoint**

In the `activate_workflow` and `internal_activate_workflow` endpoints in `dynamic_workflows.py`, add before setting status to active:

```python
from app.services.integration_status import check_workflow_integrations

missing = check_workflow_integrations(db, tenant_id, workflow.definition)
if missing:
    raise HTTPException(
        status_code=400,
        detail=f"Cannot activate: missing integrations: {', '.join(m['name'] for m in missing)}",
    )
```

- [ ] **Step 5: Commit**

```bash
git add apps/api/app/services/integration_status.py apps/api/app/api/v1/integration_status.py apps/api/app/api/v1/routes.py apps/api/app/api/v1/dynamic_workflows.py
git commit -m "feat: add integration status endpoints and activation gate for workflows"
```

---

## Phase 2: Visual Builder — Core Canvas

### Task 5: Install ReactFlow and Dependencies

**Files:**
- Modify: `apps/web/package.json`

- [ ] **Step 1: Install packages**

```bash
cd apps/web && npm install reactflow @dagrejs/dagre
```

- [ ] **Step 2: Commit**

```bash
git add apps/web/package.json apps/web/package-lock.json
git commit -m "feat: add reactflow and dagre dependencies for workflow builder"
```

---

### Task 6: Workflow Adapter — JSON <-> ReactFlow Converter

This is the most critical piece. It translates between the nested workflow JSON definition and ReactFlow's flat node/edge arrays.

**Files:**
- Create: `apps/web/src/components/workflows/WorkflowAdapter.js`

- [ ] **Step 1: Build definitionToFlow**

```javascript
import dagre from '@dagrejs/dagre';

const NODE_WIDTH = 220;
const NODE_HEIGHT = 80;

// Map step types to ReactFlow node types
const STEP_TYPE_MAP = {
  mcp_tool: 'stepNode',
  agent: 'stepNode',
  transform: 'stepNode',
  condition: 'conditionNode',
  for_each: 'forEachNode',
  parallel: 'parallelNode',
  wait: 'stepNode',
  human_approval: 'approvalNode',
  webhook_trigger: 'stepNode',
  workflow: 'stepNode',
  continue_as_new: 'stepNode',
  cli_execute: 'stepNode',
  internal_api: 'stepNode',
};

export function definitionToFlow(definition, triggerConfig) {
  const nodes = [];
  const edges = [];
  let idCounter = 0;

  // Add trigger node as root
  const triggerId = 'trigger-root';
  nodes.push({
    id: triggerId,
    type: 'triggerNode',
    data: { trigger: triggerConfig || { type: 'manual' } },
    position: { x: 0, y: 0 },
  });

  const steps = definition?.steps || [];
  let prevId = triggerId;

  function processSteps(stepList, parentPrevId, parentId = null) {
    let currentPrevId = parentPrevId;

    stepList.forEach((step, idx) => {
      const nodeId = step.id || `step-${idCounter++}`;
      const nodeType = STEP_TYPE_MAP[step.type] || 'stepNode';

      nodes.push({
        id: nodeId,
        type: nodeType,
        data: { step, parentId },
        position: { x: 0, y: 0 },
      });

      // Edge from previous to current
      if (step.type === 'condition') {
        edges.push({ id: `e-${currentPrevId}-${nodeId}`, source: currentPrevId, target: nodeId });

        // Then branch
        if (step.then_steps && step.then_steps.length > 0) {
          processSteps(step.then_steps, nodeId, nodeId);
          // Find last then-step for merge
        }
        // Else branch
        if (step.else_steps && step.else_steps.length > 0) {
          processSteps(step.else_steps, nodeId, nodeId);
        }
      } else {
        edges.push({
          id: `e-${currentPrevId}-${nodeId}`,
          source: currentPrevId,
          target: nodeId,
          ...(currentPrevId.startsWith('condition') ? {} : {}),
        });

        // Process sub-steps for for_each / parallel
        if (step.steps && step.steps.length > 0) {
          processSteps(step.steps, nodeId, nodeId);
        }
      }

      currentPrevId = nodeId;
    });

    return currentPrevId;
  }

  processSteps(steps, triggerId);

  // Apply dagre layout
  return applyDagreLayout(nodes, edges);
}

function applyDagreLayout(nodes, edges) {
  const g = new dagre.graphlib.Graph();
  g.setDefaultEdgeLabel(() => ({}));
  g.setGraph({ rankdir: 'TB', nodesep: 50, ranksep: 80 });

  nodes.forEach((node) => {
    g.setNode(node.id, { width: NODE_WIDTH, height: NODE_HEIGHT });
  });
  edges.forEach((edge) => {
    g.setEdge(edge.source, edge.target);
  });

  dagre.layout(g);

  const layoutedNodes = nodes.map((node) => {
    const pos = g.node(node.id);
    return {
      ...node,
      position: { x: pos.x - NODE_WIDTH / 2, y: pos.y - NODE_HEIGHT / 2 },
    };
  });

  return { nodes: layoutedNodes, edges };
}
```

- [ ] **Step 2: Build flowToDefinition**

```javascript
export function flowToDefinition(nodes, edges) {
  // Find trigger node
  const triggerNode = nodes.find(n => n.type === 'triggerNode');
  const triggerConfig = triggerNode?.data?.trigger || { type: 'manual' };

  // Build adjacency from edges
  const children = {};
  edges.forEach(e => {
    if (!children[e.source]) children[e.source] = [];
    children[e.source].push(e.target);
  });

  // Reconstruct step sequence from trigger
  function buildSteps(parentId) {
    const childIds = children[parentId] || [];
    return childIds.map(childId => {
      const node = nodes.find(n => n.id === childId);
      if (!node) return null;
      const step = { ...node.data.step, id: node.id };

      // Recurse for nested steps
      const subChildIds = children[childId] || [];
      if (subChildIds.length > 0 && ['for_each', 'parallel'].includes(step.type)) {
        step.steps = buildSteps(childId);
      }
      return step;
    }).filter(Boolean);
  }

  const steps = buildSteps(triggerNode?.id || 'trigger-root');

  return { definition: { steps }, triggerConfig };
}
```

- [ ] **Step 3: Commit**

```bash
git add apps/web/src/components/workflows/WorkflowAdapter.js
git commit -m "feat: add workflow adapter for JSON <-> ReactFlow conversion"
```

---

### Task 7: Custom Node Components

**Files:**
- Create: `apps/web/src/components/workflows/nodes/TriggerNode.js`
- Create: `apps/web/src/components/workflows/nodes/StepNode.js`
- Create: `apps/web/src/components/workflows/nodes/ConditionNode.js`
- Create: `apps/web/src/components/workflows/nodes/ForEachNode.js`
- Create: `apps/web/src/components/workflows/nodes/ParallelNode.js`
- Create: `apps/web/src/components/workflows/nodes/ApprovalNode.js`

- [ ] **Step 1: Create TriggerNode**

Ocean blue pill shape. Displays trigger type and schedule. Single output handle at bottom.

```javascript
import React from 'react';
import { Handle, Position } from 'reactflow';
import { FiClock, FiZap, FiPlay, FiGlobe } from 'react-icons/fi';

const TRIGGER_ICONS = {
  cron: FiClock, interval: FiClock, webhook: FiGlobe,
  event: FiZap, manual: FiPlay, agent: FiZap,
};

const TRIGGER_LABELS = {
  cron: (t) => `Cron: ${t.schedule || 'not set'}`,
  interval: (t) => `Every ${t.interval_minutes || '?'} min`,
  webhook: () => 'Webhook trigger',
  event: (t) => `On: ${t.event_type || 'event'}`,
  manual: () => 'Manual trigger',
  agent: () => 'Agent trigger',
};

export default function TriggerNode({ data }) {
  const trigger = data.trigger || { type: 'manual' };
  const Icon = TRIGGER_ICONS[trigger.type] || FiPlay;
  const label = (TRIGGER_LABELS[trigger.type] || (() => trigger.type))(trigger);

  return (
    <div className="workflow-node trigger-node">
      <div className="node-header">
        <Icon size={14} />
        <span className="node-title">{label}</span>
      </div>
      <Handle type="source" position={Position.Bottom} />
    </div>
  );
}
```

- [ ] **Step 2: Create StepNode**

Teal rectangle for MCP tools, purple for agents. Shows tool name, param summary, output variable, integration badge.

```javascript
import React from 'react';
import { Handle, Position } from 'reactflow';
import { FiTool, FiCpu, FiCode, FiClock, FiGlobe, FiServer } from 'react-icons/fi';

const TYPE_CONFIG = {
  mcp_tool: { icon: FiTool, color: '#0d9488', label: (s) => s.tool || 'Tool' },
  agent: { icon: FiCpu, color: '#7c3aed', label: (s) => `${s.agent || 'Luna'}: ${(s.prompt || '').slice(0, 30)}...` },
  transform: { icon: FiCode, color: '#9ca3af', label: (s) => s.operation || 'Transform' },
  wait: { icon: FiClock, color: '#6b7280', label: (s) => `Wait ${s.duration || ''}` },
  webhook_trigger: { icon: FiGlobe, color: '#6b7280', label: () => 'Webhook' },
  continue_as_new: { icon: FiServer, color: '#6b7280', label: () => 'Restart workflow' },
  cli_execute: { icon: FiCode, color: '#7c3aed', label: () => 'Code CLI' },
  internal_api: { icon: FiServer, color: '#0d9488', label: (s) => `API: ${s.path || ''}` },
};

export default function StepNode({ data, selected }) {
  const step = data.step || {};
  const config = TYPE_CONFIG[step.type] || TYPE_CONFIG.mcp_tool;
  const Icon = config.icon;

  return (
    <div className={`workflow-node step-node ${selected ? 'selected' : ''}`}
         style={{ borderColor: config.color }}>
      <Handle type="target" position={Position.Top} />
      <div className="node-header">
        <Icon size={14} style={{ color: config.color }} />
        <span className="node-title">{step.id || 'Step'}</span>
      </div>
      <div className="node-body">
        <span className="node-label">{config.label(step)}</span>
        {step.output && <span className="node-output-chip">{`{{${step.output}}}`}</span>}
      </div>
      {data.integrationStatus && (
        <span className={`integration-badge ${data.integrationStatus.connected ? 'connected' : 'disconnected'}`}>
          {data.integrationStatus.name}
        </span>
      )}
      <Handle type="source" position={Position.Bottom} />
    </div>
  );
}
```

- [ ] **Step 3: Create ConditionNode**

Diamond shape (CSS rotated square). Amber color. Two output handles: bottom-left (then/green) and bottom-right (else/red).

```javascript
import React from 'react';
import { Handle, Position } from 'reactflow';
import { FiGitBranch } from 'react-icons/fi';

export default function ConditionNode({ data, selected }) {
  const step = data.step || {};
  const expression = step.if || 'condition';

  return (
    <div className={`workflow-node condition-node ${selected ? 'selected' : ''}`}>
      <Handle type="target" position={Position.Top} />
      <div className="condition-diamond">
        <FiGitBranch size={14} />
        <span className="node-title">{expression}</span>
      </div>
      <Handle type="source" position={Position.Bottom} id="then"
              style={{ left: '30%' }} />
      <Handle type="source" position={Position.Bottom} id="else"
              style={{ left: '70%' }} />
      <div className="condition-labels">
        <span className="then-label">Then</span>
        <span className="else-label">Else</span>
      </div>
    </div>
  );
}
```

- [ ] **Step 4: Create ForEachNode**

Green rounded rect with loop badge. Single input, single output. Displays collection variable and item alias.

```javascript
import React from 'react';
import { Handle, Position } from 'reactflow';
import { FiRepeat } from 'react-icons/fi';

export default function ForEachNode({ data, selected }) {
  const step = data.step || {};

  return (
    <div className={`workflow-node foreach-node ${selected ? 'selected' : ''}`}>
      <Handle type="target" position={Position.Top} />
      <div className="node-header">
        <FiRepeat size={14} />
        <span className="node-title">{step.id || 'Loop'}</span>
        <span className="loop-badge">LOOP</span>
      </div>
      <div className="node-body">
        <span>for each <strong>{step.as || 'item'}</strong> in {step.collection || '...'}</span>
        <span className="substep-count">{(step.steps || []).length} sub-steps</span>
      </div>
      <Handle type="source" position={Position.Bottom} />
    </div>
  );
}
```

- [ ] **Step 5: Create ParallelNode and ApprovalNode**

Similar patterns — ParallelNode is cyan hexagon with fan-out, ApprovalNode is orange with pending/approved state.

- [ ] **Step 6: Create shared CSS**

Create `apps/web/src/components/workflows/nodes/WorkflowNodes.css` with Ocean theme styling: glassmorphic backgrounds, backdrop blur, grid canvas, hover transitions.

- [ ] **Step 7: Commit**

```bash
git add apps/web/src/components/workflows/nodes/
git commit -m "feat: add custom ReactFlow node components for workflow builder"
```

---

### Task 8: WorkflowCanvas Component

**Files:**
- Create: `apps/web/src/components/workflows/WorkflowCanvas.js`

- [ ] **Step 1: Build the canvas**

ReactFlow canvas with custom node types registered, dagre auto-layout, drag-and-drop from palette, and edge connection handling.

```javascript
import React, { useCallback, useMemo } from 'react';
import ReactFlow, {
  Background, Controls, MiniMap,
  useNodesState, useEdgesState, addEdge,
} from 'reactflow';
import 'reactflow/dist/style.css';

import TriggerNode from './nodes/TriggerNode';
import StepNode from './nodes/StepNode';
import ConditionNode from './nodes/ConditionNode';
import ForEachNode from './nodes/ForEachNode';
import ParallelNode from './nodes/ParallelNode';
import ApprovalNode from './nodes/ApprovalNode';

const nodeTypes = {
  triggerNode: TriggerNode,
  stepNode: StepNode,
  conditionNode: ConditionNode,
  forEachNode: ForEachNode,
  parallelNode: ParallelNode,
  approvalNode: ApprovalNode,
};

export default function WorkflowCanvas({
  nodes, edges, onNodesChange, onEdgesChange, setEdges,
  onNodeClick, onDrop, onDragOver,
}) {
  const onConnect = useCallback(
    (params) => setEdges((eds) => addEdge({
      ...params,
      animated: false,
      style: { stroke: '#64748b' },
    }, eds)),
    [setEdges]
  );

  return (
    <div className="workflow-canvas" onDrop={onDrop} onDragOver={onDragOver}>
      <ReactFlow
        nodes={nodes}
        edges={edges}
        onNodesChange={onNodesChange}
        onEdgesChange={onEdgesChange}
        onConnect={onConnect}
        onNodeClick={onNodeClick}
        nodeTypes={nodeTypes}
        fitView
        deleteKeyCode="Delete"
        className="ocean-canvas"
      >
        <Background color="#334155" gap={20} size={1} />
        <Controls />
        <MiniMap nodeStrokeColor="#64748b" nodeColor="#1e293b" />
      </ReactFlow>
    </div>
  );
}
```

- [ ] **Step 2: Commit**

```bash
git add apps/web/src/components/workflows/WorkflowCanvas.js
git commit -m "feat: add WorkflowCanvas component with ReactFlow integration"
```

---

### Task 9: StepPalette — Left Sidebar

**Files:**
- Create: `apps/web/src/components/workflows/StepPalette.js`

- [ ] **Step 1: Build the palette**

Collapsible categories. Each item is draggable (HTML5 drag). Drop target is the canvas.

```javascript
import React, { useState } from 'react';
import { Accordion } from 'react-bootstrap';
import { FiClock, FiTool, FiCpu, FiGitBranch, FiRepeat, FiPause, FiCheckSquare, FiLayers } from 'react-icons/fi';

const PALETTE_CATEGORIES = [
  {
    key: 'triggers',
    label: 'Triggers',
    items: [
      { type: 'trigger', subtype: 'cron', label: 'Scheduled (Cron)', icon: FiClock },
      { type: 'trigger', subtype: 'webhook', label: 'Webhook', icon: FiTool },
      { type: 'trigger', subtype: 'event', label: 'Event', icon: FiCpu },
      { type: 'trigger', subtype: 'manual', label: 'Manual', icon: FiClock },
    ],
  },
  {
    key: 'tools',
    label: 'MCP Tools',
    items: [], // Populated dynamically from tool registry
  },
  {
    key: 'agents',
    label: 'Agents',
    items: [
      { type: 'agent', subtype: 'luna', label: 'Luna', icon: FiCpu },
      { type: 'agent', subtype: 'code', label: 'Code Agent', icon: FiCpu },
      { type: 'agent', subtype: 'data', label: 'Data Agent', icon: FiCpu },
    ],
  },
  {
    key: 'logic',
    label: 'Logic',
    items: [
      { type: 'condition', label: 'Condition (If/Else)', icon: FiGitBranch },
      { type: 'for_each', label: 'For Each Loop', icon: FiRepeat },
      { type: 'parallel', label: 'Parallel', icon: FiLayers },
    ],
  },
  {
    key: 'flow',
    label: 'Flow Control',
    items: [
      { type: 'wait', label: 'Wait / Delay', icon: FiPause },
      { type: 'human_approval', label: 'Human Approval', icon: FiCheckSquare },
    ],
  },
];

export default function StepPalette({ mcpTools = [] }) {
  const categories = [...PALETTE_CATEGORIES];

  // Populate MCP tools category dynamically
  const toolsCategory = categories.find(c => c.key === 'tools');
  if (toolsCategory) {
    toolsCategory.items = mcpTools.map(tool => ({
      type: 'mcp_tool',
      subtype: tool.name,
      label: tool.name.replace(/_/g, ' ').replace(/\b\w/g, l => l.toUpperCase()),
      icon: FiTool,
    }));
  }

  const onDragStart = (event, item) => {
    event.dataTransfer.setData('application/workflow-step', JSON.stringify(item));
    event.dataTransfer.effectAllowed = 'move';
  };

  return (
    <div className="step-palette">
      <h6 className="palette-title">Steps</h6>
      <Accordion defaultActiveKey={['triggers', 'logic']} alwaysOpen>
        {categories.map(cat => (
          <Accordion.Item key={cat.key} eventKey={cat.key}>
            <Accordion.Header>{cat.label}</Accordion.Header>
            <Accordion.Body>
              {cat.items.map((item, i) => {
                const Icon = item.icon;
                return (
                  <div key={i} className="palette-item"
                       draggable onDragStart={(e) => onDragStart(e, item)}>
                    <Icon size={14} />
                    <span>{item.label}</span>
                  </div>
                );
              })}
              {cat.items.length === 0 && <span className="text-muted">Loading...</span>}
            </Accordion.Body>
          </Accordion.Item>
        ))}
      </Accordion>
    </div>
  );
}
```

- [ ] **Step 2: Commit**

```bash
git add apps/web/src/components/workflows/StepPalette.js
git commit -m "feat: add StepPalette with draggable step types for workflow builder"
```

---

### Task 10: StepInspector — Right Panel

**Files:**
- Create: `apps/web/src/components/workflows/StepInspector.js`

- [ ] **Step 1: Build the inspector**

Right panel that renders when a node is selected. Dynamic form based on step type. Tool picker, agent picker, param editor, variable browser, expression builder, integration status display.

```javascript
import React from 'react';
import { Form, Badge } from 'react-bootstrap';
import { FiX } from 'react-icons/fi';

export default function StepInspector({ node, integrationStatus, onUpdate, onClose }) {
  if (!node) return null;

  const step = node.data?.step || {};
  const trigger = node.data?.trigger;

  const handleChange = (field, value) => {
    onUpdate(node.id, { ...step, [field]: value });
  };

  // Trigger config
  if (node.type === 'triggerNode') {
    return (
      <div className="step-inspector">
        <div className="inspector-header">
          <h6>Trigger Configuration</h6>
          <FiX className="close-btn" onClick={onClose} />
        </div>
        <Form.Group className="mb-3">
          <Form.Label>Type</Form.Label>
          <Form.Select value={trigger?.type || 'manual'}
                       onChange={(e) => onUpdate(node.id, { trigger: { ...trigger, type: e.target.value } })}>
            <option value="manual">Manual</option>
            <option value="cron">Scheduled (Cron)</option>
            <option value="interval">Interval</option>
            <option value="webhook">Webhook</option>
            <option value="event">Event</option>
          </Form.Select>
        </Form.Group>
        {trigger?.type === 'cron' && (
          <Form.Group className="mb-3">
            <Form.Label>Cron Expression</Form.Label>
            <Form.Control value={trigger?.schedule || ''} placeholder="0 8 * * *"
                          onChange={(e) => onUpdate(node.id, { trigger: { ...trigger, schedule: e.target.value } })} />
          </Form.Group>
        )}
      </div>
    );
  }

  // Step config
  return (
    <div className="step-inspector">
      <div className="inspector-header">
        <h6>Step: {step.id || 'Unnamed'}</h6>
        <FiX className="close-btn" onClick={onClose} />
      </div>

      <Form.Group className="mb-3">
        <Form.Label>Step ID</Form.Label>
        <Form.Control value={step.id || ''} onChange={(e) => handleChange('id', e.target.value)} />
      </Form.Group>

      <Form.Group className="mb-3">
        <Form.Label>Type</Form.Label>
        <Form.Select value={step.type || 'mcp_tool'} onChange={(e) => handleChange('type', e.target.value)}>
          <option value="mcp_tool">MCP Tool</option>
          <option value="agent">Agent</option>
          <option value="condition">Condition</option>
          <option value="for_each">For Each</option>
          <option value="parallel">Parallel</option>
          <option value="wait">Wait</option>
          <option value="human_approval">Human Approval</option>
          <option value="transform">Transform</option>
        </Form.Select>
      </Form.Group>

      {/* MCP Tool picker */}
      {step.type === 'mcp_tool' && (
        <>
          <Form.Group className="mb-3">
            <Form.Label>Tool</Form.Label>
            <Form.Control value={step.tool || ''} placeholder="search_emails"
                          onChange={(e) => handleChange('tool', e.target.value)} />
          </Form.Group>
          <Form.Group className="mb-3">
            <Form.Label>Parameters (JSON)</Form.Label>
            <Form.Control as="textarea" rows={4}
                          value={JSON.stringify(step.params || {}, null, 2)}
                          onChange={(e) => {
                            try { handleChange('params', JSON.parse(e.target.value)); } catch {}
                          }} />
          </Form.Group>
        </>
      )}

      {/* Agent config */}
      {step.type === 'agent' && (
        <>
          <Form.Group className="mb-3">
            <Form.Label>Agent</Form.Label>
            <Form.Select value={step.agent || 'luna'} onChange={(e) => handleChange('agent', e.target.value)}>
              <option value="luna">Luna</option>
              <option value="code">Code Agent</option>
              <option value="data">Data Agent</option>
            </Form.Select>
          </Form.Group>
          <Form.Group className="mb-3">
            <Form.Label>Prompt</Form.Label>
            <Form.Control as="textarea" rows={4} value={step.prompt || ''}
                          onChange={(e) => handleChange('prompt', e.target.value)}
                          placeholder="Use {{variable}} to reference previous step outputs" />
          </Form.Group>
        </>
      )}

      {/* Condition */}
      {step.type === 'condition' && (
        <Form.Group className="mb-3">
          <Form.Label>Expression</Form.Label>
          <Form.Control value={step.if || ''} placeholder="{{score.score}} >= 70"
                        onChange={(e) => handleChange('if', e.target.value)} />
        </Form.Group>
      )}

      {/* Output variable */}
      <Form.Group className="mb-3">
        <Form.Label>Output Variable</Form.Label>
        <Form.Control value={step.output || ''} placeholder="result"
                      onChange={(e) => handleChange('output', e.target.value)} />
      </Form.Group>

      {/* Integration status */}
      {integrationStatus && (
        <div className="integration-info mt-3">
          <small className="text-muted">Requires:</small>
          <Badge bg={integrationStatus.connected ? 'success' : 'danger'}>
            {integrationStatus.name} — {integrationStatus.connected ? 'Connected' : 'Not connected'}
          </Badge>
        </div>
      )}
    </div>
  );
}
```

- [ ] **Step 2: Commit**

```bash
git add apps/web/src/components/workflows/StepInspector.js
git commit -m "feat: add StepInspector panel for workflow node configuration"
```

---

### Task 11: WorkflowBuilder — Main Container

**Files:**
- Create: `apps/web/src/components/workflows/WorkflowBuilder.js`

- [ ] **Step 1: Build the builder container**

Main component that wires together: toolbar, palette, canvas, inspector, test console. Manages state, handles save/test/activate, loads workflow from API.

```javascript
import React, { useState, useEffect, useCallback, useRef } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import { Button, Badge, Dropdown, Spinner } from 'react-bootstrap';
import { useNodesState, useEdgesState } from 'reactflow';
import { FiSave, FiPlay, FiPower, FiCode, FiArrowLeft } from 'react-icons/fi';

import WorkflowCanvas from './WorkflowCanvas';
import StepPalette from './StepPalette';
import StepInspector from './StepInspector';
import TestConsole from './TestConsole';
import { definitionToFlow, flowToDefinition } from './WorkflowAdapter';
import dynamicWorkflowService from '../../services/dynamicWorkflowService';

export default function WorkflowBuilder() {
  const { id } = useParams();
  const navigate = useNavigate();

  const [workflow, setWorkflow] = useState(null);
  const [nodes, setNodes, onNodesChange] = useNodesState([]);
  const [edges, setEdges, onEdgesChange] = useEdgesState([]);
  const [selectedNode, setSelectedNode] = useState(null);
  const [showJson, setShowJson] = useState(false);
  const [showTestConsole, setShowTestConsole] = useState(false);
  const [testResults, setTestResults] = useState(null);
  const [saving, setSaving] = useState(false);
  const [integrationStatus, setIntegrationStatus] = useState({});
  const [toolMapping, setToolMapping] = useState({});

  // Load workflow + integrations
  useEffect(() => {
    async function load() {
      const [intStatus, mapping] = await Promise.all([
        dynamicWorkflowService.getIntegrationStatus(),
        dynamicWorkflowService.getToolMapping(),
      ]);
      setIntegrationStatus(intStatus);
      setToolMapping(mapping);

      if (id) {
        const wf = await dynamicWorkflowService.get(id);
        setWorkflow(wf);
        const { nodes: n, edges: e } = definitionToFlow(wf.definition, wf.trigger_config);
        setNodes(n);
        setEdges(e);
      } else {
        // New workflow — start with just a trigger node
        setNodes([{
          id: 'trigger-root', type: 'triggerNode',
          data: { trigger: { type: 'manual' } },
          position: { x: 300, y: 50 },
        }]);
        setEdges([]);
      }
    }
    load();
  }, [id]);

  // Save
  const handleSave = async () => {
    setSaving(true);
    const { definition, triggerConfig } = flowToDefinition(nodes, edges);
    const payload = {
      name: workflow?.name || 'Untitled Workflow',
      description: workflow?.description || '',
      definition,
      trigger_config: triggerConfig,
    };
    if (id) {
      await dynamicWorkflowService.update(id, payload);
    } else {
      const created = await dynamicWorkflowService.create(payload);
      navigate(`/workflows/builder/${created.id}`, { replace: true });
      setWorkflow(created);
    }
    setSaving(false);
  };

  // Test (dry run)
  const handleTest = async () => {
    setShowTestConsole(true);
    const results = await dynamicWorkflowService.dryRun(id || workflow?.id, {});
    setTestResults(results);
  };

  // Activate
  const handleActivate = async () => {
    await dynamicWorkflowService.activate(id);
    setWorkflow(prev => ({ ...prev, status: 'active' }));
  };

  // Drop handler for palette items
  const onDrop = useCallback((event) => {
    event.preventDefault();
    const data = JSON.parse(event.dataTransfer.getData('application/workflow-step'));
    const position = { x: event.clientX - 250, y: event.clientY - 100 };
    const newId = `${data.type}-${Date.now()}`;
    const newNode = {
      id: newId,
      type: data.type === 'trigger' ? 'triggerNode' :
            data.type === 'condition' ? 'conditionNode' :
            data.type === 'for_each' ? 'forEachNode' :
            data.type === 'parallel' ? 'parallelNode' :
            data.type === 'human_approval' ? 'approvalNode' : 'stepNode',
      data: {
        step: { id: newId, type: data.type, tool: data.subtype || '', params: {}, output: '' },
      },
      position,
    };
    setNodes((nds) => [...nds, newNode]);
  }, [setNodes]);

  const onDragOver = useCallback((event) => {
    event.preventDefault();
    event.dataTransfer.dropEffect = 'move';
  }, []);

  // Node selection
  const onNodeClick = useCallback((_, node) => {
    setSelectedNode(node);
  }, []);

  // Update node data from inspector
  const handleNodeUpdate = useCallback((nodeId, updatedData) => {
    setNodes((nds) => nds.map(n =>
      n.id === nodeId ? { ...n, data: { ...n.data, step: updatedData.step || updatedData, trigger: updatedData.trigger } } : n
    ));
  }, [setNodes]);

  // Get integration info for selected node
  const getNodeIntegration = () => {
    if (!selectedNode?.data?.step?.tool) return null;
    const intName = toolMapping[selectedNode.data.step.tool];
    if (!intName) return null;
    return integrationStatus[intName] || null;
  };

  // Count integration requirements
  const integrationPill = () => {
    const required = new Set();
    nodes.forEach(n => {
      const tool = n.data?.step?.tool;
      if (tool && toolMapping[tool]) required.add(toolMapping[tool]);
    });
    const connected = [...required].filter(r => integrationStatus[r]?.connected).length;
    return { connected, total: required.size };
  };
  const pill = integrationPill();

  return (
    <div className="workflow-builder">
      {/* Toolbar */}
      <div className="builder-toolbar">
        <Button variant="link" onClick={() => navigate('/workflows')}>
          <FiArrowLeft /> Back
        </Button>
        <input className="workflow-name-input"
               value={workflow?.name || 'Untitled Workflow'}
               onChange={(e) => setWorkflow(prev => ({ ...prev, name: e.target.value }))} />
        <Badge bg={workflow?.status === 'active' ? 'success' : 'secondary'}>
          {workflow?.status || 'draft'}
        </Badge>

        {pill.total > 0 && (
          <Badge bg={pill.connected === pill.total ? 'success' : 'warning'}>
            Integrations: {pill.connected}/{pill.total}
          </Badge>
        )}

        <div className="toolbar-actions">
          <Button variant="outline-secondary" size="sm" onClick={() => setShowJson(!showJson)}>
            <FiCode /> JSON
          </Button>
          <Button variant="outline-info" size="sm" onClick={handleTest} disabled={!id}>
            <FiPlay /> Test
          </Button>
          <Button variant="primary" size="sm" onClick={handleSave} disabled={saving}>
            {saving ? <Spinner size="sm" /> : <><FiSave /> Save</>}
          </Button>
          <Button variant="success" size="sm" onClick={handleActivate}
                  disabled={!id || workflow?.status === 'active' || pill.connected < pill.total}>
            <FiPower /> Activate
          </Button>
        </div>
      </div>

      {/* Main layout: palette | canvas | inspector */}
      <div className="builder-layout">
        <StepPalette />
        <WorkflowCanvas
          nodes={nodes} edges={edges}
          onNodesChange={onNodesChange} onEdgesChange={onEdgesChange}
          onNodeClick={onNodeClick} onDrop={onDrop} onDragOver={onDragOver}
        />
        <StepInspector
          node={selectedNode}
          integrationStatus={getNodeIntegration()}
          onUpdate={handleNodeUpdate}
          onClose={() => setSelectedNode(null)}
        />
      </div>

      {/* JSON toggle */}
      {showJson && (
        <div className="json-editor">
          <pre>{JSON.stringify(flowToDefinition(nodes, edges), null, 2)}</pre>
        </div>
      )}

      {/* Test console */}
      {showTestConsole && <TestConsole results={testResults} onClose={() => setShowTestConsole(false)} />}
    </div>
  );
}
```

- [ ] **Step 2: Commit**

```bash
git add apps/web/src/components/workflows/WorkflowBuilder.js
git commit -m "feat: add WorkflowBuilder main container component"
```

---

### Task 12: TestConsole Component

**Files:**
- Create: `apps/web/src/components/workflows/TestConsole.js`

- [ ] **Step 1: Build test console**

Bottom panel showing dry-run validation results: steps planned, integrations required, validation errors.

```javascript
import React from 'react';
import { Badge, Alert, ListGroup } from 'react-bootstrap';
import { FiX, FiCheckCircle, FiAlertCircle } from 'react-icons/fi';

export default function TestConsole({ results, onClose }) {
  if (!results) return (
    <div className="test-console">
      <div className="console-header">
        <span>Test Console</span>
        <FiX onClick={onClose} className="close-btn" />
      </div>
      <div className="console-body text-muted">Running validation...</div>
    </div>
  );

  const hasErrors = results.validation_errors?.length > 0;

  return (
    <div className="test-console">
      <div className="console-header">
        <span>Test Console</span>
        <Badge bg={hasErrors ? 'danger' : 'success'}>
          {hasErrors ? 'Errors Found' : 'Valid'}
        </Badge>
        <FiX onClick={onClose} className="close-btn" />
      </div>
      <div className="console-body">
        {hasErrors && (
          <Alert variant="danger">
            {results.validation_errors.map((err, i) => <div key={i}><FiAlertCircle /> {err}</div>)}
          </Alert>
        )}

        <h6>Execution Plan ({results.step_count} steps)</h6>
        <ListGroup variant="flush">
          {(results.steps_planned || []).map((step, i) => (
            <ListGroup.Item key={i} className="d-flex align-items-center gap-2">
              <Badge bg="secondary">{i + 1}</Badge>
              <span>{step.type}</span>
              {step.tool && <Badge bg="info">{step.tool}</Badge>}
              {step.agent && <Badge bg="primary">{step.agent}</Badge>}
              <FiCheckCircle className="text-success ms-auto" />
            </ListGroup.Item>
          ))}
        </ListGroup>

        {results.integrations_required?.length > 0 && (
          <>
            <h6 className="mt-3">Required Integrations</h6>
            {results.integrations_required.map((int, i) => (
              <Badge key={i} bg="outline-secondary" className="me-1">{int}</Badge>
            ))}
          </>
        )}
      </div>
    </div>
  );
}
```

- [ ] **Step 2: Commit**

```bash
git add apps/web/src/components/workflows/TestConsole.js
git commit -m "feat: add TestConsole for workflow dry-run validation display"
```

---

### Task 13: Update Frontend Service Layer

**Files:**
- Modify: `apps/web/src/services/dynamicWorkflowService.js`

- [ ] **Step 1: Add NEW API methods only**

The service already has `list`, `create`, `activate`, `pause`, `run`, `listRuns`, `getRun`, `installTemplate`, `browseTemplates`. Only add the methods that don't exist yet:

```javascript
// Add these NEW methods to the existing dynamicWorkflowService:

async get(id) {
  const response = await api.get(`/dynamic-workflows/${id}`);
  return response.data;
},

async update(id, data) {
  const response = await api.put(`/dynamic-workflows/${id}`, data);
  return response.data;
},

async delete(id) {
  const response = await api.delete(`/dynamic-workflows/${id}`);
  return response.data;
},

async dryRun(id, inputData) {
  const response = await api.post(`/dynamic-workflows/${id}/run`, {
    input_data: inputData,
    dry_run: true,
  });
  return response.data;
},

async getIntegrationStatus() {
  const response = await api.get('/integrations/status');
  return response.data;
},

async getToolMapping() {
  const response = await api.get('/integrations/tool-mapping');
  return response.data;
},
```

- [ ] **Step 2: Commit**

```bash
git add apps/web/src/services/dynamicWorkflowService.js
git commit -m "feat: add update, delete, dryRun, integration API methods to workflow service"
```

---

### Task 14: Add Builder Route to App.js

**Files:**
- Modify: `apps/web/src/App.js`

- [ ] **Step 1: Add route**

```javascript
import WorkflowBuilder from './components/workflows/WorkflowBuilder';

// Inside routes:
<Route path="/workflows/builder/:id?" element={<ProtectedRoute><WorkflowBuilder /></ProtectedRoute>} />
```

- [ ] **Step 2: Commit**

```bash
git add apps/web/src/App.js
git commit -m "feat: add /workflows/builder route for visual workflow editor"
```

---

## Phase 3: Workflows Page Restructure

### Task 15: Restructure WorkflowsPage Tabs

**Files:**
- Modify: `apps/web/src/pages/WorkflowsPage.js`
- Create: `apps/web/src/components/workflows/TemplatesTab.js`
- Create: `apps/web/src/components/workflows/RunsTab.js`

- [ ] **Step 1: Create TemplatesTab**

Browse native + community templates. One-click install. Preview button opens read-only builder.

```javascript
import React, { useState, useEffect } from 'react';
import { Card, Row, Col, Button, Badge, Spinner } from 'react-bootstrap';
import { FiDownload, FiEye } from 'react-icons/fi';
import { useNavigate } from 'react-router-dom';
import dynamicWorkflowService from '../../services/dynamicWorkflowService';

export default function TemplatesTab() {
  const [templates, setTemplates] = useState([]);
  const [loading, setLoading] = useState(true);
  const navigate = useNavigate();

  useEffect(() => {
    dynamicWorkflowService.browseTemplates().then(data => {
      setTemplates(data || []);
      setLoading(false);
    });
  }, []);

  const handleInstall = async (templateId) => {
    const installed = await dynamicWorkflowService.installTemplate(templateId);
    navigate(`/workflows/builder/${installed.id}`);
  };

  if (loading) return <Spinner />;

  return (
    <Row xs={1} md={2} lg={3} className="g-3">
      {templates.map(t => (
        <Col key={t.id}>
          <Card className="glass-card h-100">
            <Card.Body>
              <Card.Title>{t.name}</Card.Title>
              <Card.Text className="text-muted">{t.description}</Card.Text>
              <div className="d-flex gap-1 mb-2">
                <Badge bg="secondary">{t.trigger_config?.type || 'manual'}</Badge>
                <Badge bg="info">{(t.definition?.steps || []).length} steps</Badge>
                <Badge bg="primary">{t.tier}</Badge>
              </div>
            </Card.Body>
            <Card.Footer className="d-flex gap-2">
              <Button variant="outline-primary" size="sm" onClick={() => handleInstall(t.id)}>
                <FiDownload /> Install
              </Button>
              <Button variant="outline-secondary" size="sm">
                <FiEye /> Preview
              </Button>
            </Card.Footer>
          </Card>
        </Col>
      ))}
    </Row>
  );
}
```

- [ ] **Step 2: Create RunsTab**

Unified execution history. Status badges, duration/cost columns, filters, click to expand run detail.

```javascript
import React, { useState, useEffect } from 'react';
import { Table, Badge, Button, Form, Spinner } from 'react-bootstrap';
import { FiRefreshCw, FiPlay, FiClock } from 'react-icons/fi';
import RunTreeView from './RunTreeView';
import dynamicWorkflowService from '../../services/dynamicWorkflowService';

const STATUS_COLORS = {
  running: 'primary', completed: 'success', failed: 'danger', cancelled: 'secondary',
};

export default function RunsTab({ workflows }) {
  const [runs, setRuns] = useState([]);
  const [loading, setLoading] = useState(true);
  const [selectedRun, setSelectedRun] = useState(null);
  const [statusFilter, setStatusFilter] = useState('all');

  useEffect(() => {
    loadRuns();
  }, [workflows]);

  const loadRuns = async () => {
    setLoading(true);
    // Load runs across all workflows
    const allRuns = [];
    for (const wf of (workflows || [])) {
      const wfRuns = await dynamicWorkflowService.listRuns(wf.id, 20);
      allRuns.push(...(wfRuns || []).map(r => ({ ...r, workflow_name: wf.name })));
    }
    allRuns.sort((a, b) => new Date(b.started_at) - new Date(a.started_at));
    setRuns(allRuns);
    setLoading(false);
  };

  const filtered = statusFilter === 'all' ? runs : runs.filter(r => r.status === statusFilter);

  if (selectedRun) {
    return <RunTreeView run={selectedRun} onBack={() => setSelectedRun(null)} />;
  }

  return (
    <>
      <div className="d-flex gap-2 mb-3">
        <Form.Select size="sm" style={{ width: 150 }} value={statusFilter}
                     onChange={(e) => setStatusFilter(e.target.value)}>
          <option value="all">All Status</option>
          <option value="running">Running</option>
          <option value="completed">Completed</option>
          <option value="failed">Failed</option>
        </Form.Select>
        <Button variant="outline-secondary" size="sm" onClick={loadRuns}>
          <FiRefreshCw /> Refresh
        </Button>
      </div>

      {loading ? <Spinner /> : (
        <Table hover className="glass-table">
          <thead>
            <tr>
              <th>Workflow</th><th>Status</th><th>Trigger</th>
              <th>Duration</th><th>Cost</th><th>Started</th><th></th>
            </tr>
          </thead>
          <tbody>
            {filtered.map(run => (
              <tr key={run.id} onClick={() => setSelectedRun(run)} style={{ cursor: 'pointer' }}>
                <td>{run.workflow_name}</td>
                <td><Badge bg={STATUS_COLORS[run.status]}>{run.status}</Badge></td>
                <td>{run.trigger_type}</td>
                <td>{run.duration_ms ? `${(run.duration_ms / 1000).toFixed(1)}s` : '—'}</td>
                <td>{run.total_cost_usd ? `$${run.total_cost_usd.toFixed(4)}` : '—'}</td>
                <td>{new Date(run.started_at).toLocaleString()}</td>
                <td><Button variant="link" size="sm"><FiPlay /> Re-run</Button></td>
              </tr>
            ))}
          </tbody>
        </Table>
      )}
    </>
  );
}
```

- [ ] **Step 3: Restructure WorkflowsPage.js tabs**

Replace the current 3-tab structure (Executions | Dynamic | Designs) with:

```javascript
// New tab structure in WorkflowsPage.js:
const TABS = [
  { key: 'workflows', label: 'My Workflows' },
  { key: 'templates', label: 'Templates' },
  { key: 'runs', label: 'Runs' },
];

// In render:
<Nav variant="tabs">
  {TABS.map(tab => (
    <Nav.Link key={tab.key} active={activeTab === tab.key}
              onClick={() => setActiveTab(tab.key)}>
      {tab.label}
    </Nav.Link>
  ))}
</Nav>

{activeTab === 'workflows' && <DynamicWorkflowsTab />}
{activeTab === 'templates' && <TemplatesTab />}
{activeTab === 'runs' && <RunsTab workflows={workflows} />}
```

Remove the legacy `WORKFLOW_DEFINITIONS` array and `Designs` tab. Remove the old `Executions` tab — it's replaced by RunsTab.

Add "New Workflow" and "Edit" buttons that navigate to `/workflows/builder/:id`.

- [ ] **Step 4: Commit**

```bash
git add apps/web/src/pages/WorkflowsPage.js apps/web/src/components/workflows/TemplatesTab.js apps/web/src/components/workflows/RunsTab.js
git commit -m "feat: restructure WorkflowsPage with My Workflows, Templates, Runs tabs"
```

---

## Phase 4: Execution UI

### Task 16: RunTreeView — Live Execution Visualization

**Files:**
- Create: `apps/web/src/components/workflows/RunTreeView.js`
- Create: `apps/web/src/components/workflows/RunStepDetail.js`

- [ ] **Step 1: Build RunTreeView**

Read-only ReactFlow canvas rendering the workflow tree with execution status colors. Nodes change color based on step status. Polling updates every 3 seconds for active runs.

```javascript
import React, { useState, useEffect, useCallback } from 'react';
import ReactFlow, { Background, Controls } from 'reactflow';
import { Button, Badge, ProgressBar } from 'react-bootstrap';
import { FiArrowLeft } from 'react-icons/fi';
import { definitionToFlow } from './WorkflowAdapter';
import RunStepDetail from './RunStepDetail';
import dynamicWorkflowService from '../../services/dynamicWorkflowService';

// Import same node types but with execution status overlay
import TriggerNode from './nodes/TriggerNode';
import StepNode from './nodes/StepNode';
import ConditionNode from './nodes/ConditionNode';
import ForEachNode from './nodes/ForEachNode';
import ParallelNode from './nodes/ParallelNode';
import ApprovalNode from './nodes/ApprovalNode';

const nodeTypes = { triggerNode: TriggerNode, stepNode: StepNode, conditionNode: ConditionNode, forEachNode: ForEachNode, parallelNode: ParallelNode, approvalNode: ApprovalNode };

const STATUS_STYLES = {
  pending: { border: '2px solid #64748b' },
  running: { border: '2px solid #3b82f6', boxShadow: '0 0 12px rgba(59,130,246,0.5)' },
  completed: { border: '2px solid #22c55e' },
  failed: { border: '2px solid #ef4444' },
  waiting: { border: '2px solid #eab308' },
};

export default function RunTreeView({ run, onBack }) {
  const [runDetail, setRunDetail] = useState(run);
  const [nodes, setNodes] = useState([]);
  const [edges, setEdges] = useState([]);
  const [selectedStep, setSelectedStep] = useState(null);

  // Load full run details
  useEffect(() => {
    async function load() {
      const detail = await dynamicWorkflowService.getRun(run.id);
      setRunDetail(detail);
      applyRunStatus(detail);
    }
    load();
  }, [run.id]);

  // Poll for running workflows
  useEffect(() => {
    if (runDetail?.status !== 'running') return;
    const interval = setInterval(async () => {
      const detail = await dynamicWorkflowService.getRun(run.id);
      setRunDetail(detail);
      applyRunStatus(detail);
      if (detail.status !== 'running') clearInterval(interval);
    }, 3000);
    return () => clearInterval(interval);
  }, [runDetail?.status]);

  const applyRunStatus = (detail) => {
    if (!detail?.step_results) return;
    // Build nodes from the workflow definition, overlay step statuses
    const wfDef = detail.definition || { steps: [] };
    const { nodes: baseNodes, edges: baseEdges } = definitionToFlow(wfDef, detail.trigger_config);

    const stepLogs = detail.step_logs || [];
    const statusMap = {};
    stepLogs.forEach(log => { statusMap[log.step_id] = log; });

    const styledNodes = baseNodes.map(n => ({
      ...n,
      style: STATUS_STYLES[statusMap[n.id]?.status || 'pending'],
      data: { ...n.data, executionStatus: statusMap[n.id] },
    }));

    setNodes(styledNodes);
    setEdges(baseEdges);
  };

  const stepsCompleted = (runDetail?.step_logs || []).filter(s => s.status === 'completed').length;
  const stepsTotal = (runDetail?.step_logs || []).length || nodes.length;

  return (
    <div className="run-tree-view">
      {/* Run summary bar */}
      <div className="run-summary-bar">
        <Button variant="link" onClick={onBack}><FiArrowLeft /> Back to Runs</Button>
        <Badge bg={run.status === 'completed' ? 'success' : run.status === 'failed' ? 'danger' : 'primary'}>
          {run.status}
        </Badge>
        <span>Steps: {stepsCompleted}/{stepsTotal}</span>
        <ProgressBar now={(stepsCompleted / Math.max(stepsTotal, 1)) * 100} className="flex-grow-1" />
        {runDetail?.duration_ms && <span>{(runDetail.duration_ms / 1000).toFixed(1)}s</span>}
        {runDetail?.total_cost_usd && <span>${runDetail.total_cost_usd.toFixed(4)}</span>}
      </div>

      {/* Tree + detail layout */}
      <div className="run-layout">
        <div className="run-canvas">
          <ReactFlow
            nodes={nodes} edges={edges}
            nodeTypes={nodeTypes}
            onNodeClick={(_, node) => setSelectedStep(node.data.executionStatus)}
            fitView nodesDraggable={false} nodesConnectable={false}
            elementsSelectable={true}
          >
            <Background color="#334155" gap={20} />
            <Controls showInteractive={false} />
          </ReactFlow>
        </div>
        {selectedStep && (
          <RunStepDetail step={selectedStep} onClose={() => setSelectedStep(null)} />
        )}
      </div>
    </div>
  );
}
```

- [ ] **Step 2: Build RunStepDetail**

```javascript
import React from 'react';
import { Badge } from 'react-bootstrap';
import { FiX, FiClock, FiDollarSign, FiCpu } from 'react-icons/fi';

export default function RunStepDetail({ step, onClose }) {
  return (
    <div className="run-step-detail">
      <div className="detail-header">
        <h6>{step.step_id} <Badge bg="secondary">{step.step_type}</Badge></h6>
        <FiX onClick={onClose} className="close-btn" />
      </div>

      <div className="detail-row">
        <Badge bg={step.status === 'completed' ? 'success' : step.status === 'failed' ? 'danger' : 'primary'}>
          {step.status}
        </Badge>
      </div>

      {step.duration_ms != null && (
        <div className="detail-row"><FiClock /> {step.duration_ms}ms</div>
      )}
      {step.tokens_used > 0 && (
        <div className="detail-row"><FiCpu /> {step.tokens_used} tokens</div>
      )}
      {step.cost_usd > 0 && (
        <div className="detail-row"><FiDollarSign /> ${step.cost_usd.toFixed(4)}</div>
      )}
      {step.retry_count > 0 && (
        <div className="detail-row">Retries: {step.retry_count}</div>
      )}
      {step.platform && (
        <div className="detail-row">Platform: {step.platform}</div>
      )}

      {step.input_data && (
        <>
          <h6 className="mt-3">Input</h6>
          <pre className="code-block">{JSON.stringify(step.input_data, null, 2)}</pre>
        </>
      )}
      {step.output_data && (
        <>
          <h6>Output</h6>
          <pre className="code-block">{JSON.stringify(step.output_data, null, 2)}</pre>
        </>
      )}
      {step.error && (
        <>
          <h6>Error</h6>
          <pre className="code-block error">{step.error}</pre>
        </>
      )}
    </div>
  );
}
```

- [ ] **Step 3: Commit**

```bash
git add apps/web/src/components/workflows/RunTreeView.js apps/web/src/components/workflows/RunStepDetail.js
git commit -m "feat: add execution tree visualization with live status and step detail"
```

---

## Phase 5: New Step Types for Migration

### Task 17: Add `continue_as_new` Step Type

**Files:**
- Modify: `apps/api/app/workflows/dynamic_executor.py`
- Modify: `apps/api/app/schemas/dynamic_workflow.py`

- [ ] **Step 1: Update schema to include new step types**

Add `continue_as_new`, `cli_execute`, `internal_api` to the step type comment/validation in the schema.

- [ ] **Step 2: Add continue_as_new handling in executor**

In `DynamicWorkflowExecutor.run()`, after processing all steps, check if the last step is `continue_as_new`:

```python
# At end of step loop:
last_step = input.definition["steps"][-1] if input.definition["steps"] else None
if last_step and last_step.get("type") == "continue_as_new":
    interval = last_step.get("interval_seconds", 900)  # default 15 min
    await workflow.sleep(timedelta(seconds=interval))
    workflow.continue_as_new(input)
```

- [ ] **Step 3: Commit**

```bash
git add apps/api/app/workflows/dynamic_executor.py apps/api/app/schemas/dynamic_workflow.py
git commit -m "feat: add continue_as_new step type for infinite-duration workflows"
```

---

### Task 18: Add `cli_execute` and `internal_api` Step Types

**Files:**
- Modify: `apps/api/app/workflows/activities/dynamic_step.py`

- [ ] **Step 1: Add cli_execute handler**

Dispatches a child workflow on `servicetsunami-code` queue:

```python
elif step_type == "cli_execute":
    activity.heartbeat("Dispatching CLI execution")
    # This step type is handled in the executor via child workflow
    # The activity just validates params
    return {"delegated_to": "servicetsunami-code", "task": params.get("task", "")}
```

In the executor, add cli_execute handling:

```python
elif step["type"] == "cli_execute":
    from app.workflows.code_task import CodeTaskWorkflow
    result = await workflow.execute_child_workflow(
        CodeTaskWorkflow.run,
        args=[{
            "tenant_id": input.tenant_id,
            "task": resolve_template(step.get("task", ""), ctx.snapshot()),
            "repo_url": step.get("repo_url", ""),
        }],
        id=f"{workflow.info().workflow_id}-cli-{step['id']}",
        task_queue="servicetsunami-code",
    )
    ctx.set(step["id"], result)
```

- [ ] **Step 2: Add internal_api handler**

```python
elif step_type == "internal_api":
    activity.heartbeat(f"Calling internal API: {params.get('path', '')}")
    method = params.get("method", "GET").lower()
    path = params.get("path", "")
    body = resolve_templates(params.get("body", {}), context)

    async with httpx.AsyncClient(timeout=25.0) as client:
        resp = await getattr(client, method)(
            f"{API_BASE_URL}/api/v1{path}",
            headers={
                "X-Internal-Key": API_INTERNAL_KEY,
                "X-Tenant-Id": tenant_id,
            },
            json=body if method in ("post", "put") else None,
            params=body if method == "get" else None,
        )
        return resp.json()
```

- [ ] **Step 3: Commit**

```bash
git add apps/api/app/workflows/activities/dynamic_step.py apps/api/app/workflows/dynamic_executor.py
git commit -m "feat: add cli_execute and internal_api step types for Tier 3-4 migration"
```

---

## Phase 6: RL Wiring

### Task 19: Run-Level and Step-Level RL Scoring

**Files:**
- Modify: `apps/api/app/workflows/activities/dynamic_step.py`
- Modify: `apps/api/app/workflows/dynamic_executor.py`

- [ ] **Step 1: Add RL experience logging after workflow run completes**

In `finalize_workflow_run()` activity, after persisting the run result, log an RL experience.

**Note on function names**: verify the actual function signatures before calling — the codebase uses `log_experience()` in `rl_experience_service.py`:

```python
from app.services.rl_experience_service import log_experience

# After updating workflow_run status:
log_experience(
    db=db,
    tenant_id=tenant_id,
    decision_point="workflow_execution",
    state_text=f"workflow:{workflow_id} steps:{step_count} trigger:{trigger_type}",
    action_text=f"platforms:{platforms_used} tools:{tools_called}",
    reward=1.0 if status == "completed" else 0.0,
    reward_components={
        "success": status == "completed",
        "duration_ms": duration_ms,
        "cost_usd": total_cost,
        "steps_completed": steps_completed,
        "steps_total": steps_total,
        "trigger_type": trigger_type,
    },
    reward_source="workflow_run",
)
```

- [ ] **Step 2: Add step-level RL logging**

In `execute_dynamic_step()`, after each step completes, log step-level RL:

```python
# After step execution returns result:
log_experience(
    db=db,
    tenant_id=tenant_id,
    decision_point="workflow_step",
    state_text=f"step_type:{step_type} tool:{step.get('tool','')} position:{step_index}",
    action_text=f"platform:{platform} retries:{retry_count}",
    reward=1.0 if not error else 0.0,
    reward_components={
        "success": not error,
        "duration_ms": duration_ms,
        "tokens": tokens_used,
        "cost_usd": cost_usd,
        "platform": platform or "default",
        "error_type": type(error).__name__ if error else None,
    },
    reward_source="workflow_step",
)
```

- [ ] **Step 3: Add creation event logging**

In the create workflow API endpoint (both user-facing and internal), log workflow creation:

```python
log_experience(
    db=db,
    tenant_id=tenant_id,
    decision_point="workflow_creation",
    state_text=f"trigger:{trigger_type} steps:{step_count} types:{step_types_used}",
    action_text=f"template:{source_template_id or 'custom'} tools:{tools_chosen}",
    reward=0.0,  # Reward computed later based on first-run success
    reward_source="workflow_creation",
)
```

- [ ] **Step 4: Commit**

```bash
git add apps/api/app/workflows/activities/dynamic_step.py apps/api/app/workflows/dynamic_executor.py apps/api/app/api/v1/dynamic_workflows.py
git commit -m "feat: wire RL experience logging for workflow runs, steps, and creation"
```

---

### Task 20: Memory Integration — Workflow Entities

**Files:**
- Modify: `apps/api/app/api/v1/dynamic_workflows.py`

- [ ] **Step 1: Create knowledge entity on workflow creation**

After creating a dynamic workflow, create a knowledge entity.

**Note**: Verify actual function names before calling. The codebase uses `create_entity()` in `knowledge.py` and `log_activity()` in `memory_activity.py`:

```python
from app.services.knowledge import create_entity

# After workflow creation:
create_entity(
    db=db,
    tenant_id=tenant_id,
    name=workflow.name,
    entity_type="workflow",
    category="automation",
    metadata={"workflow_id": str(workflow.id), "trigger": workflow.trigger_config},
)
```

- [ ] **Step 2: Log memory activities on workflow events**

```python
from app.services.memory_activity import log_activity

# On create:
log_activity(db, tenant_id, "workflow_created", {"workflow_id": str(workflow.id), "name": workflow.name})

# On activate:
log_activity(db, tenant_id, "workflow_activated", {"workflow_id": str(workflow.id)})

# On run failure:
log_activity(db, tenant_id, "workflow_failed", {"workflow_id": str(workflow.id), "run_id": str(run.id), "error": error})
```

- [ ] **Step 3: Commit**

```bash
git add apps/api/app/api/v1/dynamic_workflows.py
git commit -m "feat: wire workflow entities and memory activities for knowledge graph"
```

---

## Phase 7: Static Workflow Migration

### Task 21: Migrate Tier 1 — Linear Workflows (5)

**Files:**
- Create: `apps/api/app/services/workflow_templates.py` (add entries to NATIVE_TEMPLATES)

- [ ] **Step 1: Convert follow_up.py to JSON definition**

Read `apps/api/app/workflows/follow_up.py`, extract the activity sequence, write as JSON definition with `wait` + `agent` steps. Add to native templates.

- [ ] **Step 2: Convert monthly_billing.py, dataset_sync.py, data_source_sync.py, embedding_backfill.py**

Same process for each. Each becomes a native-tier template entry.

- [ ] **Step 3: Add feature flags**

In the relevant services/routes that start these workflows, add feature flag check:

```python
import os
USE_DYNAMIC = os.getenv(f"USE_DYNAMIC_EXECUTOR_follow_up", "false") == "true"
if USE_DYNAMIC:
    # Start DynamicWorkflowExecutor with the JSON definition
else:
    # Start the static FollowUpWorkflow
```

- [ ] **Step 4: Commit**

```bash
git commit -m "feat: migrate Tier 1 linear workflows to dynamic JSON definitions"
```

---

### Task 22: Migrate Tier 2 — Branching Workflows (4)

Same pattern as Task 21 for: `deal_pipeline.py`, `prospecting_pipeline.py`, `remedia_order.py`, `auto_action.py`. These require `condition` and `for_each` step types in their definitions.

- [ ] **Step 1: Convert each workflow, mapping conditions to condition nodes and loops to for_each**
- [ ] **Step 2: Add feature flags**
- [ ] **Step 3: Commit**

```bash
git commit -m "feat: migrate Tier 2 branching workflows to dynamic JSON definitions"
```

---

### Task 23: Migrate Tier 3 — continue_as_new Workflows (7)

Requires `continue_as_new` step type (implemented in Task 17). Convert: `competitor_monitor.py`, `aremko_monitor.py`, `inbox_monitor.py`, `channel_health.py`, `goal_review.py`, `memory_consolidation.py`, `autonomous_learning.py`.

- [ ] **Step 1: Convert each, adding continue_as_new as the final step with appropriate interval_seconds**
- [ ] **Step 2: Add feature flags**
- [ ] **Step 3: Commit**

```bash
git commit -m "feat: migrate Tier 3 long-running workflows to dynamic JSON with continue_as_new"
```

---

### Task 24: Migrate Tier 4 — Infrastructure Workflows (4)

Requires `cli_execute` and `internal_api` step types (implemented in Task 18). Convert: `task_execution.py`, `knowledge_extraction.py`, `code_task.py`, `rl_policy_update.py`.

- [ ] **Step 1: Convert each, mapping internal service calls to internal_api steps and CLI calls to cli_execute**
- [ ] **Step 2: Add feature flags**
- [ ] **Step 3: Commit**

```bash
git commit -m "feat: migrate Tier 4 infrastructure workflows to dynamic JSON definitions"
```

---

## Phase 8: CSS & Polish

### Task 25: Ocean Theme Styling for Builder

**Files:**
- Create: `apps/web/src/components/workflows/WorkflowBuilder.css`

- [ ] **Step 1: Write CSS**

Glassmorphic nodes, dark canvas, grid background, node hover animations (6px Y-axis), condition edge colors (green/red), status glow effects for execution view, sidebar/inspector panel styling matching Ocean theme.

- [ ] **Step 2: Import CSS in WorkflowBuilder.js and RunTreeView.js**
- [ ] **Step 3: Commit**

```bash
git add apps/web/src/components/workflows/WorkflowBuilder.css
git commit -m "feat: add Ocean theme CSS for workflow builder and execution view"
```

---

## Deferred Items (Next Phase)

These items are in the design spec but intentionally deferred from this plan to avoid scope creep. Ship the core builder first, add these in a follow-up:

- **Undo/redo** (spec Section 3.2) — Ctrl+Z / Ctrl+Shift+Z with state history stack. Requires a state management layer (zustand or useReducer with history).
- **Right-click context menu** (spec Section 3.2) — delete, duplicate, wrap in for_each/condition. Custom context menu component.
- **Step timeline alternative view** (spec Section 7.6) — vertical timeline below the tree, reuses TaskTimeline component. For linear workflows where tree view is overkill.
- **Run export as JSON/CSV** (spec Section 7.4) — download full audit trail for compliance review.
- **Bulk re-run for failed runs** (spec Section 7.5) — select multiple failed runs, trigger re-execution.

---

## Summary

| Phase | Tasks | What Ships |
|-------|-------|------------|
| 1: Backend Prerequisites | 1-4 | MCP auth fix, new MCP tools, dry_run, integration endpoints |
| 2: Visual Builder Core | 5-14 | ReactFlow canvas, nodes, palette, inspector, adapter, test console |
| 3: Page Restructure | 15 | My Workflows / Templates / Runs tabs |
| 4: Execution UI | 16 | Live tree visualization with step detail |
| 5: New Step Types | 17-18 | continue_as_new, cli_execute, internal_api |
| 6: RL Wiring | 19-20 | Run + step + creation RL, memory integration |
| 7: Migration | 21-24 | All 20 static workflows converted with feature flags |
| 8: Polish | 25 | Ocean theme styling |
