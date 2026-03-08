# Prospecting Team & Skills System Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Replace fragmented prospecting agents with a unified Prospecting Team, introduce a reusable Skills system, and rename skill_config/skill_credential to integrations.

**Architecture:** Additive-first approach — create new tables, models, agents, and routes before doing the rename. This ensures no downtime: new functionality deploys alongside existing code, then the rename is a final atomic swap.

**Tech Stack:** Python 3.11 (FastAPI, SQLAlchemy, Temporal), React 18 (Bootstrap 5), Google ADK (agent framework), PostgreSQL

**Design Doc:** `docs/plans/2026-03-07-prospecting-team-skills-system-design.md`

---

## Phase 1: Database Migration

### Task 1: Create migration 040 — new tables + renames

**Files:**
- Create: `apps/api/migrations/040_skills_and_integration_rename.sql`

**Step 1:** Create the migration SQL file with:

```sql
-- 040_skills_and_integration_rename.sql
-- Rename skill_configs/skill_credentials to integration_configs/integration_credentials
-- Create new skills and skill_executions tables
-- Add skill_id to execution_traces

-- 1. Rename existing tables
ALTER TABLE IF EXISTS skill_configs RENAME TO integration_configs;
ALTER TABLE IF EXISTS skill_credentials RENAME TO integration_credentials;

-- 2. Rename FK constraint references (update column names in integration_credentials)
-- The FK column skill_config_id stays as-is for now (rename is cosmetic overhead)
-- PostgreSQL auto-updates FK constraints when table is renamed

-- 3. Create skills table
CREATE TABLE IF NOT EXISTS skills (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    name VARCHAR NOT NULL,
    description VARCHAR,
    skill_type VARCHAR NOT NULL,
    config JSON,
    is_system BOOLEAN DEFAULT false,
    enabled BOOLEAN DEFAULT true,
    created_at TIMESTAMP DEFAULT now(),
    updated_at TIMESTAMP DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_skills_tenant ON skills(tenant_id);
CREATE INDEX IF NOT EXISTS idx_skills_type ON skills(skill_type);

-- 4. Create skill_executions table
CREATE TABLE IF NOT EXISTS skill_executions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    skill_id UUID NOT NULL REFERENCES skills(id) ON DELETE CASCADE,
    entity_id UUID REFERENCES knowledge_entities(id) ON DELETE SET NULL,
    agent_id UUID REFERENCES agents(id) ON DELETE SET NULL,
    workflow_run_id UUID REFERENCES pipeline_runs(id) ON DELETE SET NULL,
    input JSON,
    output JSON,
    status VARCHAR NOT NULL,
    duration_ms INTEGER,
    created_at TIMESTAMP DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_skill_exec_tenant ON skill_executions(tenant_id);
CREATE INDEX IF NOT EXISTS idx_skill_exec_skill ON skill_executions(skill_id);
CREATE INDEX IF NOT EXISTS idx_skill_exec_entity ON skill_executions(entity_id);

-- 5. Add skill_id to execution_traces
ALTER TABLE execution_traces ADD COLUMN IF NOT EXISTS skill_id UUID REFERENCES skills(id) ON DELETE SET NULL;
```

**Step 2:** Verify syntax by reading the file back.

**Step 3:** Commit: `feat: add migration 040 for skills tables and integration rename`

---

### Task 2: Seed system rubrics in init_db

**Files:**
- Modify: `apps/api/app/db/init_db.py`
- Reference: `apps/api/app/services/scoring_rubrics.py` (rubric definitions)

**Step 1:** Add a `seed_system_skills(db)` function after `seed_demo_data` in `init_db()`. It should:
- Check if system skills already exist (idempotent)
- Create 3 Skill rows with `is_system=true` for the rubrics: `ai_lead`, `hca_deal`, `marketing_signal`
- Copy rubric config (categories, weights, prompts) from `scoring_rubrics.py` into the `config` JSON column
- Use `skill_type="scoring"` for all three

