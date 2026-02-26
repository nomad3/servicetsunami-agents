# Customer Support & Sales Agents Design

## Context

ServiceTsunami is an agent orchestration platform. The ADK supervisor currently routes to 4 sub-agents: data_analyst, report_generator, knowledge_manager, web_researcher. Customer-facing messages (WhatsApp, chat) that don't match these categories get rejected. There's no conversational, customer support, or sales automation capability.

Both WhatsApp and the chat UI share the same pipeline: `chat_service.post_user_message()` → enhanced_chat (memory, LLM routing) → ADK supervisor → sub-agents → tools → context_manager → knowledge_extraction. New agents automatically work for both channels.

## Goals

1. Add a **customer_support** agent that handles inbound customer interactions — FAQ, product inquiries, order status, complaints, and general conversation.
2. Add a **sales_agent** that handles outbound sales automation — lead qualification, outreach drafting, pipeline management, proposals, and scheduled follow-ups.
3. Both agents are **generic/multi-tenant** — tenants configure their own product catalogs, FAQs, policies, and pipeline stages via the knowledge graph and agent kit config.
4. Both agents query **tenant-connected data sources** (Shopify, WooCommerce, Snowflake, PostgreSQL, REST APIs) through the existing connector infrastructure.
5. Maximize reuse of existing components — no new models, no new API routes, no new services.

## Architecture

```
servicetsunami_supervisor (router)
├── data_analyst          (existing)
├── report_generator      (existing)
├── knowledge_manager     (existing)
├── web_researcher        (existing)
├── customer_support      ← NEW
└── sales_agent           ← NEW
```

### Supervisor Routing Additions

- Customer questions, FAQ, product inquiries, order status, complaints → `customer_support`
- General conversation, greetings, casual messages → `customer_support` (catch-all)
- Lead qualification, outreach, pipeline, proposals, sales automation → `sales_agent`
- Research prospect then qualify → `web_researcher` first, then `sales_agent`
- Creating/scoring entities → still `knowledge_manager` (unchanged)

## Customer Support Agent

### Purpose

Handle inbound customer interactions from any channel. Searches tenant knowledge base for answers, queries connected data sources for order/inventory/customer data, and escalates to human when needed.

### System Prompt Behavior

- Adapts tone per tenant (configured in agent kit personality: formal, friendly, etc.)
- Always searches knowledge graph before saying "I don't know"
- Can query tenant's connected databases for real-time data (order status, inventory, account info)
- Escalates after 2 failed attempts to answer, or on explicit request
- Records customer feedback as observations for knowledge extraction
- Handles general conversation naturally (greetings, small talk, clarifications)

### Tools

| Tool | Source | Purpose |
|------|--------|---------|
| `search_knowledge` | existing (knowledge_tools.py) | FAQ/policy/product lookup |
| `find_entities` | existing (knowledge_tools.py) | Customer/order/product entity lookup |
| `record_observation` | existing (knowledge_tools.py) | Log customer feedback |
| `query_data_source` | **NEW** (connector_tools.py) | Query tenant connectors for live data |

## Sales Agent

### Purpose

Full sales automation — research, qualify, draft personalized outreach, manage pipeline stages, generate proposals, and schedule follow-ups. Works both proactively (internal user: "find me leads") and reactively (WhatsApp prospect inquiry).

### System Prompt Behavior

- Professional sales methodology (BANT qualification framework)
- Leverages knowledge graph for prospect context and competitive intel
- Queries connected CRM/ecommerce data sources for customer history
- Pipeline stages are configurable per tenant via agent kit config
- Can schedule follow-up actions via Temporal workflows

### Tools

