# Universal Embeddings & Skills Unification — Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Wire embedding service into every content creation path and unify all agent tools + scoring rubrics into the Skills Marketplace as native skills with a dynamic wizard skill picker.

**Architecture:** Two workstreams. (1) Add `embed_and_store()` hooks to chat messages, media attachments, knowledge relations, and agent tasks — all try/except wrapped. (2) Create 10 native skill.md files (7 tool-backed + 3 rubric markdown), add `tool` engine type to skill_manager, rewrite wizard SkillsDataStep as a marketplace skill picker.

**Tech Stack:** pgvector + Gemini Embedding 2 (already deployed), Python/FastAPI, React 18, existing tool_executor.py framework.

---

## Workstream A: Universal Embedding Wiring

### Task 1: Embed Chat Messages

**Files:**
- Modify: `apps/api/app/services/chat.py:147-166`

**Step 1: Add embedding import and helper**

Add to the top imports section of `apps/api/app/services/chat.py`:

```python
from app.services.embedding_service import embed_and_store as _embed
```

**Step 2: Add embedding call in `_append_message()`**

After line 165 (`db.refresh(message)`), before `return message`, add:

```python
    try:
        _embed(
            db,
            tenant_id=session.tenant_id,
            content_type="chat_message",
            content_id=str(message.id),
            text_content=f"[{role}]: {content[:2000]}",
        )
    except Exception:
        logger.debug("Chat message embedding skipped", exc_info=True)
```

Note: `session` is already available as a parameter. `logger` is already imported in this file.

**Step 3: Verify**

Run: `cd apps/api && python -c "from app.services.chat import _append_message; print('OK')"`
Expected: OK (no import errors)

**Step 4: Commit**

```bash
git add apps/api/app/services/chat.py
git commit -m "feat: embed chat messages on creation for semantic recall"
```

---

### Task 2: Embed Media Attachments

**Files:**
- Modify: `apps/api/app/api/v1/chat.py:144-193`

**Step 1: Add embedding import**

Add to the imports section of `apps/api/app/api/v1/chat.py`:

```python
from app.services.embedding_service import embed_and_store as _embed
```

**Step 2: Add embedding call after user message is created**

In `post_message_with_file()`, after line 189 (after `chat_service.post_user_message` returns), before the return statement, add:

```python
    # Embed attachment text for semantic search
    try:
        embed_text = f"{file.filename or 'attachment'}: {attachment_meta.get('extracted_text', content)[:2000]}"
        _embed(
            db,
            tenant_id=current_user.tenant_id,
            content_type="attachment",
            content_id=str(user_msg.id),
            text_content=embed_text,
        )
    except Exception:
        pass  # Never break uploads for embedding failures
```

**Step 3: Verify**

Run: `cd apps/api && python -c "from app.api.v1.chat import router; print('OK')"`
Expected: OK

**Step 4: Commit**

```bash
git add apps/api/app/api/v1/chat.py
git commit -m "feat: embed media attachments (PDFs, voice, images) for semantic search"
```

---

### Task 3: Embed Knowledge Relations

**Files:**
- Modify: `apps/api/app/services/knowledge.py:301-322`

**Step 1: Add embedding call in `create_relation()`**

The file already imports `embed_and_store` and `delete_embedding` from `embedding_service` (used by entity functions). After line 321 (`db.refresh(relation)`), before `return relation`, add:

```python
    try:
        from app.services.embedding_service import embed_and_store
        embed_text = f"{relation.relation_type}: {from_entity.name} → {to_entity.name}"
        if relation.evidence:
            embed_text += f" ({relation.evidence[:300]})"
        embed_and_store(
            db,
            tenant_id=tenant_id,
            content_type="relation",
            content_id=str(relation.id),
            text_content=embed_text,
        )
    except Exception:
        logger.debug("Relation embedding skipped", exc_info=True)
```

Note: `from_entity` and `to_entity` are already fetched on lines 304-305. `logger` is already imported.

**Step 2: Verify**

Run: `cd apps/api && python -c "from app.services.knowledge import create_relation; print('OK')"`
Expected: OK

**Step 3: Commit**

```bash
git add apps/api/app/services/knowledge.py
git commit -m "feat: embed knowledge relations for semantic graph search"
```

---

### Task 4: Embed Agent Tasks

**Files:**
- Modify: `apps/api/app/services/chat.py:738-801`

**Step 1: Add embedding call in `_bridge_chat_to_workflow()`**