**Step 2:** Call `seed_system_skills(db)` from `init_db()` after `seed_demo_data(db)`.

**Step 3:** Verify by running `python -c "import ast; ast.parse(open('apps/api/app/db/init_db.py').read()); print('OK')"`.

**Step 4:** Commit: `feat: seed system scoring rubrics as skills on startup`

---

## Phase 2: API Backend — Integration Rename

### Task 3: Rename SkillConfig model to IntegrationConfig

**Files:**
- Modify: `apps/api/app/models/skill_config.py`
- Create: `apps/api/app/models/integration_config.py` (copy + rename)

**Step 1:** Copy `skill_config.py` to `integration_config.py`. In the new file:
- Rename class `SkillConfig` → `IntegrationConfig`
- Change `__tablename__` from `"skill_configs"` to `"integration_configs"`
- Keep all columns and relationships identical

**Step 2:** Update `apps/api/app/models/__init__.py` — add `IntegrationConfig` import alongside existing `SkillConfig` (keep both temporarily for backward compat).

**Step 3:** Verify syntax: `python -c "ast.parse(...)"`

**Step 4:** Commit: `refactor: add IntegrationConfig model (skill_config rename)`

---

### Task 4: Rename SkillCredential model to IntegrationCredential

**Files:**
- Modify: `apps/api/app/models/skill_credential.py`
- Create: `apps/api/app/models/integration_credential.py` (copy + rename)

**Step 1:** Copy `skill_credential.py` to `integration_credential.py`. In the new file:
- Rename class `SkillCredential` → `IntegrationCredential`
- Change `__tablename__` from `"skill_credentials"` to `"integration_credentials"`
- Update FK reference: `ForeignKey("integration_configs.id")` (was `skill_configs.id`)
- Update relationship: `integration_config = relationship("IntegrationConfig", ...)`

**Step 2:** Update `apps/api/app/models/__init__.py` — add `IntegrationCredential` import.

**Step 3:** Verify syntax.

**Step 4:** Commit: `refactor: add IntegrationCredential model (skill_credential rename)`

---

### Task 5: Create integration schemas

**Files:**
- Create: `apps/api/app/schemas/integration_config.py`
- Create: `apps/api/app/schemas/integration_credential.py`

**Step 1:** Copy `apps/api/app/schemas/skill_config.py` to `integration_config.py`. Rename all classes: `SkillConfigBase` → `IntegrationConfigBase`, `SkillConfigCreate` → `IntegrationConfigCreate`, etc. Keep all fields identical.

**Step 2:** Copy credential schema similarly if separate file exists, or create `integration_credential.py` based on the `CredentialOut` schema in skill_config.py.

**Step 3:** Verify syntax.

**Step 4:** Commit: `refactor: add integration config/credential schemas`

---

### Task 6: Create integrations service and routes

**Files:**
- Create: `apps/api/app/services/integrations.py` (copy from `skill_configs.py`)
- Create: `apps/api/app/api/v1/integrations.py` (copy from `skill_configs.py`)
- Modify: `apps/api/app/api/v1/routes.py`

**Step 1:** Copy `apps/api/app/services/skill_configs.py` to `integrations.py`. Update all model references from `SkillConfig` → `IntegrationConfig` and `SkillCredential` → `IntegrationCredential`.

**Step 2:** Copy `apps/api/app/api/v1/skill_configs.py` to `integrations.py`. Update:
- Import from new models and schemas
- Import from new service
- Keep all endpoint logic identical

**Step 3:** In `apps/api/app/api/v1/routes.py`:
- Add: `from app.api.v1 import integrations`
- Add: `api_router.include_router(integrations.router, prefix="/integrations", tags=["integrations"])`
- Keep the old `/skill-configs` route for backward compatibility

**Step 4:** Verify syntax on all new files.

**Step 5:** Commit: `feat: add /api/v1/integrations routes (skill-configs rename)`

---

## Phase 3: API Backend — New Skills System

