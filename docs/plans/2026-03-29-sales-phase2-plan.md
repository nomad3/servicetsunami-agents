# Luna Sales Phase 2 — Customer Acquisition Engine

**Goal**: Turn Luna into a real sales co-pilot that proactively finds, qualifies, and closes customers for agentprovision.com.

**Date**: 2026-03-29
**Context**: Phase 1 delivered tool scaffolding (qualify_lead, draft_outreach, update_pipeline_stage, generate_proposal, schedule_followup) and Temporal workflow infrastructure. All 5 ProspectingPipelineWorkflow activities are stubs. No inbound lead capture. No actual message sending from the sales flow.

## Ideal Customer Profile (ICP)

**Primary use case**: Luna as always-on **business and technical support agent** — replaces 2-3 support/ops headcount at a fraction of the cost.

**Target segments:**
- Vet clinics, medical practices, law offices, accounting firms (professional services needing client support)
- SaaS startups needing tier-1 technical support on WhatsApp + email
- Agencies and e-commerce businesses in LATAM (WhatsApp-heavy operations)

**Profile:** 5–200 employees · Founder or ops lead decision-maker · $500–5K/month budget · Pain: manual support overload, no AI yet · Geography: LATAM + US

**Proof point:** Brett Boorstin (BB Cardiology) — first external customer. Vet cardiologist using Luna for cardiac report automation and client support.

**Pitch:** *"Your business, always on. Luna handles support and sales conversations 24/7 on WhatsApp, email, and web — trained on your knowledge base, connected to your tools."*

## Code Review Corrections (2026-03-29)

Issues found by reviewing the actual codebase before implementation:

1. **Activity file location**: Stubs live in `prospecting_pipeline.py` directly, not a separate `activities/prospecting.py`. Implementation should happen in-place, then extract to `activities/prospecting.py` as a follow-up refactor — updating the import in `orchestration_worker.py` accordingly.

2. **Bug in `execute_followup_action`**: The `remind` action creates `KnowledgeObservation` without setting `entity_id` — observation is unlinked. Fix this before building Module 3 on top of it.

3. **LinkedIn scraping blocked**: Module 1.2 originally called for Playwright scraping of LinkedIn — violates ToS and gets IP-blocked. Replace with: Apollo.io free API, Hunter.io email finder, or Google News + company website scraping only.

4. **`prospect_score` duplicates `qualify_lead`**: Both do BANT scoring. `prospect_score` activity should call `qualify_lead` via internal API (`POST /api/v1/knowledge/leads/{id}/qualify`) rather than re-implementing scoring logic.

5. **Daily briefing needs a trigger**: Module 6.1 ("every morning") has no mechanism. Use `scheduler_worker.py` cron system with a `0 8 * * *` pipeline, or a dedicated Temporal schedule — not the InboxMonitorWorkflow (fires every 15 min).

6. **Inbound webhook needs rate limiting**: `POST /api/v1/sales/inbound` must be public — add slowapi IP rate limiting (10 req/min) + optional API key header to prevent spam.

---

## What Needs to Happen

### The user's goal in plain terms:
Simon needs paying customers. Luna needs to:
1. Know who to target (ICP definition + lead sourcing)
2. Research and enrich prospects automatically
3. Draft and send personalized outreach (email + WhatsApp)
4. Track conversations and move deals forward
5. Give Simon a clear view of his pipeline at any time

---

## Phase 2 — 6 Modules

---

### Module 1: ICP + Lead Sourcing

**What**: Define Simon's Ideal Customer Profile and auto-source leads from web research.

**Why it's blocking**: Luna can't proactively find customers because there's no ICP defined and no sourcing mechanism. The `prospect_research` activity is a stub.

**Tasks**:

**1.1 — ICP entity in knowledge graph**
Create a structured `ideal_customer_profile` entity in the KG with:
- Industry verticals (SMBs, agencies, startups, vet clinics, e-commerce)
- Company size (5–200 employees)
- Pain points (too many manual tasks, no AI yet, WhatsApp-heavy business)
- Budget signal ($500–5K/month)
- Decision-maker persona (founder, ops lead, head of marketing)
- Geographic focus (LATAM, US)

Luna auto-references this when scoring new leads.

**1.2 — Implement `prospect_research` activity**
Replace stub in `prospecting_pipeline.py` with real logic:
- Apollo.io free API or Hunter.io for contact discovery (not LinkedIn scraping — ToS violation)
- Google News + company website scraping via existing Playwright BrowserService
- Extract: company name, website, founder/CEO name, email, pain signals from about/blog pages
- Store as KG entity with `category=lead`, `pipeline_stage=prospect`
- Target sources: ProductHunt, YC directory, Google Maps (local businesses), news searches