After line 794 (`db.add(trace)`) and before `db.commit()` on line 795, add:

```python
        try:
            _embed(
                db,
                tenant_id=session.tenant_id,
                content_type="agent_task",
                content_id=str(task.id),
                text_content=f"Task: {objective} | session:{session.id}",
            )
        except Exception:
            logger.debug("Task embedding skipped", exc_info=True)
```

Note: `_embed` was imported in Task 1. `objective` is defined on line 754.

**Step 2: Verify**

Run: `cd apps/api && python -c "from app.services.chat import _bridge_chat_to_workflow; print('OK')"`
Expected: OK

**Step 3: Commit**

```bash
git add apps/api/app/services/chat.py
git commit -m "feat: embed agent tasks for semantic task recall"
```

---

## Workstream B: Skills Unification

### Task 5: Create 7 Tool-Backed Native Skill Files

**Files:**
- Create: `apps/api/app/skills/sql_query/skill.md`
- Create: `apps/api/app/skills/data_summary/skill.md`
- Create: `apps/api/app/skills/calculator/skill.md`
- Create: `apps/api/app/skills/entity_extraction/skill.md`
- Create: `apps/api/app/skills/knowledge_search/skill.md`
- Create: `apps/api/app/skills/lead_scoring/skill.md`
- Create: `apps/api/app/skills/report_generation/skill.md`

**Step 1: Create all 7 skill directories and skill.md files**

Each skill.md follows this pattern (example for sql_query):

```yaml
---
name: SQL Query
engine: tool
tool_class: SQLQueryTool
version: 1
category: data
tags: [sql, query, data, analysis, datasets]
auto_trigger: "Run SQL query against connected datasets to retrieve and analyze data"
inputs:
  - name: query
    type: string
    description: "SQL query to execute"
    required: true
---

## Description
Execute SQL queries on connected datasets to retrieve and analyze data. Returns query results as structured data.
```

Create all 7 with appropriate metadata:

| Slug | tool_class | Category | Tags |
|---|---|---|---|
| sql_query | SQLQueryTool | data | sql, query, data, analysis |
| data_summary | DataSummaryTool | data | statistics, summary, data |
| calculator | CalculatorTool | general | math, calculator, compute |
| entity_extraction | EntityExtractionTool | automation | extraction, entities, NER |
| knowledge_search | KnowledgeSearchTool | general | knowledge, search, graph |
| lead_scoring | LeadScoringTool | sales | leads, scoring, qualification |
| report_generation | ReportGenerationTool | data | reports, excel, charts |

**Step 2: Verify files exist**

Run: `ls -la apps/api/app/skills/*/skill.md | wc -l`
Expected: 9 (2 existing + 7 new)

**Step 3: Commit**

```bash
git add apps/api/app/skills/
git commit -m "feat: add 7 tool-backed native skills to marketplace"
```

---

### Task 6: Create 3 Scoring Rubric Markdown Skills

**Files:**
- Create: `apps/api/app/skills/ai_lead_rubric/skill.md`
- Create: `apps/api/app/skills/hca_deal_rubric/skill.md`
- Create: `apps/api/app/skills/marketing_signal_rubric/skill.md`

**Step 1: Create rubric skill files**

Each rubric skill contains the full rubric from `scoring_rubrics.py` as the markdown body. Example for `ai_lead_rubric/skill.md`:

```yaml
---
name: AI Lead Scoring
engine: markdown
version: 1
category: sales
tags: [leads, scoring, AI, qualification]
auto_trigger: "Score leads for AI platform fit using the AI Lead Scoring rubric"
inputs:
  - name: entity_id
    type: string
    description: "Knowledge entity UUID to score"
    required: true
---

## Description
Score leads 0-100 based on likelihood of becoming a customer for an AI/agent orchestration platform.

## Scoring Rubric (0-100 total)

| Category | Max Points | What to look for |
|---|---|---|
| hiring | 25 | Job posts mentioning AI, ML, agents, orchestration, automation |
| tech_stack | 20 | Uses LangChain, OpenAI, Anthropic, CrewAI, AutoGen, or similar |
| funding | 20 | Recent funding round (Series A/B/C within 12 months) |
| company_size | 15 | Mid-market (50-500 employees) and growth-stage |
| news | 10 | Recent product launches, partnerships, AI initiatives |
| direct_fit | 10 | Explicit mentions of orchestration needs |
```

