# Memory / Knowledge Base Page Redesign

**Date**: 2026-03-02
**Status**: Completed

## Context

The Memory page (`/memory`) is the UI for the knowledge graph — entities, relations, and observations extracted from conversations, Gmail, Calendar, and other tools. The current implementation has critical UX problems:

1. **Massive entity duplication** — "Providencia" appears 5+ times, "Amoxicilina" 4+ times, created fresh each conversation
2. **No categorization** — Every entity shows as UNCATEGORIZED / CONCEPT / DRAFT
3. **Noise entities** — Internal platform terms (Luna, ADK service, WhatsApp, Gmail, inbox) pollute the knowledge base
4. **Flat unusable table** — Dense 9-column table, no grouping, no hierarchy

5. **No entity detail view** — Can't see properties, observations, history
6. **No CRUD from UI** — Can't edit, merge, delete, or reclassify entities
7. **No pagination** — Hard-capped at 100 results

The backend has 13 API endpoints but the frontend only uses 3.

## Part A: Fix Entity Extraction Quality (Backend)

**MODIFY**: `apps/api/app/services/knowledge_extraction.py`

1. Add `ENTITY_BLOCKLIST` set at module level — platform internals (luna, adk, whatsapp, gmail, inbox, dashboard, workflow, knowledge_manager, sales_agent, etc.)
2. Improve `_build_prompt()` — add category guidance, blocklist instructions, normalize naming rules. The LLM must return `category` field and use proper entity types.
3. Add blocklist check in `_persist_entities()` — skip entities whose `name.lower()` is in blocklist
4. Pass `category` from LLM output through to `KnowledgeEntity` constructor (currently lost)

## Part B: Extend Frontend Service Layer

**REWRITE**: `apps/web/src/services/memory.js`

Add all missing API calls:
- `createEntity(data)` — POST /entities
- `updateEntity(id, data)` — PUT /entities/{id}
- `deleteEntity(id)` — DELETE /entities/{id}
- `bulkDeleteEntities(ids)` — sequential DELETE calls
- `updateEntityStatus(id, status)` — PUT /entities/{id}/status
- `scoreEntity(id, rubricId)` — POST /entities/{id}/score
- `createRelation(data)` — POST /relations
- `deleteRelation(id)` — DELETE /relations/{id}
- `getScoringRubrics()` — GET /scoring-rubrics

## Part C: New Components

**NEW**: `apps/web/src/components/memory/` folder:

| Component | Purpose |
|-----------|---------|
| `constants.js` | CATEGORY_CONFIG (icons/colors per category), STATUS_CONFIG, ENTITY_TYPES, RELATION_TYPES |
| `EntityStatsBar.js` | Stats row: total entities + breakdown by category as colored chips |
| `EntityCard.js` | Card per entity: category icon, name, badges, confidence bar, expand/collapse, multi-select |
| `EntityDetail.js` | Expanded inline detail: editable fields, properties, relations, actions (delete/score/status) |
| `RelationsList.js` | Relations list with create/delete, direction arrows, strength bars |
| `EntityCreateModal.js` | Modal form for manual entity creation |

## Part D: Main Page Rewrite

**REWRITE**: `apps/web/src/pages/MemoryPage.js`

Replace flat table with:
- Page header with "Add Entity" button
- Stats bar (total, by-category counts)
- Search + filter row (category, type, status dropdowns + select-all checkbox)
- Responsive card grid (`repeat(auto-fill, minmax(380px, 1fr))`)
- Click-to-expand entity cards (follows SkillsConfigPanel pattern)
- Multi-select + bulk delete
- Pagination (Load More button, 50 per page)
- Keep Import tab, remove Agent Memories stub

## Part E: CSS

**NEW**: `apps/web/src/pages/MemoryPage.css`

Follow existing design system patterns:
- Glassmorphic cards (AgentsPage `.data-card`)
- Tab navigation (WorkflowsPage `.workflows-tabs`)
- Responsive grid (IntegrationsPage grid layout)
- Page header (AgentsPage `.page-header`)

## Implementation Order

1. Backend extraction fix (Part A) — immediate noise reduction
2. Service layer (Part B) — prerequisite for UI
3. Constants + CSS (Parts C partial + E)
4. Leaf components: EntityStatsBar, RelationsList
5. EntityDetail → EntityCard → EntityCreateModal
6. Main page rewrite (Part D)

## Verification

1. Create a new chat session, send messages — verify extracted entities have proper categories and no noise
2. Open Memory page — verify card grid, stats bar, filters work
3. Click entity card — verify expand shows detail with relations
4. Edit entity name/category inline — verify API update works
5. Bulk select + delete duplicates — verify cleanup works
6. Create entity manually via modal — verify it appears in grid