| Tool | Source | Purpose |
|------|--------|---------|
| `search_knowledge` | existing (knowledge_tools.py) | Prospect/product/competitive intel lookup |
| `find_entities` / `create_entity` | existing (knowledge_tools.py) | Lead/contact CRUD |
| `update_entity` | existing (knowledge_tools.py) | Update lead properties and pipeline stage |
| `score_entity` | existing (knowledge_tools.py) | Lead scoring (ai_lead, hca_deal, marketing_signal) |
| `search_and_scrape` | existing (web_researcher tools) | Research prospects online |
| `record_observation` | existing (knowledge_tools.py) | Log sales intelligence |
| `query_data_source` | **NEW** (connector_tools.py) | Query CRM, ecommerce DB, pipeline data |
| `qualify_lead` | **NEW** (sales_tools.py) | BANT qualification via LLM |
| `draft_outreach` | **NEW** (sales_tools.py) | Personalized message generation |
| `update_pipeline_stage` | **NEW** (sales_tools.py) | Move entity through funnel stages |
| `get_pipeline_summary` | **NEW** (sales_tools.py) | Aggregate pipeline metrics |
| `generate_proposal` | **NEW** (sales_tools.py) | Create proposal from product catalog + lead context |
| `schedule_followup` | **NEW** (sales_tools.py) | Temporal timer for follow-up actions |

## New Tool Designs

### query_data_source (connector_tools.py)

Bridges agents to tenant-connected data sources. Uses existing connector credentials and connection logic for on-demand read queries.

```python
def query_data_source(
    tenant_id: str,        # Tenant isolation
    query: str,            # SQL query or natural language question
    connector_id: str = None,  # Specific connector (optional, auto-discovers if omitted)
    connector_type: str = None,  # Filter by type: postgres, mysql, snowflake, api
) -> dict:
    # Returns: {success, columns, rows, row_count, connector_used}
```

Implementation: Calls the API's existing `/api/v1/connectors` to list available connectors, then uses the same connection logic from `workflows/activities/connectors/extract.py` to execute read-only queries. For REST API connectors, translates the query to an HTTP GET with appropriate parameters.

### qualify_lead (sales_tools.py)

LLM-powered BANT qualification. Fetches entity context via existing `get_entity`, runs qualification prompt, updates entity properties.

```python
def qualify_lead(
    entity_id: str,
    tenant_id: str,
) -> dict:
    # Returns: {score, budget, authority, need, timeline, summary, qualified: bool}
```

Stores result in entity `properties.qualification` and updates `properties.pipeline_stage` to "qualified" or "unqualified".

### draft_outreach (sales_tools.py)

Generates personalized outreach messages. Pure LLM generation using entity context + tenant product info from knowledge graph.

```python
def draft_outreach(
    entity_id: str,
    tenant_id: str,
    channel: str = "email",  # email, whatsapp, linkedin
    tone: str = "professional",
) -> dict:
    # Returns: {subject, body, channel, entity_name}
```

### update_pipeline_stage (sales_tools.py)

Thin wrapper around existing `update_entity`. Updates `properties.pipeline_stage` and records stage transition as an observation.

```python
def update_pipeline_stage(
    entity_id: str,
    new_stage: str,    # Must be in tenant's configured pipeline_stages
    tenant_id: str,
    reason: str = "",
) -> dict:
    # Returns: {entity_id, previous_stage, new_stage, updated_at}
```

### get_pipeline_summary (sales_tools.py)

Queries knowledge entities to aggregate pipeline metrics.

```python
def get_pipeline_summary(
    tenant_id: str,
    pipeline_stages: list = None,  # Override tenant default
) -> dict:
    # Returns: {stages: [{name, count, total_value}], total_leads, conversion_rate}
```

### generate_proposal (sales_tools.py)

LLM generation using lead entity + product/service entities from knowledge graph.

```python
def generate_proposal(
    entity_id: str,
    tenant_id: str,
    product_ids: list = None,  # Specific products, or auto-selects from knowledge graph
) -> dict:
    # Returns: {title, sections: [{heading, content}], total_value, entity_name}
```

### schedule_followup (sales_tools.py)

Starts a Temporal timer workflow for delayed actions.

```python
def schedule_followup(
    entity_id: str,
    tenant_id: str,
    action: str,         # "send_email", "send_whatsapp", "update_stage", "remind"
    delay_hours: int,
    message: str = "",
) -> dict:
    # Returns: {workflow_id, scheduled_for, action, entity_id}
```

## Pipeline Stage Configuration