Copy the full rubric content from `apps/api/app/services/scoring_rubrics.py` for each:
- `ai_lead` → `ai_lead_rubric/skill.md`
- `hca_deal` → `hca_deal_rubric/skill.md`
- `marketing_signal` → `marketing_signal_rubric/skill.md`

**Step 2: Verify**

Run: `ls -la apps/api/app/skills/*/skill.md | wc -l`
Expected: 12 (2 original + 7 tool + 3 rubric)

**Step 3: Commit**

```bash
git add apps/api/app/skills/
git commit -m "feat: add 3 scoring rubric native skills to marketplace"
```

---

### Task 7: Add `tool` Engine Support to Skill Manager

**Files:**
- Modify: `apps/api/app/services/skill_manager.py`
- Modify: `apps/api/app/services/tool_executor.py`

**Step 1: Add tool class registry to `tool_executor.py`**

At the end of `apps/api/app/services/tool_executor.py`, add a registry dict:

```python
# Tool class registry — maps class name to class for skill manager lookup
TOOL_CLASS_REGISTRY = {
    "SQLQueryTool": SQLQueryTool,
    "DataSummaryTool": DataSummaryTool,
    "CalculatorTool": CalculatorTool,
    "EntityExtractionTool": EntityExtractionTool,
    "KnowledgeSearchTool": KnowledgeSearchTool,
    "LeadScoringTool": LeadScoringTool,
    "ReportGenerationTool": ReportGenerationTool,
}
```

Note: All these classes already exist in the file. Just add the registry dict.

**Step 2: Add `tool` engine handler to `skill_manager.py`**

In `skill_manager.py`, find the `execute()` method (or `_execute_python`). Add a new handler:

```python
def _execute_tool(self, skill: FileSkill, inputs: dict) -> str:
    """Execute a tool-backed skill via tool_executor."""
    tool_class_name = skill.metadata.get("tool_class") if hasattr(skill, 'metadata') else None
    if not tool_class_name:
        # Parse from skill.md frontmatter
        skill_dir = self._find_skill_dir(skill.slug)
        if skill_dir:
            content = (skill_dir / "skill.md").read_text()
            parts = content.split("---", 2)
            meta = yaml.safe_load(parts[1].strip()) if len(parts) >= 3 else {}
            tool_class_name = meta.get("tool_class")

    if not tool_class_name:
        return json.dumps({"error": f"No tool_class defined for skill {skill.name}"})

    from app.services.tool_executor import TOOL_CLASS_REGISTRY
    tool_cls = TOOL_CLASS_REGISTRY.get(tool_class_name)
    if not tool_cls:
        return json.dumps({"error": f"Unknown tool class: {tool_class_name}"})

    # Tool classes require different constructor args — return schema info for LLM
    return json.dumps({
        "tool_class": tool_class_name,
        "description": skill.description,
        "inputs": [{"name": i.name, "type": i.type, "required": i.required} for i in (skill.inputs or [])],
        "message": f"Tool '{skill.name}' is available. Use the corresponding agent tool to execute it.",
    })
```

Also update the `execute()` method to route `engine: tool`:

```python
if skill.engine == "tool":
    return self._execute_tool(skill, inputs)
```

**Step 3: Update `_parse_skill_md` to capture `tool_class`**

In `_parse_skill_md()`, the `tool_class` field from frontmatter is already stored in the metadata dict via yaml parsing. Ensure the FileSkill schema can carry it. In `apps/api/app/schemas/file_skill.py`, add to the `FileSkill` class:

```python
tool_class: Optional[str] = None
```

And in `_parse_skill_md()`, pass it through:

```python
tool_class=metadata.get("tool_class"),
```

**Step 4: Verify**

Run: `cd apps/api && python -c "from app.services.skill_manager import skill_manager; print('OK')"`
Expected: OK

**Step 5: Commit**

```bash
git add apps/api/app/services/skill_manager.py apps/api/app/services/tool_executor.py apps/api/app/schemas/file_skill.py
git commit -m "feat: add tool engine type to skill manager for tool-backed skills"
```

---

### Task 8: Rewrite Wizard SkillsDataStep as Skill Picker

**Files:**
- Rewrite: `apps/web/src/components/wizard/SkillsDataStep.js`

**Step 1: Rewrite SkillsDataStep**

Replace the entire file with a marketplace skill picker component:

