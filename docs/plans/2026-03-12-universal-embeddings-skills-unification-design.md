# Universal Embeddings & Skills Unification — Design Document

**Date:** 2026-03-12
**Goal:** Wire embedding service into every content creation path for full semantic memory, and unify all agent tools/rubrics into the Skills Marketplace as native skills.

**Architecture:** Two parallel workstreams. (1) Add `embed_and_store()` hooks to chat messages, media attachments, knowledge relations, and agent tasks. (2) Migrate 7 agent tools + 3 scoring rubrics into 10 native marketplace skills, add `tool` engine type, replace wizard tool toggles with a dynamic skill picker.

**Tech Stack:** pgvector + Gemini Embedding 2 (already deployed), React skill picker component, new `tool` engine in skill_manager.

---

## 1. Universal Embedding Wiring

Every content creation path gets an `embed_and_store()` call after the record is persisted. All calls wrapped in try/except — embedding failures never break the main flow.

### Integration Points

| Content | File | Function | Text to Embed | Content Type |
|---|---|---|---|---|
| Chat messages | `services/chat.py` | `_append_message()` | `"[user|assistant]: {content[:2000]}"` | `chat_message` |
| Media attachments | `api/v1/chat.py` | `post_message_with_file()` | `"{filename}: {extracted_text[:2000]}"` | `attachment` |
| Knowledge relations | `services/knowledge.py` | `create_relation()` | `"{type}: {from.name} → {to.name} ({evidence})"` | `relation` |
| Agent tasks | `services/chat.py` | `_bridge_chat_to_workflow()` | `"Task: {objective} | {context[:1000]}"` | `agent_task` |

### Embedding Lifecycle
- Create: embed immediately after flush/commit
- Update: delete old embedding, create new
- Delete: cascade delete embedding
- All inline — no batch processing

### Impact
The `recall()` function already searches all content types. Once wired, Luna's semantic context assembly automatically includes chat history, uploaded documents, relations, and task context.

---

## 2. Skills Unification — Tools & Rubrics → Native Marketplace Skills

### New Engine Type: `tool`

A `tool` engine skill.md maps to an existing Python `Tool` class in `tool_executor.py`. The skill.md provides metadata (description, auto_trigger, category, inputs) while execution delegates to the Tool class.

Frontmatter adds `tool_class` field:
```yaml
---
name: SQL Query
engine: tool
tool_class: SQLQueryTool
category: data
auto_trigger: "Run SQL query against connected datasets"
inputs:
  - name: query
    type: string
    description: "SQL query to execute"
    required: true
---
```

### 10 New Native Skills

| Skill Slug | Engine | Category | Auto-trigger Description |
|---|---|---|---|
| sql_query | tool | data | "Run SQL query against datasets" |
| data_summary | tool | data | "Generate statistics or summary of data" |
| calculator | tool | general | "Calculate, compute, or do math" |
| entity_extraction | tool | automation | "Extract entities from text" |
| knowledge_search | tool | general | "Search the knowledge graph" |
| lead_scoring | tool | sales | "Score or qualify a lead" |
| report_generation | tool | data | "Generate a report or Excel file" |
| ai_lead_rubric | markdown | sales | "Score leads for AI platform fit" |
| hca_deal_rubric | markdown | sales | "Score companies for M&A sell-likelihood" |
| marketing_signal_rubric | markdown | marketing | "Score leads on marketing engagement" |

### Skill Manager Changes
- `skill_manager.py` — add `_execute_tool(skill, inputs)` handler that imports and calls the Tool class from `tool_executor.py`
- `execute_chain()` — support `tool` engine in chain execution
- Rubric markdown skills contain the full rubric config (categories, weights, ranges) in the skill.md body, replacing `scoring_rubrics.py` hardcoded data

---

## 3. Wizard Skill Picker

### Replace SkillsDataStep.js

Current: 7 hardcoded toggle switches for tools.
New: Mini marketplace browser with search and category filters.

**Component behavior:**
- Fetches skills from `GET /api/v1/skills/library?tier=native` on mount
- Category chip filters (same chip set as marketplace page)
- Search bar with debounce
- Compact selectable cards: checkbox + name + one-line description + category badge
- Templates pre-select skills by slug array: `skills: ["sql_query", "knowledge_search"]`
- Selected skills stored in agent `config.skills` (replaces `config.tools`)

### Template Config Migration

```javascript
// Before
tools: ["sql_query", "knowledge_search", "lead_scoring"]
scoring_rubric: "ai_lead"

// After
skills: ["sql_query", "knowledge_search", "lead_scoring", "ai_lead_rubric"]
```

### Remove SkillsManagementPanel.js
Scoring rubrics are now managed in the marketplace. The old panel becomes unnecessary.

---

## 4. Backward Compatibility

- Existing agents with `config.tools` keep working — agent config reader checks `config.skills` first, falls back to `config.tools`
- Old `Skill` DB records (scoring rubrics) remain in DB but become unused
- No data migration needed for existing agents
- `tool_executor.py` Tool classes unchanged — skill manager wraps them

---

## 5. Files Changed Summary

### Embedding Wiring
- **Modify:** `apps/api/app/services/chat.py` — embed chat messages in `_append_message()`
- **Modify:** `apps/api/app/api/v1/chat.py` — embed media attachments in `post_message_with_file()`
- **Modify:** `apps/api/app/services/knowledge.py` — embed relations in `create_relation()`

### Skills Unification
- **Create:** 10 `skill.md` files in `apps/api/app/skills/` (one per tool + rubric)
- **Modify:** `apps/api/app/services/skill_manager.py` — add `tool` engine support
- **Modify:** `apps/api/app/services/tool_executor.py` — export Tool class registry for skill manager lookup

### Wizard
- **Rewrite:** `apps/web/src/components/wizard/SkillsDataStep.js` — skill picker
- **Modify:** `apps/web/src/components/wizard/TemplateSelector.js` — `tools` → `skills` in templates
- **Modify:** `apps/web/src/components/wizard/AgentWizard.js` — pass skills config
- **Delete:** `apps/web/src/components/SkillsManagementPanel.js`

### Cleanup
- **Deprecate:** `apps/api/app/services/scoring_rubrics.py` — rubrics move to skill.md files