### Task 7: Create Skill model and schema

**Files:**
- Create: `apps/api/app/models/skill.py`
- Create: `apps/api/app/schemas/skill.py`
- Modify: `apps/api/app/models/__init__.py`

**Step 1:** Create `skill.py` model with columns matching migration 040:
- `id`, `tenant_id`, `name`, `description`, `skill_type`, `config` (JSON), `is_system`, `enabled`, `created_at`, `updated_at`
- Relationships: `tenant`

**Step 2:** Create `skill.py` schema with:
- `SkillBase(name, description, skill_type, config, enabled)`
- `SkillCreate(SkillBase)` — no tenant_id (injected from JWT)
- `SkillUpdate(BaseModel)` — all fields optional
- `SkillInDB(SkillBase)` — id, tenant_id, is_system, created_at, updated_at, `Config: from_attributes = True`

**Step 3:** Add import to `__init__.py`.

**Step 4:** Verify syntax.

**Step 5:** Commit: `feat: add Skill model and schema`

---

### Task 8: Create SkillExecution model and schema

**Files:**
- Create: `apps/api/app/models/skill_execution.py`
- Create: `apps/api/app/schemas/skill_execution.py`
- Modify: `apps/api/app/models/__init__.py`
- Modify: `apps/api/app/models/execution_trace.py` — add `skill_id` column

**Step 1:** Create `skill_execution.py` model with columns matching migration 040:
- `id`, `tenant_id`, `skill_id`, `entity_id`, `agent_id`, `workflow_run_id`, `input`, `output`, `status`, `duration_ms`, `created_at`
- Relationships: `tenant`, `skill`, `entity`, `agent`

**Step 2:** Create `skill_execution.py` schema with:
- `SkillExecutionCreate(skill_id, entity_id, agent_id, workflow_run_id, input, status)`
- `SkillExecution(...)` — full response model with `Config: from_attributes = True`

**Step 3:** In `execution_trace.py`, add:
- `skill_id = Column(UUID(as_uuid=True), ForeignKey("skills.id"), nullable=True)`
- Add `skill` relationship

**Step 4:** Update `execution_trace.py` schema to include optional `skill_id`.

**Step 5:** Add imports to `__init__.py`.

**Step 6:** Verify syntax.

**Step 7:** Commit: `feat: add SkillExecution model and skill_id on execution traces`

---

### Task 9: Create skills service

**Files:**
- Create: `apps/api/app/services/skills.py`

**Step 1:** Create service with functions following the `base.py` CRUD pattern:
- `get_skills(db, tenant_id, skill_type=None, skip=0, limit=100)` — list with optional type filter
- `get_skill(db, skill_id, tenant_id)` — get by ID
- `create_skill(db, skill_in, tenant_id)` — create custom skill
- `update_skill(db, skill_id, tenant_id, skill_in)` — update config
- `delete_skill(db, skill_id, tenant_id)` — delete only non-system skills
- `clone_skill(db, skill_id, tenant_id)` — clone system rubric for customization
- `execute_skill(db, skill_id, tenant_id, entity_id, params, agent_id=None)` — invoke skill on entity:
  1. Load skill config from DB
  2. If skill_type == "scoring": use existing LeadScoringTool with rubric from config
  3. Write results to entity (score, scored_at, scoring_rubric_id)
  4. Create SkillExecution record
  5. Log memory_activity with event_type="skill_executed"
  6. Return output
- `get_skill_executions(db, skill_id, tenant_id, skip=0, limit=50)` — audit trail

**Step 2:** Verify syntax.

**Step 3:** Commit: `feat: add skills service with CRUD and execution`

---

### Task 10: Create skills API routes

**Files:**
- Create: `apps/api/app/api/v1/skills_new.py` (temporary name to avoid conflict with existing `skills.py`)
- Modify: `apps/api/app/api/v1/routes.py`