```jsx
import React, { useState, useEffect, useMemo } from 'react';
import { Card, Form, Badge, Spinner, Alert } from 'react-bootstrap';
import { getFileSkills } from '../../services/skills';

const CATEGORY_COLORS = {
  sales: '#28a745', marketing: '#17a2b8', data: '#6f42c1',
  coding: '#fd7e14', communication: '#e83e8c', automation: '#20c997',
  general: '#6c757d',
};

const SkillCard = ({ skill, isSelected, onToggle }) => (
  <Card className="mb-2" style={{
    border: isSelected ? '2px solid #4dabf7' : '1px solid rgba(255,255,255,0.1)',
    background: isSelected ? 'rgba(77,171,247,0.08)' : 'rgba(255,255,255,0.03)',
    cursor: 'pointer',
  }} onClick={onToggle}>
    <Card.Body className="py-2 px-3">
      <div className="d-flex align-items-center justify-content-between">
        <div className="flex-grow-1">
          <div className="d-flex align-items-center gap-2">
            <Form.Check type="checkbox" checked={isSelected} onChange={onToggle}
              onClick={e => e.stopPropagation()} aria-label={skill.name} />
            <strong style={{ fontSize: '0.95rem' }}>{skill.name}</strong>
            <Badge bg="none" style={{
              backgroundColor: CATEGORY_COLORS[skill.category] || '#6c757d',
              fontSize: '0.7rem',
            }}>{skill.category}</Badge>
          </div>
          <small className="text-muted d-block mt-1" style={{ marginLeft: '2rem' }}>
            {skill.description?.substring(0, 120)}
          </small>
        </div>
        <small className="text-muted">{skill.engine}</small>
      </div>
    </Card.Body>
  </Card>
);

const SkillsDataStep = ({ data, onChange, templateName }) => {
  const [allSkills, setAllSkills] = useState([]);
  const [loading, setLoading] = useState(true);
  const [search, setSearch] = useState('');
  const [categoryFilter, setCategoryFilter] = useState('all');

  useEffect(() => {
    getFileSkills({ tier: 'native' })
      .then(res => setAllSkills(res.data?.skills || res.data || []))
      .catch(() => setAllSkills([]))
      .finally(() => setLoading(false));
  }, []);

  const selectedSlugs = useMemo(() => new Set(
    Object.entries(data.skills || {}).filter(([, v]) => v).map(([k]) => k)
  ), [data.skills]);

  const filtered = useMemo(() => {
    let list = allSkills;
    if (categoryFilter !== 'all') list = list.filter(s => s.category === categoryFilter);
    if (search) list = list.filter(s =>
      s.name.toLowerCase().includes(search.toLowerCase()) ||
      (s.description || '').toLowerCase().includes(search.toLowerCase())
    );
    return list;
  }, [allSkills, categoryFilter, search]);

  const categories = useMemo(() =>
    [...new Set(allSkills.map(s => s.category))].sort(), [allSkills]);

  const handleToggle = (slug) => {
    const updated = { ...data.skills, [slug]: !data.skills?.[slug] };
    onChange({ ...data, skills: updated });
  };

  if (loading) return <div className="text-center py-5"><Spinner animation="border" /></div>;

  return (
    <div className="skills-data-step">
      <h3 className="mb-2">What can your agent do?</h3>
      <p className="text-muted mb-3">Select skills from the marketplace</p>

      {templateName && (
        <Alert variant="success" className="mb-3">
          <small>✓ Based on your <strong>{templateName}</strong> template, we've pre-selected recommended skills.</small>
        </Alert>
      )}

      <Form.Control type="text" placeholder="Search skills..." value={search}
        onChange={e => setSearch(e.target.value)} className="mb-3"
        style={{ background: 'rgba(255,255,255,0.05)', border: '1px solid rgba(255,255,255,0.15)', color: '#fff' }} />

      <div className="d-flex gap-2 flex-wrap mb-3">
        <Badge bg={categoryFilter === 'all' ? 'primary' : 'secondary'} role="button"
          onClick={() => setCategoryFilter('all')}>All</Badge>
        {categories.map(c => (
          <Badge key={c} bg={categoryFilter === c ? 'primary' : 'secondary'} role="button"
            onClick={() => setCategoryFilter(c)} style={{ textTransform: 'capitalize' }}>{c}</Badge>
        ))}
      </div>

      <small className="text-muted mb-2 d-block">
        {selectedSlugs.size} skill{selectedSlugs.size !== 1 ? 's' : ''} selected
      </small>

      {filtered.map(skill => (
        <SkillCard key={skill.slug || skill.name} skill={skill}
          isSelected={!!selectedSlugs.has(skill.slug || skill.name)}
          onToggle={() => handleToggle(skill.slug || skill.name)} />
      ))}

      {filtered.length === 0 && (
        <p className="text-muted text-center py-4">No skills match your search.</p>
      )}
    </div>
  );
};

export default SkillsDataStep;
```

