# Prospecting Team & Skills System Design

**Date:** 2026-03-07
**Status:** Approved
**Goal:** Replace fragmented lead scoring/prospecting across marketing_team, sales_agent, and deal_team with a unified Prospecting Team, introduce a reusable Skills system, and rename existing skill_config/skill_credential to integrations.

---

## Architecture Decisions

1. New top-level **Prospecting Team** replaces `marketing_team` in root supervisor
2. 3 pipeline-stage sub-agents: `prospect_researcher`, `prospect_scorer`, `prospect_outreach`
3. `knowledge_manager` agent removed — Luna handles KG directly, prospecting agents get KG tools inline
4. `deal_team` stays separate for M&A-specific work
5. Scoring rubrics: tenant-configurable via UI + agent can create ad-hoc rubrics at runtime
6. `skill_config`/`skill_credential` renamed to `integration_config`/`integration_credential`
7. New **Skill** concept = reusable capability that agents and workflows can invoke
8. Skills wired to memory system (memory_activities) and workflow system (Temporal activities)

---

## Agent Structure

### Remove
- `marketing_team.py` (supervisor)
- `knowledge_manager.py` (absorbed — Luna has KG tools, scoring becomes a skill)
- `sales_agent.py` (outreach/pipeline absorbed into prospect_outreach)

### New: Prospecting Team

**`prospecting_team`** (top-level supervisor, replaces marketing_team in root)

Routes to 3 sub-agents by pipeline stage:

**`prospect_researcher`**
- Web scraping + entity enrichment
- Tools: `scrape_webpage`, `scrape_structured_data`, `search_and_scrape`, `login_google`, `login_linkedin`, `create_entity`, `update_entity`, `find_entities`, `create_relation`, `record_observation`
- After enriching entities, triggers scoring via prospect_scorer

**`prospect_scorer`**
- Scoring skill invocation + BANT qualification
- Tools: `execute_skill` (new), `qualify_lead`, `find_entities`, `get_entity`, `update_entity`, `get_neighborhood`
- Selects rubric based on context (ai_lead, hca_deal, marketing_signal, or tenant-custom)
- Can create ad-hoc rubrics from natural language requests

**`prospect_outreach`**
- Draft messages + pipeline stage management + proposals + follow-ups
- Tools: `draft_outreach`, `update_pipeline_stage`, `get_pipeline_summary`, `generate_proposal`, `schedule_followup`, `send_email`, `create_calendar_event`

### Modified
- `sales_team.py` — keeps only `customer_support` sub-agent
- Root `agent.py` — swap `marketing_team` -> `prospecting_team` in sub_agents list, update routing instructions

### Unchanged
- `deal_team`, `data_team`, `dev_team`, `vet_supervisor`, `personal_assistant` (Luna)

---

## Data Model

### Rename (no logic change)
- `skill_configs` table -> `integration_configs`
- `skill_credentials` table -> `integration_credentials`
- `SkillConfig` model -> `IntegrationConfig`
- `SkillCredential` model -> `IntegrationCredential`
- `SkillsConfigPanel` component -> `IntegrationsPanel`
- API routes `/api/v1/skills` -> `/api/v1/integrations`

### New: `skills` table
```sql
CREATE TABLE skills (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID NOT NULL REFERENCES tenants(id),
    name VARCHAR NOT NULL,
    description VARCHAR,
    skill_type VARCHAR NOT NULL,  -- 'scoring', 'qualification', 'outreach', 'analysis'
    config JSON,                  -- skill-specific config (rubric: categories + weights)
    is_system BOOLEAN DEFAULT false,
    enabled BOOLEAN DEFAULT true,
    created_at TIMESTAMP DEFAULT now(),
    updated_at TIMESTAMP DEFAULT now()
);
CREATE INDEX idx_skills_tenant ON skills(tenant_id);
CREATE INDEX idx_skills_type ON skills(skill_type);
```

Seed data: 3 system scoring rubrics (ai_lead, hca_deal, marketing_signal) with `is_system=true`.

### New: `skill_executions` table
```sql
CREATE TABLE skill_executions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID NOT NULL REFERENCES tenants(id),
    skill_id UUID NOT NULL REFERENCES skills(id),
    entity_id UUID REFERENCES knowledge_entities(id),
    agent_id UUID REFERENCES agents(id),
    workflow_run_id UUID REFERENCES pipeline_runs(id),
    input JSON,
    output JSON,
    status VARCHAR NOT NULL,  -- 'success', 'error'
    duration_ms INTEGER,
    created_at TIMESTAMP DEFAULT now()
);
CREATE INDEX idx_skill_exec_tenant ON skill_executions(tenant_id);
CREATE INDEX idx_skill_exec_skill ON skill_executions(skill_id);
CREATE INDEX idx_skill_exec_entity ON skill_executions(entity_id);
```