**1.3 — MCP tool: `source_leads`**
New tool that triggers batch prospecting:
```
source_leads(vertical, location, count=20)
→ runs research for N companies → returns created entity IDs
```

**Files**: `apps/mcp-server/src/mcp_tools/sales.py`, `apps/api/app/workflows/prospecting_pipeline.py` → extract to `apps/api/app/workflows/activities/prospecting.py`

---

### Module 2: Lead Enrichment + Scoring

**What**: Auto-enrich leads with real data and score them against ICP using a rubric.

**Why it's blocking**: BANT scoring today relies on data already in entity properties. Cold leads always score 0. There's no enrichment.

**Tasks**:

**2.1 — Implement `prospect_score` activity**
Replace stub in `prospecting_pipeline.py`. Call existing `qualify_lead` via internal API rather than re-implementing BANT scoring. For each lead entity:
- Call `POST /api/v1/knowledge/leads/{id}/qualify` (reuses qualify_lead MCP tool logic)
- Supplement with: website scrape for tech stack / team size signals, news search for buying signals (funding, hiring, expansions)
- Write enriched score + breakdown back to entity properties

**2.2 — Configurable scoring rubric**
Store rubric in KG as a `scoring_rubric` entity per tenant. Luna reads it before scoring.
Default rubric:
- Industry match: 25 pts
- Size match: 20 pts
- Pain signals detected: 25 pts
- Buying signals (funding, hiring): 20 pts
- Has direct contact info: 10 pts

**2.3 — Score threshold routing**
In `ProspectingPipelineWorkflow`:
- Score ≥ 70 → auto-advance to `qualified`, trigger outreach
- Score 40–69 → flag for Simon's review, notify
- Score < 40 → mark `disqualified`, log reason

**Files**: `apps/api/app/workflows/prospecting_pipeline.py`

---

### Module 3: Outreach Execution

**What**: Actually send personalized outreach emails and WhatsApp messages — not just drafts.

**Why it's blocking**: `draft_outreach` produces a template string but never sends anything. Simon has to copy-paste manually.

**Tasks**:

**3.1 — LLM-powered outreach generation**
Replace template string in `draft_outreach` with a real LLM call:
- Input: lead entity (company, role, pain signals, ICP match reason)
- Output: personalized subject + 3-paragraph email, WhatsApp variant (shorter, casual)
- Store draft as observation on the lead entity

**3.2 — Add `send_email` action to `execute_followup_action`**
Current actions: `send_whatsapp`, `update_stage`, `remind`
Add: `send_email` — calls the existing email MCP send_email tool via internal API

**3.3 — Outreach approval loop**
New flow for Simon:
1. Luna generates draft → creates notification "Outreach ready for [Company] — approve to send"
2. Simon replies "send it" or "edit: [changes]"
3. Luna sends via email or WhatsApp
4. Records sent timestamp + channel on entity

**3.4 — MCP tool: `send_outreach`**
```
send_outreach(lead_entity_id, channel="email"|"whatsapp", approved=True)
→ fetches draft from entity observations → sends → logs sent event
```

**Files**: `apps/mcp-server/src/mcp_tools/sales.py`, `apps/api/app/workflows/activities/follow_up.py`

---

### Module 4: Pipeline Visibility

**What**: Give Simon a real-time view of his pipeline — accurate counts, deal values, next actions.

**Why it's blocking**: `get_pipeline_summary` uses fuzzy text search (unreliable). No frontend CRM view. No deal value tracking.

**Tasks**:

**4.1 — Fix `get_pipeline_summary` with SQL**
Replace fuzzy KG search with direct SQL COUNT by pipeline_stage from `knowledge_entities` table:
```sql
SELECT properties->>'pipeline_stage' as stage, COUNT(*)
FROM knowledge_entities
WHERE tenant_id = :tid AND category = 'lead'
GROUP BY stage
```

**4.2 — Add deal value to lead entities**
Standard properties on lead entities:
- `deal_value` (number, monthly $ or total contract)
- `probability` (0–100, auto-set by stage)
- `expected_close_date`
- `last_contact_date`
- `next_action` + `next_action_date`

**4.3 — `/api/v1/sales` router**
New FastAPI router with:
- `GET /sales/pipeline` — funnel by stage with counts + total value
- `GET /sales/leads` — paginated leads with filters (stage, score, last_contact)
- `GET /sales/leads/{id}` — full lead detail with activity timeline
- `POST /sales/leads` — create lead directly
- `PATCH /sales/leads/{id}/stage` — move stage