**Step 1:** Create routes:
- `GET /` — list skills (optional `skill_type` query param)
- `POST /` — create skill
- `GET /{skill_id}` — get skill
- `PUT /{skill_id}` — update skill
- `DELETE /{skill_id}` — delete (non-system only)
- `POST /{skill_id}/execute` — execute skill on entity (body: `entity_id`, `params`)
- `GET /{skill_id}/executions` — skill audit trail
- `POST /{skill_id}/clone` — clone system rubric

**Step 2:** In routes.py, replace old skills router mount:
- Remove: `api_router.include_router(skills.router, prefix="/skills", ...)`
- Add: `api_router.include_router(skills_new.router, prefix="/skills", tags=["skills"])`
- Keep old `skills.py` execute/health endpoints under a different prefix or merge them

**Step 3:** Verify syntax.

**Step 4:** Commit: `feat: add /api/v1/skills routes for skill management and execution`

---

## Phase 4: ADK — Prospecting Team

### Task 11: Create prospect_researcher agent

**Files:**
- Create: `apps/adk-server/servicetsunami_supervisor/prospect_researcher.py`

**Step 1:** Create the agent following the pattern in `web_researcher.py`:
- Import web scraping tools from `web_researcher.py` (scrape_webpage, scrape_structured_data, search_and_scrape, login_google, login_linkedin)
- Import KG tools from `tools.knowledge_tools` (create_entity, update_entity, find_entities, create_relation, record_observation)
- Agent name: `prospect_researcher`
- Model: `settings.adk_model`
- Instruction: Web scraping + entity enrichment specialist. After enriching entities, notes they should be routed to prospect_scorer for scoring.
- Tools: all 10 tools listed above

**Step 2:** Verify syntax: `python -c "ast.parse(...)"`

**Step 3:** Commit: `feat: add prospect_researcher agent`

---

### Task 12: Create prospect_scorer agent

**Files:**
- Create: `apps/adk-server/servicetsunami_supervisor/prospect_scorer.py`

**Step 1:** Create the agent:
- Import KG tools: `find_entities`, `get_entity`, `update_entity`, `get_neighborhood` from `tools.knowledge_tools`
- Import sales tools: `qualify_lead` from `tools.sales_tools`
- Import `score_entity` from `.knowledge_manager` (the async function that calls the API)
- Agent name: `prospect_scorer`
- Instruction: Scoring + BANT qualification specialist. Selects rubric based on context. Can score entities and qualify leads.
- Tools: score_entity, qualify_lead, find_entities, get_entity, update_entity, get_neighborhood

**Note:** The `execute_skill` tool for runtime rubric execution will be added in a later phase once the skills API is deployed. For now, use the existing `score_entity` function.

**Step 2:** Verify syntax.

**Step 3:** Commit: `feat: add prospect_scorer agent`

---

### Task 13: Create prospect_outreach agent

**Files:**
- Create: `apps/adk-server/servicetsunami_supervisor/prospect_outreach.py`

**Step 1:** Create the agent:
- Import sales tools: `draft_outreach`, `update_pipeline_stage`, `get_pipeline_summary`, `generate_proposal`, `schedule_followup` from `tools.sales_tools`
- Import google tools: `send_email`, `create_calendar_event` from `tools.google_tools`
- Agent name: `prospect_outreach`
- Instruction: Outreach + pipeline management specialist. Drafts messages, manages pipeline stages, creates proposals and follow-ups.
- Tools: all 7 listed above

**Step 2:** Verify syntax.

**Step 3:** Commit: `feat: add prospect_outreach agent`

---

### Task 14: Create prospecting_team supervisor

**Files:**
- Create: `apps/adk-server/servicetsunami_supervisor/prospecting_team.py`

**Step 1:** Create supervisor following the pattern in `marketing_team.py`:
- Import sub-agents: `prospect_researcher`, `prospect_scorer`, `prospect_outreach` (relative imports)
- Agent name: `prospecting_team`
- Model: `settings.adk_model`
- Instruction: Route by pipeline stage:
  - Research requests (scraping, enrichment, entity discovery) → prospect_researcher
  - Scoring, qualification, rubric selection → prospect_scorer
  - Outreach, proposals, follow-ups, pipeline management → prospect_outreach