### Modified: `execution_traces`
- Add `skill_id UUID REFERENCES skills(id)` (nullable)

---

## Memory System Wiring

Skill executions create `memory_activities` entries:
- New activity_type values: `skill_executed`, `entity_scored`, `rubric_created`, `rubric_updated`
- `agent_id`: which agent invoked the skill
- `entity_id`: which entity was affected
- `change_delta`: JSON with skill output (score breakdown, previous vs new score, rubric used)
- `task_id`: links to agent_task if invoked during task execution

Entity scoring updates:
- `knowledge_entities.score` + `scored_at` updated on each scoring
- `knowledge_entities.properties` gets `score_breakdown`, `score_reasoning`, `rubric_id`
- `knowledge_entities.updated_by_agent_id` tracks which agent triggered scoring
- `knowledge_entities.data_quality_score` computed from property completeness

Timeline visibility:
- `get_entity_timeline` shows scoring events via observation records
- `skill_executions` provides separate skill audit log view

---

## Workflow Integration

### New: `ProspectingPipelineWorkflow`
Queue: `servicetsunami-orchestration`

Steps:
1. **Research** — web scrape, enrich entity properties
2. **Score** — invoke scoring skill on each entity using tenant's rubric
3. **Qualify** — BANT qualification on entities above threshold
4. **Outreach** — draft outreach for qualified leads, create follow-ups
5. **Notify** — create notifications with results summary

Triggers:
- Manual: Luna or prospect_scorer agent
- Scheduled: via scheduler_worker (cron/interval)
- Reactive: InboxMonitorWorkflow detects inbound interest signals

### Reusable Temporal activity
```python
@activity.defn
async def execute_skill(tenant_id, skill_name, entity_id, params) -> dict:
    # Load skill config from DB
    # Execute skill logic (LLM call for scoring, etc.)
    # Write results to entity + memory_activity + skill_execution
    # Return output
```

Callable from ANY workflow: prospecting, deal pipeline, knowledge extraction, inbox monitor.

### Modified existing workflows
- `DealPipelineWorkflow` — add scoring step via execute_skill activity (7 steps total)
- `KnowledgeExtractionWorkflow` — optional scoring after entity extraction for lead/contact categories

### Workflows Page
- Add `ProspectingPipelineWorkflow` to `WORKFLOW_DEFINITIONS` (5 steps, orchestration queue)
- Update `DealPipelineWorkflow` step count (6 -> 7)
- Total managed workflows: 14

---

## API Routes

### Renamed
- `GET/POST /api/v1/skills` -> `GET/POST /api/v1/integrations`
- `GET/PUT/DELETE /api/v1/skills/{id}` -> `GET/PUT/DELETE /api/v1/integrations/{id}`

### New: Skills
- `GET /api/v1/skills` — list skills for tenant (with optional type filter)
- `POST /api/v1/skills` — create custom skill/rubric
- `GET /api/v1/skills/{id}` — get skill details
- `PUT /api/v1/skills/{id}` — update skill config (weights, categories)
- `DELETE /api/v1/skills/{id}` — delete (only non-system)
- `POST /api/v1/skills/{id}/execute` — invoke skill on an entity
- `GET /api/v1/skills/{id}/executions` — audit trail for a skill
- `POST /api/v1/skills/{id}/clone` — clone a system rubric for customization

---

## Frontend Changes

- Rename `SkillsConfigPanel` -> `IntegrationsPanel` (all references)
- New "Skills" tab in Settings page — rubric management UI (list, create, clone, edit weights/categories)
- Rubric editor: category name, max points, description per category
- Update `WorkflowsPage.js` — add ProspectingPipelineWorkflow definition
- Update sidebar if page names change

---

## Migration & Rollout Order

1. **Migration** — DB schema (rename tables, create new tables, seed data)
2. **API** — models, schemas, services, routes
3. **ADK** — new prospecting agents, remove old agents
4. **Web** — UI renames + skill management page
5. **Worker** — register ProspectingPipelineWorkflow in orchestration worker

No new microservices. All changes deploy through existing API, worker, ADK, and web pipelines.