**Step 2: Verify**

Run: `cd apps/web && npx react-scripts build 2>&1 | tail -5`
Expected: Build succeeds (or at least no errors in SkillsDataStep)

**Step 3: Commit**

```bash
git add apps/web/src/components/wizard/SkillsDataStep.js
git commit -m "feat: replace wizard tool toggles with marketplace skill picker"
```

---

### Task 9: Update Template Configs — `tools` → `skills`

**Files:**
- Modify: `apps/web/src/components/wizard/TemplateSelector.js`
- Modify: `apps/web/src/components/wizard/AgentWizard.js`

**Step 1: Update TemplateSelector templates**

In `apps/web/src/components/wizard/TemplateSelector.js`, for each template config object:
- Rename `tools` array to `skills`
- For templates with `scoring_rubric`, add the rubric slug to `skills` and remove `scoring_rubric`

Example for sales_assistant:
```javascript
// Before
tools: ['entity_extraction', 'knowledge_search', 'lead_scoring', 'calculator'],
scoring_rubric: 'ai_lead',

// After
skills: ['entity_extraction', 'knowledge_search', 'lead_scoring', 'calculator', 'ai_lead_rubric'],
```

Apply to all templates. Remove all `scoring_rubric` fields.

**Step 2: Update AgentWizard.js**

In `apps/web/src/components/wizard/AgentWizard.js`, find where `template.config.tools` is used to initialize wizard data. Change to read from `template.config.skills` with fallback:

```javascript
const toolsList = template.config.skills || template.config.tools || [];
const skillsObj = {};
toolsList.forEach(t => { skillsObj[t] = true; });
```

Also update the agent creation payload — where `config.tools` is sent to the API, add `config.skills`:

```javascript
config: {
  ...config,
  skills: Object.entries(wizardData.skills).filter(([, v]) => v).map(([k]) => k),
  // Keep tools for backward compat
  tools: Object.entries(wizardData.skills).filter(([, v]) => v).map(([k]) => k),
}
```

**Step 3: Verify**

Run: `cd apps/web && npx react-scripts build 2>&1 | tail -5`
Expected: Build succeeds

**Step 4: Commit**

```bash
git add apps/web/src/components/wizard/TemplateSelector.js apps/web/src/components/wizard/AgentWizard.js
git commit -m "feat: update wizard templates from tools to skills array"
```

---

### Task 10: Delete SkillsManagementPanel

**Files:**
- Delete: `apps/web/src/components/SkillsManagementPanel.js`
- Modify: any file that imports it

**Step 1: Find references**

Run: `grep -r "SkillsManagementPanel" apps/web/src/`

**Step 2: Remove imports and usages**

Remove the import and any `<SkillsManagementPanel />` usage from parent components (likely `SettingsPage.js` or similar).

**Step 3: Delete the file**

```bash
rm apps/web/src/components/SkillsManagementPanel.js
```

**Step 4: Verify build**

Run: `cd apps/web && npx react-scripts build 2>&1 | tail -5`
Expected: Build succeeds

**Step 5: Commit**

```bash
git add -A apps/web/src/
git commit -m "chore: remove SkillsManagementPanel — rubrics now managed in marketplace"
```

---

## Implementation Order

1. **Tasks 1-4** (Embedding wiring) — independent of each other, can be done in parallel
2. **Tasks 5-6** (Skill files) — creates the native skills on disk
3. **Task 7** (Tool engine) — backend support for new engine type
4. **Tasks 8-9** (Wizard rewrite) — frontend changes
5. **Task 10** (Cleanup) — remove old panel

## Verification

After all tasks:
1. Send a chat message → verify embedding created in `embeddings` table with `content_type='chat_message'`
2. Upload a PDF → verify `content_type='attachment'` embedding
3. Create a knowledge relation → verify `content_type='relation'` embedding
4. Open Skills page → verify 12 native skills (2 original + 7 tool + 3 rubric)
5. Open agent wizard → verify skill picker loads skills from API with search and category filters
6. Create agent from Sales template → verify `config.skills` includes `ai_lead_rubric`