- sub_agents: [prospect_researcher, prospect_scorer, prospect_outreach]
- No tools (routing agent only)

**Step 2:** Verify syntax.

**Step 3:** Commit: `feat: add prospecting_team supervisor`

---

### Task 15: Update sales_team, root agent, and __init__.py

**Files:**
- Modify: `apps/adk-server/servicetsunami_supervisor/sales_team.py`
- Modify: `apps/adk-server/servicetsunami_supervisor/agent.py`
- Modify: `apps/adk-server/servicetsunami_supervisor/__init__.py`

**Step 1:** In `sales_team.py`:
- Remove `sales_agent` from imports
- Remove `sales_agent` from sub_agents list (keep only `customer_support`)
- Simplify routing instructions (everything routes to customer_support)

**Step 2:** In `agent.py` (root supervisor):
- Replace `marketing_team` with `prospecting_team` in imports
- Replace `marketing_team` with `prospecting_team` in sub_agents list (line 108)
- Update routing instructions: change "marketing_team" references to "prospecting_team"
- Add routing for: web research, scraping, lead gen, prospecting, scoring → prospecting_team

**Step 3:** In `__init__.py`:
- Add imports for new agents: `prospect_researcher`, `prospect_scorer`, `prospect_outreach`, `prospecting_team`
- Remove imports for: `marketing_team`, `knowledge_manager`, `sales_agent`
- Keep the old files on disk (don't delete yet — can clean up later)

**Step 4:** Verify syntax on all 3 files.

**Step 5:** Commit: `feat: wire prospecting_team into root supervisor, simplify sales_team`

---

## Phase 5: Frontend

### Task 16: Rename SkillsConfigPanel to IntegrationsPanel

**Files:**
- Rename: `apps/web/src/components/SkillsConfigPanel.js` → `apps/web/src/components/IntegrationsPanel.js`
- Modify: `apps/web/src/pages/IntegrationsPage.js`

**Step 1:** Copy `SkillsConfigPanel.js` to `IntegrationsPanel.js`. No internal changes needed — the component name in JSX doesn't need to match the filename.

**Step 2:** In `IntegrationsPage.js`, update the import:
- `import SkillsConfigPanel from '../components/SkillsConfigPanel'` → `import IntegrationsPanel from '../components/IntegrationsPanel'`
- Update JSX usage: `<SkillsConfigPanel />` → `<IntegrationsPanel />`

**Step 3:** Keep old `SkillsConfigPanel.js` for now (delete in cleanup).

**Step 4:** Verify: `cd apps/web && npx react-scripts build 2>&1 | tail -5`

**Step 5:** Commit: `refactor: rename SkillsConfigPanel to IntegrationsPanel`

---

### Task 17: Update frontend service layer for integrations

**Files:**
- Modify: `apps/web/src/services/skillConfig.js` (or create `integrations.js`)

**Step 1:** Find the skill config service file (likely `skillConfig.js` or similar in services/).

**Step 2:** Create a parallel `integrationConfig.js` service that calls `/api/v1/integrations` instead of `/api/v1/skill-configs`. Keep both services working (old routes still functional via backward compat).

**Step 3:** Update `IntegrationsPanel.js` to import from the new service.

**Step 4:** Build check.

**Step 5:** Commit: `refactor: add integration config frontend service`

---

### Task 18: Create Skills management UI

**Files:**
- Create: `apps/web/src/components/SkillsManagementPanel.js`
- Modify: `apps/web/src/pages/IntegrationsPage.js`

**Step 1:** Create `SkillsManagementPanel.js` — a new component for managing skills/rubrics:
- List skills fetched from `GET /api/v1/skills` (grouped by skill_type)
- Each skill card shows: name, description, type, is_system badge, enabled toggle
- System skills show a "Clone" button
- Custom skills show "Edit" and "Delete" buttons
- "Create Skill" button opens a form (name, description, skill_type, config JSON editor)
- Follow the card-grid pattern from existing components (glassmorphic cards)

**Step 2:** In `IntegrationsPage.js`:
- Add `'skills'` to `TAB_KEYS` array (currently: `['integrations', 'connectors', 'data-sources', 'datasets', 'ai-models']`)
- Add tab content: `{activeTab === 'skills' && <SkillsManagementPanel />}`

**Step 3:** Build check.

**Step 4:** Commit: `feat: add Skills management tab to Integrations page`

---

### Task 19: Create skills frontend service

**Files:**
- Create: `apps/web/src/services/skills.js`

**Step 1:** Create service with all API calls:
- `getSkills(skillType)` → `GET /api/v1/skills`
- `getSkill(id)` → `GET /api/v1/skills/{id}`
- `createSkill(data)` → `POST /api/v1/skills`
- `updateSkill(id, data)` → `PUT /api/v1/skills/{id}`
- `deleteSkill(id)` → `DELETE /api/v1/skills/{id}`
- `executeSkill(id, entityId, params)` → `POST /api/v1/skills/{id}/execute`
- `getSkillExecutions(id)` → `GET /api/v1/skills/{id}/executions`
- `cloneSkill(id)` → `POST /api/v1/skills/{id}/clone`

**Step 2:** Commit: `feat: add skills frontend service`

---

### Task 20: Update WorkflowsPage definitions

**Files:**
- Modify: `apps/web/src/pages/WorkflowsPage.js`

**Step 1:** Add `ProspectingPipelineWorkflow` to `WORKFLOW_DEFINITIONS`:
```javascript
{
  id: 'prospecting-pipeline',
  name: 'Prospecting Pipeline',
  description: 'Automated prospect research, scoring, qualification, and outreach',
  queue: 'servicetsunami-orchestration',
  icon: FaBullseye,
  color: '#34d399',
  steps: [
    { name: 'Research', description: 'Web scrape and enrich entity properties' },
    { name: 'Score', description: 'Invoke scoring skill using tenant rubric' },
    { name: 'Qualify', description: 'BANT qualification on entities above threshold' },
    { name: 'Outreach', description: 'Draft outreach for qualified leads' },
    { name: 'Notify', description: 'Create notifications with results summary' },
  ],
}
```

**Step 2:** Update `DealPipelineWorkflow` step count: add a 7th step "Score" between Discover and Research:
```javascript
{ name: 'Score', description: 'Score prospects using deal intelligence rubric' },
```

**Step 3:** Build check.

**Step 4:** Commit: `feat: add ProspectingPipelineWorkflow to WorkflowsPage`

---

### Task 21: Update memory activity constants for skill events

**Files:**
- Modify: `apps/web/src/components/memory/constants.js`

**Step 1:** Add new event types to `ACTIVITY_EVENT_CONFIG`:
```javascript
skill_executed:   { icon: FaWrench,  color: '#a78bfa', label: 'Skill Executed' },
entity_scored:    { icon: FaStar,    color: '#fbbf24', label: 'Entity Scored' },
rubric_created:   { icon: FaPlus,    color: '#34d399', label: 'Rubric Created' },
rubric_updated:   { icon: FaEdit,    color: '#60a5fa', label: 'Rubric Updated' },
```

**Step 2:** Build check.

**Step 3:** Commit: `feat: add skill event types to memory activity constants`

---

## Phase 6: Workflows & Worker

### Task 22: Create ProspectingPipelineWorkflow

**Files:**
- Create: `apps/api/app/workflows/prospecting_pipeline.py`

**Step 1:** Create the workflow following the pattern in `deal_pipeline.py`:
- Workflow class: `ProspectingPipelineWorkflow`
- Queue: `servicetsunami-orchestration`
- 5 activities as steps:
  1. `prospect_research(tenant_id, entity_ids, params)` — enrichment step
  2. `prospect_score(tenant_id, entity_ids, rubric_id)` — scoring via execute_skill
  3. `prospect_qualify(tenant_id, entity_ids, threshold)` — BANT qualification
  4. `prospect_outreach(tenant_id, entity_ids, template)` — draft outreach
  5. `prospect_notify(tenant_id, results)` — create notification summary
- Each activity: 5-minute timeout, 3 retries with 10s backoff

**Step 2:** Create activity implementations (stub — call LLM service or skill service):
- Each activity loads the entities, performs its step, updates entities, logs memory_activity
- Use existing services: `knowledge.py`, `skills.py`, `llm.py`

**Step 3:** Verify syntax.

**Step 4:** Commit: `feat: add ProspectingPipelineWorkflow with 5-step pipeline`

---

### Task 23: Create execute_skill Temporal activity

**Files:**
- Create: `apps/api/app/workflows/activities/skill_activities.py`

**Step 1:** Create the reusable `execute_skill` activity:
```python
@activity.defn
async def execute_skill(tenant_id: str, skill_name: str, entity_id: str, params: dict) -> dict:
    # 1. Get DB session
    # 2. Load skill by name + tenant_id
    # 3. Call skills service execute_skill()
    # 4. Return output dict
```

**Step 2:** This activity is importable from ANY workflow — prospecting, deal pipeline, knowledge extraction.

**Step 3:** Verify syntax.

**Step 4:** Commit: `feat: add execute_skill reusable Temporal activity`

---

### Task 24: Register ProspectingPipelineWorkflow in orchestration worker

**Files:**
- Modify: `apps/api/app/workers/orchestration_worker.py`

**Step 1:** Import `ProspectingPipelineWorkflow` and its activities.

**Step 2:** Add `ProspectingPipelineWorkflow` to the `workflows=[]` list.

**Step 3:** Add prospecting activities to the `activities=[]` list.

**Step 4:** Also add `execute_skill` activity to the activities list.

**Step 5:** Verify syntax.

**Step 6:** Commit: `feat: register ProspectingPipelineWorkflow in orchestration worker`

---

## Verification & Deploy

### Task 25: Build checks and final verification

**Step 1:** API syntax check:
```bash
cd apps/api && python -c "
import ast
import glob
for f in glob.glob('app/**/*.py', recursive=True):
    ast.parse(open(f).read())
print('All Python files OK')
"
```

**Step 2:** Frontend build:
```bash
cd apps/web && npx react-scripts build 2>&1 | tail -5
```

**Step 3:** ADK syntax check:
```bash
cd apps/adk-server && python -c "
import ast
import glob
for f in glob.glob('**/*.py', recursive=True):
    ast.parse(open(f).read())
print('All ADK files OK')
"
```

**Step 4:** If all pass, commit any remaining changes and push.

**Step 5:** Deploy:
- Push to main triggers API, Web, Worker CI
- Manually trigger ADK deploy: `gh workflow run adk-deploy.yaml -f deploy=true -f environment=prod`
- Verify pods: `kubectl get pods -n prod -w`

---

## Task Dependency Summary

```
Phase 1 (DB):     [1] → [2]
Phase 2 (Rename): [3] → [4] → [5] → [6]  (depends on Phase 1)
Phase 3 (Skills): [7] → [8] → [9] → [10] (depends on Phase 1)
Phase 4 (ADK):    [11] [12] [13] → [14] → [15]  (independent of Phases 2-3)
Phase 5 (Web):    [16] → [17] [18] [19] → [20] [21]  (depends on Phases 2-3 for routes)
Phase 6 (Worker): [22] → [23] → [24]  (depends on Phase 3)
Final:            [25] (depends on all)
```

Phases 2, 3, and 4 can run in parallel after Phase 1.
Phase 5 depends on Phases 2-3 (needs new routes).
Phase 6 depends on Phase 3 (needs skills service).