Stored in agent kit config, customizable per tenant:

```json
{
  "pipeline_stages": ["prospect", "qualified", "proposal", "negotiation", "closed_won", "closed_lost"],
  "default_stage": "prospect"
}
```

Examples for different business types:
- SaaS: `["trial", "demo", "proposal", "negotiation", "closed"]`
- Ecommerce: `["lead", "cart_abandoned", "re_engaged", "purchased", "repeat"]`
- Consulting: `["inquiry", "discovery", "proposal", "contract", "engaged"]`

## Temporal Workflow: FollowUpWorkflow

Single new workflow for scheduled follow-up actions.

```python
@workflow.defn
class FollowUpWorkflow:
    @workflow.run
    async def run(self, input: FollowUpInput):
        # 1. Wait for the scheduled delay
        await workflow.sleep(timedelta(hours=input.delay_hours))
        # 2. Execute the follow-up action
        await workflow.execute_activity(
            execute_followup_action,
            args=[input],
            start_to_close_timeout=timedelta(minutes=5),
        )
```

Activities:
- `execute_followup_action` — Routes to: send WhatsApp message (via whatsapp_service), update pipeline stage, or create a reminder notification.

Registered on `servicetsunami-orchestration` task queue alongside existing workflows.

## WhatsApp Self-Message Bug Fix

Current bug: Bot processes messages the user sends to friends (is_from_me=True in any chat). Should only process:
- `is_from_me=False` — someone DMing the bot's number
- `is_from_me=True AND chat_jid == sender_jid` — user messaging themselves (personal bot pattern)

Fix in `_handle_inbound`:
```python
if is_from_me and chat_jid != sender_jid:
    return  # User messaging someone else, not for the bot
```

## File Changes

### New Files

| File | Purpose |
|------|---------|
| `apps/adk-server/servicetsunami_supervisor/customer_support.py` | Agent definition + system prompt |
| `apps/adk-server/servicetsunami_supervisor/sales_agent.py` | Agent definition + system prompt |
| `apps/adk-server/tools/connector_tools.py` | `query_data_source` tool |
| `apps/adk-server/tools/sales_tools.py` | Sales-specific tools (qualify, outreach, pipeline, proposal, followup) |
| `apps/api/app/workflows/follow_up.py` | FollowUpWorkflow + FollowUpInput |
| `apps/api/app/workflows/activities/follow_up.py` | execute_followup_action activity |

### Modified Files

| File | Change |
|------|--------|
| `apps/adk-server/servicetsunami_supervisor/agent.py` | Add 2 sub-agents + routing rules to supervisor |
| `apps/adk-server/servicetsunami_supervisor/__init__.py` | Export new agents |
| `apps/api/app/workers/orchestration_worker.py` | Register FollowUpWorkflow + activity |
| `apps/api/app/services/whatsapp_service.py` | Fix self-message bug (chat_jid != sender_jid check) |
| `apps/web/src/components/wizard/TemplateSelector.js` | Update customer_support and sales_assistant templates with real tools |

### No Changes Needed

- No new models (entities, pipeline stages, proposals all stored in knowledge graph properties)
- No new API routes (agents call existing endpoints, Temporal handles scheduling)
- No new services (reuses chat_service, connectors, knowledge graph)
- No new frontend pages (existing wizard + chat UI handle everything)

## Reused Components

| Component | Reused By |
|-----------|-----------|
| `chat_service.post_user_message()` | Both agents via WhatsApp + chat UI |
| `enhanced_chat.py` (memory, LLM routing) | Both agents |
| `context_manager.py` (summarization) | Both agents |
| `knowledge_tools.py` (13 tools) | Both agents bind to these directly |
| `data_tools.py` (query_sql, etc.) | Sales agent for analytics |
| Connector infrastructure (7 types) | `query_data_source` reuses connection logic |
| Knowledge extraction workflow | Runs post-conversation for both agents |
| Agent kit config (personality, tools) | Tenants customize agent behavior |
| Scoring rubrics (ai_lead, hca_deal, marketing_signal) | Sales agent uses existing scoring |
