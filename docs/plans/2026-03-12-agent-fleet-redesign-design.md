# Agent Fleet Page Redesign — Design Document

**Date:** 2026-03-12
**Goal:** Replace the flat agent table and Quick Create modal with a dashboard-like card grid and a dedicated agent detail page with tabbed profile view.

**Architecture:** Card grid listing at `/agents` with dashboard-style cards showing skills, stats, and relations. Clicking a card navigates to `/agents/:id` — a full detail page with tabs: Overview, Relations, Tasks, Config. Quick Create removed; Agent Wizard is the sole creation path.

**Tech Stack:** React 18, React Bootstrap, React Router v7 (useParams), existing API endpoints.

---

## 1. Agent Fleet Page — Card Grid (`/agents`)

### Remove
- Quick Create button and its modal (form state, handlers, Modal component)
- `showModal`, `editingAgent`, `formData`, `handleSubmit` state/handlers

### Layout
Responsive card grid: `repeat(auto-fill, minmax(380px, 1fr))`. Replaces the flat table.

### Card Content
Each card displays:
- **Header row**: Agent name (bold, clickable) + status dot (green/grey/red) + model badge (e.g. `gpt-4`)
- **Description**: 2-line CSS clamp
- **Badges row**: Role badge (analyst/manager/specialist), autonomy level pill (full/supervised/approval_required)
- **Skills row**: Up to 4 skill pills from `config.skills` or `config.tools`, "+N more" if truncated
- **Stats row**: Active tasks count, completed tasks count, success rate bar
- **Relations hint**: "Supervises 2 agents" or "Reports to X" if relationships exist
- **Click action**: Navigates to `/agents/:id`

### Data Fetching
- `GET /api/v1/agents` — agent list
- `GET /api/v1/tasks` — aggregate task counts per agent client-side
- `GET /api/v1/agent_groups` — derive relationship hints

---

## 2. Agent Detail Page (`/agents/:id`)

### New Route
Add `/agents/:id` → `AgentDetailPage.js` in `App.js`.

### Header (always visible)
- Back arrow → `/agents`
- Agent name (large), description
- Status badge, model badge, role badge, autonomy level
- Action buttons: Edit (navigates to wizard pre-filled), Delete (confirmation modal)

### Tabs

#### Overview (default)
- **Skills section**: Card per skill. Data merged from `agent.skills` relationship (AgentSkill: skill_name, proficiency, times_used, success_rate, learned_from) and `config.skills` array (marketplace skill slugs). Each skill shows: name, proficiency bar (0-100%), usage count, success rate, learned_from badge.
- **Stats section**: Metric tiles — total tasks, completed tasks, success rate, tokens used. Data from `GET /api/v1/tasks?assigned_agent_id={id}`.

#### Relations
- List of agent relationships: direction arrow, relationship type badge (supervises/delegates_to/collaborates_with/reports_to), other agent name (clickable link to their detail page), trust level bar.
- Agent kit membership if applicable.
- Data from `GET /api/v1/agent_groups` — derive relationships from group context.

#### Tasks
- Table: objective, status badge, priority badge, created_at, completed_at, confidence score.
- Data from `GET /api/v1/tasks?assigned_agent_id={id}`.

#### Config
- System prompt in read-only code block (full text).
- Model, temperature, max_tokens, personality preset as labeled values.
- Raw config JSON viewer (collapsible).

---

## 3. Data Flow & Backend

### Existing Endpoints (no changes needed for MVP)
- `GET /api/v1/agents` — list with config JSON
- `GET /api/v1/agents/:id` — single agent (includes skills relationship via eager load)
- `GET /api/v1/tasks` — filter client-side by assigned_agent_id
- `GET /api/v1/agent_groups` — team organization

### Backend Addition
- Ensure `AgentInDB` schema serializes the `skills` relationship (list of AgentSkill objects).

### Frontend Service Additions (`agent.js`)
- `getTasks(agentId)` — fetch tasks for a specific agent
- `getGroups()` — fetch agent groups

---

## 4. Files Changed

- **New**: `apps/web/src/pages/AgentDetailPage.js` — tabbed detail page
- **New**: `apps/web/src/pages/AgentDetailPage.css` — detail page styles
- **Rewrite**: `apps/web/src/pages/AgentsPage.js` — card grid, remove Quick Create
- **Modify**: `apps/web/src/App.js` — add `/agents/:id` route
- **Modify**: `apps/web/src/services/agent.js` — add task/group helpers
- **Possibly modify**: `apps/api/app/schemas/agent.py` — add skills to response

### Styling
Glassmorphic ocean theme consistent with existing pages:
- Metric tiles from DashboardPage pattern
- Tab pattern from WorkflowsPage
- Card grid from IntegrationsPage