Mount in `routes.py`.

**4.4 — Pipeline dashboard card in Luna web UI**
Simple pipeline view: kanban-style or funnel chart showing leads by stage with deal values.

**Files**: `apps/api/app/api/v1/sales.py`, `apps/api/app/api/v1/routes.py`, `apps/web/src/pages/`

---

### Module 5: Inbound Lead Capture

**What**: Capture leads from web forms, emails, and WhatsApp automatically — zero manual entry.

**Why it's blocking**: New leads must be created manually. Simon is losing leads from the workshop, the web, and inbound emails.

**Tasks**:

**5.1 — Email-to-lead**
In the InboxMonitorWorkflow, add a `classify_as_lead` step:
- If email sender is unknown + content mentions product interest / pricing / demo → auto-create lead entity from sender
- Extract: name, company (from email signature), pain signal (from body)
- Set stage = `prospect`, source = `inbound_email`
- Notify Simon: "New inbound lead from [name] at [company]"

**5.2 — WhatsApp-to-lead**
Unknown WhatsApp number with sales-intent message → auto-create lead entity + start qualification flow

**5.3 — Web form webhook**
`POST /api/v1/sales/inbound` — accepts a JSON payload (name, email, company, message) and creates a lead entity. Usable from any landing page via a simple fetch call. Must include: slowapi IP rate limiting (10 req/min) + optional `X-Inbound-Key` header to prevent spam.

**5.4 — Workshop attendee import**
One-off: import the 4 workshop attendees from today as leads with stage = `prospect`, source = `workshop_2026_03_29`.

**Files**: `apps/api/app/workers/orchestration_worker.py` (InboxMonitorWorkflow), `apps/api/app/api/v1/sales.py`

---

### Module 6: Proactive Sales Co-pilot Behaviors

**What**: Luna proactively surfaces deals at risk, follow-ups due, and new opportunities — without being asked.

**Tasks**:

**6.1 — Daily sales briefing**
Every morning (8am) Luna sends Simon a WhatsApp summary. Triggered via `scheduler_worker.py` cron pipeline (`0 8 * * *`) — NOT InboxMonitorWorkflow (fires every 15 min):
- Deals that haven't moved in 7+ days
- Follow-ups due today
- Leads that scored high but haven't been contacted
- Any replies to outreach in the last 24h

**6.2 — Stale deal detection**
In InboxMonitorWorkflow cycle: check all leads where `last_contact_date` > 7 days → create notification "Deal stale: [Company] hasn't been touched in X days"

**6.3 — Reply detection**
When an email reply comes in from a known lead entity → automatically link email to entity, update `last_contact_date`, notify Simon with context: "Maria from Desi Store replied to your outreach"

**6.4 — Luna can self-trigger prospecting**
If pipeline has < 5 qualified leads → Luna proactively runs `source_leads` and notifies Simon: "Your pipeline is thin. I found 8 new prospects — want me to enrich and score them?"

---

## Implementation Order

| Week | Modules | Why this order |
|------|---------|----------------|
| Week 1 | Module 5 (inbound capture) + Module 4.1-4.2 (pipeline fix) | Immediate value — stop losing leads, get accurate pipeline view |
| Week 2 | Module 3 (outreach execution) | Send real messages, start conversations |
| Week 3 | Module 1 + 2 (ICP + enrichment) | Automate top-of-funnel once manual flow is proven |
| Week 4 | Module 6 (proactive behaviors) + Module 4.3-4.4 (dashboard) | Polish and automate once data is flowing |

---

## Success Metrics

- 10 qualified leads in pipeline within 2 weeks of launch
- At least 3 outreach emails sent per week without Simon manually drafting
- Zero leads lost from inbound (email, WhatsApp, web)
- Simon gets a daily briefing that saves him ≥30 min of manual pipeline checking

---

## Key Files to Touch

| File | Change |
|------|--------|
| `apps/mcp-server/src/mcp_tools/sales.py` | source_leads, send_outreach, fix draft_outreach LLM call |
| `apps/api/app/workflows/prospecting_pipeline.py` | Implement prospect_research, prospect_score stubs; extract to activities/prospecting.py |
| `apps/api/app/workflows/activities/follow_up.py` | Fix remind bug (missing entity_id); add send_email action type |
| `apps/api/app/api/v1/sales.py` | New router: pipeline, leads CRUD, inbound webhook |
| `apps/api/app/api/v1/routes.py` | Mount sales router |
| `apps/api/app/workers/orchestration_worker.py` | Email-to-lead in InboxMonitor |
| `apps/api/migrations/` | New migration for any new columns |
