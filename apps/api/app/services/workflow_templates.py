"""Native workflow templates — pre-built workflows users can install with one click."""

NATIVE_TEMPLATES = [
    {
        "name": "Daily Briefing",
        "description": "Every morning: scan inbox + calendar, extract key items, send summary via WhatsApp",
        "tier": "native",
        "public": True,
        "tags": ["inbox", "calendar", "briefing", "daily"],
        "trigger_config": {"type": "cron", "schedule": "0 8 * * *", "timezone": "UTC"},
        "definition": {
            "steps": [
                {
                    "id": "scan_inbox",
                    "type": "mcp_tool",
                    "tool": "search_emails",
                    "params": {"query": "is:unread newer_than:1d", "max_results": 20},
                    "output": "emails",
                },
                {
                    "id": "check_calendar",
                    "type": "mcp_tool",
                    "tool": "list_calendar_events",
                    "params": {"days_ahead": 1},
                    "output": "events",
                },
                {
                    "id": "generate_briefing",
                    "type": "agent",
                    "agent": "luna",
                    "prompt": (
                        "Generate my daily briefing.\n\n"
                        "Unread emails:\n{{emails}}\n\n"
                        "Today's calendar:\n{{events}}\n\n"
                        "Format as a concise briefing with: key emails to respond to, "
                        "meetings today, and action items. Keep it short."
                    ),
                    "output": "briefing",
                },
            ],
        },
    },
    {
        "name": "Lead Pipeline",
        "description": "When a new contact is created: score with AI lead rubric, enrich data, notify if hot",
        "tier": "native",
        "public": True,
        "tags": ["leads", "sales", "scoring"],
        "trigger_config": {"type": "event", "event_type": "entity_created"},
        "definition": {
            "steps": [
                {
                    "id": "get_entity",
                    "type": "mcp_tool",
                    "tool": "get_entity",
                    "params": {"entity_id": "{{input.entity_id}}"},
                    "output": "entity",
                },
                {
                    "id": "score",
                    "type": "mcp_tool",
                    "tool": "score_entity",
                    "params": {"entity_id": "{{input.entity_id}}", "rubric": "ai_lead"},
                    "output": "score_result",
                },
                {
                    "id": "check_hot",
                    "type": "condition",
                    "if": "{{score_result.score}} >= 70",
                    "then": "notify",
                    "else": "skip",
                },
                {
                    "id": "notify",
                    "type": "agent",
                    "agent": "luna",
                    "prompt": (
                        "Hot lead detected! {{entity.name}} scored {{score_result.score}}/100.\n"
                        "Reasoning: {{score_result.reasoning}}\n"
                        "Send me a quick summary of this lead."
                    ),
                    "output": "notification",
                },
            ],
        },
    },
    {
        "name": "Competitor Watch",
        "description": "Daily: check competitor entities in knowledge graph, scrape for changes, alert on updates",
        "tier": "native",
        "public": True,
        "tags": ["competitors", "monitoring", "marketing"],
        "trigger_config": {"type": "cron", "schedule": "0 9 * * *", "timezone": "UTC"},
        "definition": {
            "steps": [
                {
                    "id": "list_competitors",
                    "type": "mcp_tool",
                    "tool": "list_competitors",
                    "params": {},
                    "output": "competitors",
                },
                {
                    "id": "analyze",
                    "type": "agent",
                    "agent": "luna",
                    "prompt": (
                        "Review these competitors and check for any recent changes, "
                        "news, or updates:\n{{competitors}}\n\n"
                        "Summarize any notable changes."
                    ),
                    "output": "analysis",
                },
            ],
        },
    },
    {
        "name": "Code Review Pipeline",
        "description": "When a PR is opened: analyze changes, check for issues, post review summary",
        "tier": "native",
        "public": True,
        "tags": ["code", "github", "review"],
        "trigger_config": {"type": "webhook", "webhook_slug": "github-pr"},
        "definition": {
            "steps": [
                {
                    "id": "get_pr",
                    "type": "mcp_tool",
                    "tool": "get_pull_request",
                    "params": {"repo": "{{input.repo}}", "pr_number": "{{input.pr_number}}"},
                    "output": "pr",
                },
                {
                    "id": "review",
                    "type": "agent",
                    "agent": "luna",
                    "prompt": (
                        "Review this pull request:\n"
                        "Title: {{pr.title}}\n"
                        "Description: {{pr.body}}\n"
                        "Files changed: {{pr.files}}\n\n"
                        "Check for: bugs, security issues, code quality, test coverage. "
                        "Be concise."
                    ),
                    "output": "review_result",
                },
            ],
        },
    },
    {
        "name": "Weekly Report",
        "description": "Every Friday: gather metrics from the week, generate summary report, email stakeholders",
        "tier": "native",
        "public": True,
        "tags": ["reports", "weekly", "metrics"],
        "trigger_config": {"type": "cron", "schedule": "0 17 * * 5", "timezone": "UTC"},
        "definition": {
            "steps": [
                {
                    "id": "gather_chat_stats",
                    "type": "mcp_tool",
                    "tool": "search_knowledge",
                    "params": {"query": "conversations tasks completed this week", "max_results": 20},
                    "output": "weekly_context",
                },
                {
                    "id": "generate_report",
                    "type": "agent",
                    "agent": "luna",
                    "prompt": (
                        "Generate a weekly summary report.\n\n"
                        "Context from this week:\n{{weekly_context}}\n\n"
                        "Include: key accomplishments, metrics, issues resolved, "
                        "and priorities for next week. Format as a professional report."
                    ),
                    "output": "report",
                },
            ],
        },
    },
    # ── Tier 1 linear workflow migrations ──────────────────────────────
    {
        "name": "Sales Follow-Up",
        "description": "Wait a configurable delay then execute a follow-up action: send WhatsApp, update pipeline stage, or create reminder",
        "tier": "native",
        "public": True,
        "tags": ["sales", "follow-up", "pipeline"],
        "trigger_config": {"type": "event", "event_type": "follow_up_scheduled"},
        "definition": {
            "steps": [
                {
                    "id": "wait_delay",
                    "type": "wait",
                    "duration": "{{input.delay_hours}}h",
                    "output": "wait_done",
                },
                {
                    "id": "resolve_entity",
                    "type": "mcp_tool",
                    "tool": "get_entity",
                    "params": {"entity_id": "{{input.entity_id}}"},
                    "output": "entity",
                },
                {
                    "id": "execute_followup",
                    "type": "agent",
                    "agent": "luna",
                    "prompt": (
                        "Execute a follow-up action for {{entity.name}}.\n\n"
                        "Action: {{input.action}}\n"
                        "Message: {{input.message}}\n\n"
                        "If action is 'send_whatsapp', send the message via WhatsApp.\n"
                        "If action is 'update_stage', advance the pipeline stage.\n"
                        "If action is 'remind', create a reminder notification."
                    ),
                    "output": "followup_result",
                },
            ],
        },
    },
    {
        "name": "Cardiac Report Generator",
        "description": "HealthPets: fetch patient echo PDF from Gmail, extract content, generate DACVIM cardiac evaluation report, save as Google Doc in Drive",
        "tier": "native",
        "public": True,
        "tags": ["healthpets", "vet", "cardiac", "echocardiogram", "google-drive", "report"],
        "trigger_config": {"type": "manual"},
        "definition": {
            "steps": [
                {
                    "id": "find_patient_email",
                    "type": "mcp_tool",
                    "tool": "search_emails",
                    "params": {
                        "query": "{{input.email_query}}",
                        "max_results": 1,
                        "account_email": "{{input.account_email}}",
                    },
                    "output": "email_search",
                },
                {
                    "id": "read_patient_email",
                    "type": "mcp_tool",
                    "tool": "read_email",
                    "params": {
                        "message_id": "{{email_search.emails[0].id}}",
                        "account_email": "{{input.account_email}}",
                    },
                    "output": "email_content",
                },
                {
                    "id": "extract_echo_pdf",
                    "type": "mcp_tool",
                    "tool": "download_attachment",
                    "params": {
                        "message_id": "{{email_search.emails[0].id}}",
                        "attachment_id": "{{email_content.attachments[0].id}}",
                        "account_email": "{{input.account_email}}",
                    },
                    "output": "echo_pdf",
                },
                {
                    "id": "generate_cardiac_report",
                    "type": "agent",
                    "agent": "luna",
                    "prompt": "You are a board-certified veterinary cardiologist (DACVIM-Cardiology). Generate a complete cardiac evaluation report in DACVIM format.\n\nPatient: {{input.patient_name}}\nEmail body: {{email_content.body}}\nEcho PDF content: {{echo_pdf.content}}\n\nProduce a professional cardiac evaluation report in markdown with these sections:\n# Cardiac Evaluation Report\n## History (Signalment, Presenting Complaint)\n## Exam (Physical Exam narrative, Echocardiogram summary, Echo Measurements table)\n## Assessment (Problem List, Concurrent Conditions)\n## Plan (Medications with dosages, Anesthesia/Fluid/Steroid Risk, Follow-up Diagnostics)\n\nUse professional veterinary cardiology terminology. Include ACVIM stage (dogs) or HCM stage (cats). Only include sections where data is available.",
                    "output": "report",
                },
                {
                    "id": "save_to_drive",
                    "type": "mcp_tool",
                    "tool": "create_drive_file",
                    "params": {
                        "name": "Cardiac Report — {{input.patient_name}} — {{input.visit_date}}",
                        "content": "{{report.response}}",
                        "mime_type": "application/vnd.google-apps.document",
                        "folder_id": "{{input.drive_folder_id}}",
                        "account_email": "{{input.account_email}}",
                    },
                    "output": "drive_doc",
                },
            ],
            "input_schema": {
                "patient_name": {"type": "string", "description": "Patient name (e.g. Buster Sugisawa)"},
                "visit_date": {"type": "string", "description": "Visit date (e.g. 2026-04-19)"},
                "email_query": {"type": "string", "description": "Gmail search query to find the patient email (e.g. 'from:jumbomail.com subject:Buster has:attachment')"},
                "account_email": {"type": "string", "description": "Google account email (btcvetmobile@gmail.com)"},
                "drive_folder_id": {"type": "string", "description": "Google Drive folder ID where reports are saved. Leave empty for root."},
            },
        },
    },
    {
        "name": "Monthly Billing",
        "description": "1st of each month: aggregate completed visits per clinic, generate invoices, send them, schedule payment follow-ups",
        "tier": "native",
        "public": True,
        "tags": ["billing", "invoices", "monthly", "healthpets"],
        "trigger_config": {"type": "cron", "schedule": "0 6 1 * *", "timezone": "UTC"},
        "definition": {
            "steps": [
                {
                    "id": "aggregate_visits",
                    "type": "internal_api",
                    "method": "POST",
                    "path": "/api/v1/billing/aggregate",
                    "body": {"month": "{{input.month}}", "clinic_ids": "{{input.clinic_ids}}"},
                    "output": "visits",
                },
                {
                    "id": "generate_invoices",
                    "type": "internal_api",
                    "method": "POST",
                    "path": "/api/v1/billing/invoices",
                    "body": {"month": "{{input.month}}", "clinics": "{{visits.clinics}}"},
                    "output": "invoices",
                },
                {
                    "id": "send_invoices",
                    "type": "mcp_tool",
                    "tool": "send_email",
                    "params": {
                        "subject": "Invoice for {{input.month}}",
                        "body": "Please find your invoice attached for billing period {{input.month}}.",
                        "invoice_ids": "{{invoices.invoice_ids}}",
                    },
                    "output": "delivery",
                },
                {
                    "id": "schedule_payment_followups",
                    "type": "mcp_tool",
                    "tool": "schedule_followup",
                    "params": {
                        "entity_ids": "{{invoices.invoice_ids}}",
                        "delay_days": 7,
                        "action": "payment_reminder",
                    },
                    "output": "followups",
                },
            ],
        },
    },
    {
        "name": "Data Source Sync",
        "description": "Extract data from any connector, load to PostgreSQL Bronze and Silver layers, update sync metadata",
        "tier": "native",
        "public": True,
        "tags": ["data", "connectors", "etl", "sync"],
        "trigger_config": {"type": "manual"},
        "definition": {
            "steps": [
                {
                    "id": "extract_data",
                    "type": "mcp_tool",
                    "tool": "query_data_source",
                    "params": {
                        "connector_id": "{{input.connector_id}}",
                        "connector_type": "{{input.connector_type}}",
                        "mode": "{{input.sync_mode}}",
                        "table_name": "{{input.table_name}}",
                        "watermark_column": "{{input.watermark_column}}",
                        "last_watermark": "{{input.last_watermark}}",
                    },
                    "output": "extract_result",
                },
                {
                    "id": "load_bronze",
                    "type": "internal_api",
                    "method": "POST",
                    "path": "/api/v1/datasources/{{input.connector_id}}/load-bronze",
                    "body": {
                        "staging_path": "{{extract_result.staging_path}}",
                        "schema": "{{extract_result.schema}}",
                        "target_dataset": "{{input.target_dataset}}",
                    },
                    "output": "bronze_result",
                },
                {
                    "id": "transform_silver",
                    "type": "internal_api",
                    "method": "POST",
                    "path": "/api/v1/datasources/{{input.connector_id}}/transform-silver",
                    "body": {"bronze_table": "{{bronze_result.bronze_table}}"},
                    "output": "silver_result",
                },
                {
                    "id": "update_sync_metadata",
                    "type": "internal_api",
                    "method": "PATCH",
                    "path": "/api/v1/datasources/{{input.connector_id}}/sync-status",
                    "body": {
                        "last_sync_status": "success",
                        "rows_synced": "{{extract_result.row_count}}",
                        "bronze_table": "{{bronze_result.bronze_table}}",
                        "silver_table": "{{silver_result.silver_table}}",
                        "new_watermark": "{{extract_result.new_watermark}}",
                    },
                    "output": "sync_metadata",
                },
            ],
        },
    },
    {
        "name": "Embedding Backfill",
        "description": "One-shot backfill of vector embeddings for knowledge entities, memories, and observations",
        "tier": "native",
        "public": True,
        "tags": ["embeddings", "knowledge", "backfill", "maintenance"],
        "trigger_config": {"type": "manual"},
        "definition": {
            "steps": [
                {
                    "id": "backfill_entities",
                    "type": "internal_api",
                    "method": "POST",
                    "path": "/api/v1/knowledge/embeddings/backfill",
                    "body": {"target": "entities", "tenant_id": "{{input.tenant_id}}"},
                    "output": "entity_results",
                },
                {
                    "id": "backfill_memories",
                    "type": "internal_api",
                    "method": "POST",
                    "path": "/api/v1/knowledge/embeddings/backfill",
                    "body": {"target": "memories", "tenant_id": "{{input.tenant_id}}"},
                    "output": "memory_results",
                },
                {
                    "id": "backfill_observations",
                    "type": "internal_api",
                    "method": "POST",
                    "path": "/api/v1/knowledge/embeddings/backfill",
                    "body": {"target": "observations", "tenant_id": "{{input.tenant_id}}"},
                    "output": "observation_results",
                },
            ],
        },
    },
    # ── Tier 2 branching workflow migrations ──────────────────────────
    {
        "name": "Deal Pipeline",
        "description": "M&A deal pipeline: discover prospects, score for sell-likelihood, research high-scorers, generate outreach, advance pipeline, sync to knowledge graph",
        "tier": "native",
        "public": True,
        "tags": ["deals", "sales", "pipeline", "m&a"],
        "trigger_config": {"type": "manual"},
        "definition": {
            "steps": [
                {
                    "id": "discover",
                    "type": "mcp_tool",
                    "tool": "find_entities",
                    "params": {
                        "category": "prospect",
                        "query": "{{input.industry}}",
                        "criteria": "{{input.criteria}}",
                    },
                    "output": "prospects",
                },
                {
                    "id": "score",
                    "type": "mcp_tool",
                    "tool": "qualify_lead",
                    "params": {
                        "entity_ids": "{{prospects.entity_ids}}",
                        "rubric": "sell_likelihood",
                    },
                    "output": "score_results",
                },
                {
                    "id": "filter_hot",
                    "type": "condition",
                    "if": "{{score_results.high_scorers | length}} > 0",
                    "then": "research",
                    "else": "skip",
                },
                {
                    "id": "research",
                    "type": "agent",
                    "agent": "luna",
                    "prompt": (
                        "Generate detailed research briefs for these high-scoring prospects:\n"
                        "{{score_results.high_scorers}}\n\n"
                        "For each prospect include: company overview, financials, "
                        "recent news, key decision-makers, and sell-likelihood rationale."
                    ),
                    "output": "research_briefs",
                },
                {
                    "id": "outreach",
                    "type": "mcp_tool",
                    "tool": "draft_outreach",
                    "params": {
                        "entity_ids": "{{score_results.high_scorer_ids}}",
                        "outreach_type": "{{input.outreach_type | default('cold_email')}}",
                        "context": "{{research_briefs}}",
                    },
                    "output": "outreach_drafts",
                },
                {
                    "id": "advance",
                    "type": "mcp_tool",
                    "tool": "update_pipeline_stage",
                    "params": {
                        "entity_ids": "{{score_results.high_scorer_ids}}",
                        "stage": "contacted",
                    },
                    "output": "advance_result",
                },
                {
                    "id": "sync_kg",
                    "type": "mcp_tool",
                    "tool": "record_observation",
                    "params": {
                        "entity_ids": "{{prospects.entity_ids}}",
                        "observation": "Deal pipeline run completed. {{score_results.high_scorers | length}} prospects above threshold, outreach generated.",
                    },
                    "output": "sync_result",
                },
            ],
        },
    },
    {
        "name": "Prospecting Pipeline",
        "description": "Outbound lead generation: enrich prospects, score, qualify by threshold, draft personalised outreach for each qualified lead, notify results",
        "tier": "native",
        "public": True,
        "tags": ["prospecting", "leads", "outbound", "sales"],
        "trigger_config": {"type": "manual"},
        "definition": {
            "steps": [
                {
                    "id": "research",
                    "type": "mcp_tool",
                    "tool": "find_entities",
                    "params": {
                        "entity_ids": "{{input.entity_ids}}",
                        "enrich": True,
                    },
                    "output": "enriched_prospects",
                },
                {
                    "id": "score",
                    "type": "mcp_tool",
                    "tool": "qualify_lead",
                    "params": {
                        "entity_ids": "{{input.entity_ids}}",
                        "rubric_id": "{{input.rubric_id}}",
                    },
                    "output": "score_results",
                },
                {
                    "id": "qualify",
                    "type": "condition",
                    "if": "{{score_results.qualified_ids | length}} > 0",
                    "then": "outreach_loop",
                    "else": "notify",
                },
                {
                    "id": "outreach_loop",
                    "type": "for_each",
                    "collection": "{{score_results.qualified_ids}}",
                    "as": "prospect_id",
                    "steps": [
                        {
                            "id": "draft_outreach",
                            "type": "mcp_tool",
                            "tool": "draft_outreach",
                            "params": {
                                "entity_id": "{{prospect_id}}",
                                "template": "{{input.template | default('default')}}",
                            },
                            "output": "outreach_draft",
                        },
                    ],
                },
                {
                    "id": "notify",
                    "type": "agent",
                    "agent": "luna",
                    "prompt": (
                        "Prospecting pipeline complete.\n\n"
                        "Total entities: {{input.entity_ids | length}}\n"
                        "Qualified: {{score_results.qualified_ids | length}}\n"
                        "Outreach drafts generated for qualified leads.\n\n"
                        "Summarize the results and highlight the top prospects."
                    ),
                    "output": "notification",
                },
            ],
        },
    },
    {
        "name": "Remedia Order",
        "description": "E-commerce order lifecycle: create order, send WhatsApp confirmation, await payment approval, confirm payment, track delivery",
        "tier": "native",
        "public": True,
        "tags": ["ecommerce", "orders", "whatsapp", "remedia"],
        "trigger_config": {"type": "manual"},
        "definition": {
            "steps": [
                {
                    "id": "create_order",
                    "type": "mcp_tool",
                    "tool": "call_mcp_tool",
                    "params": {
                        "server": "remedia",
                        "tool": "create_order",
                        "pharmacy_id": "{{input.pharmacy_id}}",
                        "items": "{{input.items}}",
                        "payment_provider": "{{input.payment_provider | default('mercadopago')}}",
                    },
                    "output": "order",
                },
                {
                    "id": "send_confirmation",
                    "type": "agent",
                    "agent": "luna",
                    "prompt": (
                        "Send order confirmation to {{input.phone_number}} via WhatsApp.\n\n"
                        "Order ID: {{order.order_id}}\n"
                        "Total: ${{order.total}}\n"
                        "Payment link: {{order.payment_url}}\n\n"
                        "Send a friendly confirmation message with the payment link."
                    ),
                    "output": "confirmation_sent",
                },
                {
                    "id": "await_payment",
                    "type": "human_approval",
                    "prompt": "Waiting for payment on order {{order.order_id}} (${{order.total}}). Payment will be confirmed automatically or can be manually approved.",
                    "timeout_minutes": 30,
                },
                {
                    "id": "check_payment",
                    "type": "condition",
                    "if": "{{await_payment.approved}} == true",
                    "then": "notify_payment",
                    "else": "notify_timeout",
                },
                {
                    "id": "notify_payment",
                    "type": "agent",
                    "agent": "luna",
                    "prompt": (
                        "Payment confirmed for order {{order.order_id}}!\n"
                        "Notify {{input.phone_number}} via WhatsApp that payment of "
                        "${{order.total}} was received and the order is being prepared."
                    ),
                    "output": "payment_notification",
                },
                {
                    "id": "notify_timeout",
                    "type": "agent",
                    "agent": "luna",
                    "prompt": (
                        "Payment not received for order {{order.order_id}} within 30 minutes.\n"
                        "Send a gentle reminder to {{input.phone_number}} via WhatsApp "
                        "with the payment link: {{order.payment_url}}"
                    ),
                    "output": "timeout_notification",
                },
                {
                    "id": "track_delivery",
                    "type": "mcp_tool",
                    "tool": "call_mcp_tool",
                    "params": {
                        "server": "remedia",
                        "tool": "track_delivery",
                        "order_id": "{{order.order_id}}",
                    },
                    "output": "delivery_status",
                },
            ],
        },
    },
    {
        "name": "Auto Action Router",
        "description": "Intent-based action router: classify action type and branch to the appropriate handler (email, WhatsApp, research, analysis, or task creation)",
        "tier": "native",
        "public": True,
        "tags": ["automation", "routing", "actions", "memory"],
        "trigger_config": {"type": "event", "event_type": "action_triggered"},
        "definition": {
            "steps": [
                {
                    "id": "classify",
                    "type": "agent",
                    "agent": "luna",
                    "prompt": (
                        "Classify this action request and extract the intent.\n\n"
                        "Action type: {{input.action_type}}\n"
                        "Entity: {{input.entity_id}}\n"
                        "Context: {{input.context}}\n\n"
                        "Return the best action category: reply_email, send_whatsapp, "
                        "research, analyze, or create_task."
                    ),
                    "output": "classification",
                },
                {
                    "id": "route_email",
                    "type": "condition",
                    "if": "{{input.action_type}} == 'reply_email'",
                    "then": "handle_email",
                    "else": "route_whatsapp",
                },
                {
                    "id": "handle_email",
                    "type": "mcp_tool",
                    "tool": "send_email",
                    "params": {
                        "entity_id": "{{input.entity_id}}",
                        "context": "{{input.context}}",
                        "draft": True,
                    },
                    "output": "email_result",
                },
                {
                    "id": "route_whatsapp",
                    "type": "condition",
                    "if": "{{input.action_type}} == 'send_whatsapp'",
                    "then": "handle_whatsapp",
                    "else": "route_research",
                },
                {
                    "id": "handle_whatsapp",
                    "type": "agent",
                    "agent": "luna",
                    "prompt": (
                        "Send a WhatsApp message regarding entity {{input.entity_id}}.\n\n"
                        "Context: {{input.context}}\n\n"
                        "Draft and send an appropriate message."
                    ),
                    "output": "whatsapp_result",
                },
                {
                    "id": "route_research",
                    "type": "condition",
                    "if": "{{input.action_type}} == 'research'",
                    "then": "handle_research",
                    "else": "route_analyze",
                },
                {
                    "id": "handle_research",
                    "type": "agent",
                    "agent": "luna",
                    "prompt": (
                        "Research entity {{input.entity_id}}.\n\n"
                        "Context: {{input.context}}\n\n"
                        "Conduct thorough research and store findings in the knowledge graph."
                    ),
                    "output": "research_result",
                },
                {
                    "id": "route_analyze",
                    "type": "condition",
                    "if": "{{input.action_type}} == 'analyze'",
                    "then": "handle_analyze",
                    "else": "handle_task",
                },
                {
                    "id": "handle_analyze",
                    "type": "agent",
                    "agent": "luna",
                    "prompt": (
                        "Analyze entity {{input.entity_id}}.\n\n"
                        "Context: {{input.context}}\n\n"
                        "Provide a detailed analysis and record insights."
                    ),
                    "output": "analysis_result",
                },
                {
                    "id": "handle_task",
                    "type": "mcp_tool",
                    "tool": "create_jira_issue",
                    "params": {
                        "summary": "Auto-action: {{input.context}}",
                        "description": "Automated task for entity {{input.entity_id}}.\n\nContext: {{input.context}}",
                        "issue_type": "Task",
                    },
                    "output": "task_result",
                },
            ],
        },
    },
    # ── Tier 3 continue_as_new workflow migrations ─────────────────────
    {
        "name": "Competitor Monitor",
        "description": "Long-running monitor: list competitor entities, scrape each for website/news/ad changes, analyze vs previous observations, store observations and notify on notable changes. Restarts every 24h.",
        "tier": "native",
        "public": True,
        "tags": ["competitors", "monitoring", "marketing", "long-running"],
        "trigger_config": {"type": "manual"},
        "definition": {
            "steps": [
                {
                    "id": "list_competitors",
                    "type": "mcp_tool",
                    "tool": "list_competitors",
                    "params": {},
                    "output": "competitors",
                },
                {
                    "id": "process_competitors",
                    "type": "for_each",
                    "collection": "{{competitors}}",
                    "as": "competitor",
                    "steps": [
                        {
                            "id": "scrape",
                            "type": "mcp_tool",
                            "tool": "call_mcp_tool",
                            "params": {
                                "server": "scraper",
                                "tool": "scrape_website",
                                "url": "{{competitor.website}}",
                            },
                            "output": "scrape_result",
                        },
                        {
                            "id": "analyze",
                            "type": "agent",
                            "agent": "luna",
                            "prompt": (
                                "Analyze competitor activity for {{competitor.name}}.\n\n"
                                "Scraped data:\n{{scrape_result}}\n\n"
                                "Previous summary:\n{{input.last_run_summary}}\n\n"
                                "Identify notable changes in products, pricing, marketing, "
                                "or public ad activity. Be concise."
                            ),
                            "output": "analysis",
                        },
                    ],
                },
                {
                    "id": "store_observations",
                    "type": "mcp_tool",
                    "tool": "record_observation",
                    "params": {
                        "entity_ids": "{{competitors | map(attribute='id') | list}}",
                        "observation": "Competitor monitor cycle complete. {{process_competitors | length}} competitors analyzed.",
                    },
                    "output": "observations_stored",
                },
                {
                    "id": "notify",
                    "type": "agent",
                    "agent": "luna",
                    "prompt": (
                        "Summarize the competitor monitoring cycle results.\n\n"
                        "Competitors analyzed: {{competitors | length}}\n"
                        "Create a brief notification only if notable changes were detected."
                    ),
                    "output": "notification",
                },
                {
                    "id": "restart",
                    "type": "continue_as_new",
                    "interval_seconds": 86400,
                },
            ],
        },
    },
    {
        "name": "Aremko Availability Monitor",
        "description": "Long-running monitor for Aremko Spa: fetch availability snapshot across all services, compare with previous snapshot, detect meaningful changes (bookings filling up, new slots), create notifications. Restarts every 60min.",
        "tier": "native",
        "public": True,
        "tags": ["aremko", "bookings", "monitoring", "long-running"],
        "trigger_config": {"type": "manual"},
        "definition": {
            "steps": [
                {
                    "id": "fetch_snapshot",
                    "type": "internal_api",
                    "method": "POST",
                    "path": "/api/v1/aremko/snapshot",
                    "body": {
                        "tenant_id": "{{input.tenant_id}}",
                        "days_ahead": "{{input.days_ahead | default(3)}}",
                    },
                    "output": "new_snapshot",
                },
                {
                    "id": "detect_changes",
                    "type": "condition",
                    "if": "{{input.previous_snapshot}} is not none",
                    "then": "compare_snapshots",
                    "else": "restart",
                },
                {
                    "id": "compare_snapshots",
                    "type": "agent",
                    "agent": "luna",
                    "prompt": (
                        "Compare Aremko Spa availability snapshots.\n\n"
                        "Previous:\n{{input.previous_snapshot}}\n\n"
                        "Current:\n{{new_snapshot}}\n\n"
                        "Detect meaningful changes: services filling up, new slots opening, "
                        "fully booked services. Return a list of changes."
                    ),
                    "output": "changes",
                },
                {
                    "id": "notify_changes",
                    "type": "agent",
                    "agent": "luna",
                    "prompt": (
                        "Create notifications for Aremko availability changes.\n\n"
                        "Changes detected:\n{{changes}}\n\n"
                        "Only notify for significant changes (fully booked, new openings)."
                    ),
                    "output": "notification",
                },
                {
                    "id": "restart",
                    "type": "continue_as_new",
                    "interval_seconds": 3600,
                },
            ],
        },
    },
    {
        "name": "Inbox Monitor",
        "description": "Long-running Gmail + Calendar monitor: fetch new emails and upcoming events, triage with LLM + memory context, create notifications, extract knowledge entities from important emails, check proactive triggers. Restarts every 15min.",
        "tier": "native",
        "public": True,
        "tags": ["inbox", "gmail", "calendar", "monitoring", "long-running"],
        "trigger_config": {"type": "manual"},
        "definition": {
            "steps": [
                {
                    "id": "fetch_emails",
                    "type": "mcp_tool",
                    "tool": "search_emails",
                    "params": {
                        "query": "is:unread newer_than:1h",
                        "max_results": 50,
                    },
                    "output": "emails",
                },
                {
                    "id": "fetch_events",
                    "type": "mcp_tool",
                    "tool": "list_calendar_events",
                    "params": {"days_ahead": 1},
                    "output": "events",
                },
                {
                    "id": "triage",
                    "type": "agent",
                    "agent": "luna",
                    "prompt": (
                        "Triage these inbox items with memory context.\n\n"
                        "Emails:\n{{emails}}\n\n"
                        "Events:\n{{events}}\n\n"
                        "For each item: classify priority (high/medium/low), "
                        "suggest action, and flag items needing immediate attention."
                    ),
                    "output": "triaged_items",
                },
                {
                    "id": "create_notifications",
                    "type": "internal_api",
                    "method": "POST",
                    "path": "/api/v1/notifications/batch",
                    "body": {
                        "tenant_id": "{{input.tenant_id}}",
                        "items": "{{triaged_items}}",
                    },
                    "output": "notifications",
                },
                {
                    "id": "extract_knowledge",
                    "type": "agent",
                    "agent": "luna",
                    "prompt": (
                        "Extract knowledge entities from important emails.\n\n"
                        "Emails:\n{{emails}}\n"
                        "Triage results:\n{{triaged_items}}\n\n"
                        "For high-priority emails: extract people, companies, dates, "
                        "action items, and relationships. Store them in the knowledge graph."
                    ),
                    "output": "extraction_result",
                },
                {
                    "id": "restart",
                    "type": "continue_as_new",
                    "interval_seconds": 900,
                },
            ],
        },
    },
    {
        "name": "Channel Health Monitor",
        "description": "Long-running WhatsApp channel health monitor: check all channel connections, reconnect any disconnected accounts, update health status in DB. Restarts every 60s.",
        "tier": "native",
        "public": True,
        "tags": ["whatsapp", "channels", "health", "monitoring", "long-running"],
        "trigger_config": {"type": "manual"},
        "definition": {
            "steps": [
                {
                    "id": "check_channels",
                    "type": "internal_api",
                    "method": "GET",
                    "path": "/api/v1/channels/health",
                    "body": {"tenant_id": "{{input.tenant_id}}"},
                    "output": "status_report",
                },
                {
                    "id": "reconnect_loop",
                    "type": "for_each",
                    "collection": "{{status_report.disconnected}}",
                    "as": "account_id",
                    "steps": [
                        {
                            "id": "reconnect",
                            "type": "internal_api",
                            "method": "POST",
                            "path": "/api/v1/channels/{{account_id}}/reconnect",
                            "body": {"tenant_id": "{{input.tenant_id}}"},
                            "output": "reconnect_result",
                        },
                    ],
                },
                {
                    "id": "update_status",
                    "type": "internal_api",
                    "method": "PATCH",
                    "path": "/api/v1/channels/health",
                    "body": {
                        "tenant_id": "{{input.tenant_id}}",
                        "status_report": "{{status_report}}",
                    },
                    "output": "status_updated",
                },
                {
                    "id": "restart",
                    "type": "continue_as_new",
                    "interval_seconds": 60,
                },
            ],
        },
    },
    {
        "name": "Goal Review",
        "description": "Long-running goal and commitment reviewer: review active goals for stalled/blocked/contradictory states, check overdue commitments, create notifications for flagged items. Restarts every 6h.",
        "tier": "native",
        "public": True,
        "tags": ["goals", "commitments", "review", "monitoring", "long-running"],
        "trigger_config": {"type": "manual"},
        "definition": {
            "steps": [
                {
                    "id": "review_goals",
                    "type": "internal_api",
                    "method": "POST",
                    "path": "/api/v1/goals/review",
                    "body": {"tenant_id": "{{input.tenant_id}}"},
                    "output": "review_result",
                },
                {
                    "id": "review_commitments",
                    "type": "internal_api",
                    "method": "POST",
                    "path": "/api/v1/commitments/review",
                    "body": {"tenant_id": "{{input.tenant_id}}"},
                    "output": "overdue_result",
                },
                {
                    "id": "create_notifications",
                    "type": "internal_api",
                    "method": "POST",
                    "path": "/api/v1/notifications/batch",
                    "body": {
                        "tenant_id": "{{input.tenant_id}}",
                        "review": "{{review_result}}",
                        "overdue": "{{overdue_result}}",
                    },
                    "output": "notifications",
                },
                {
                    "id": "restart",
                    "type": "continue_as_new",
                    "interval_seconds": 21600,
                },
            ],
        },
    },
    {
        "name": "Memory Consolidation",
        "description": "Long-running memory maintenance: find duplicate entities, auto-merge duplicates, apply memory decay, promote entity lifecycle stages, sync memories with entities, log results. Restarts every 24h.",
        "tier": "native",
        "public": True,
        "tags": ["memory", "knowledge", "consolidation", "maintenance", "long-running"],
        "trigger_config": {"type": "manual"},
        "definition": {
            "steps": [
                {
                    "id": "find_duplicates",
                    "type": "internal_api",
                    "method": "POST",
                    "path": "/api/v1/knowledge/duplicates/find",
                    "body": {"tenant_id": "{{input.tenant_id}}"},
                    "output": "duplicate_clusters",
                },
                {
                    "id": "merge_duplicates",
                    "type": "condition",
                    "if": "{{duplicate_clusters.clusters | length}} > 0",
                    "then": "auto_merge",
                    "else": "apply_decay",
                },
                {
                    "id": "auto_merge",
                    "type": "internal_api",
                    "method": "POST",
                    "path": "/api/v1/knowledge/duplicates/merge",
                    "body": {
                        "tenant_id": "{{input.tenant_id}}",
                        "clusters": "{{duplicate_clusters.clusters}}",
                    },
                    "output": "merge_result",
                },
                {
                    "id": "apply_decay",
                    "type": "internal_api",
                    "method": "POST",
                    "path": "/api/v1/knowledge/memory-decay",
                    "body": {"tenant_id": "{{input.tenant_id}}"},
                    "output": "decay_result",
                },
                {
                    "id": "promote_entities",
                    "type": "internal_api",
                    "method": "POST",
                    "path": "/api/v1/knowledge/promote",
                    "body": {"tenant_id": "{{input.tenant_id}}"},
                    "output": "promote_result",
                },
                {
                    "id": "sync_memories",
                    "type": "internal_api",
                    "method": "POST",
                    "path": "/api/v1/knowledge/sync-memories",
                    "body": {"tenant_id": "{{input.tenant_id}}"},
                    "output": "sync_result",
                },
                {
                    "id": "log_results",
                    "type": "internal_api",
                    "method": "POST",
                    "path": "/api/v1/knowledge/consolidation/log",
                    "body": {
                        "tenant_id": "{{input.tenant_id}}",
                        "duplicates": "{{duplicate_clusters}}",
                        "merge": "{{merge_result}}",
                        "decay": "{{decay_result}}",
                        "promotions": "{{promote_result}}",
                        "sync": "{{sync_result}}",
                    },
                    "output": "log_result",
                },
                {
                    "id": "restart",
                    "type": "continue_as_new",
                    "interval_seconds": 86400,
                },
            ],
        },
    },
    {
        "name": "Autonomous Learning",
        "description": "Nightly self-improvement cycle: collect learning metrics, generate/evaluate improvement candidates, manage rollouts, run self-simulation, process feedback, auto-dream RL consolidation, prune stale knowledge, learn user preferences, generate morning report. Restarts every 24h.",
        "tier": "native",
        "public": True,
        "tags": ["learning", "rl", "self-improvement", "simulation", "long-running"],
        "trigger_config": {"type": "manual"},
        "definition": {
            "steps": [
                {
                    "id": "collect_metrics",
                    "type": "internal_api",
                    "method": "GET",
                    "path": "/api/v1/rl/metrics",
                    "body": {"tenant_id": "{{input.tenant_id}}"},
                    "output": "metrics",
                },
                {
                    "id": "generate_candidates",
                    "type": "internal_api",
                    "method": "POST",
                    "path": "/api/v1/rl/candidates/generate",
                    "body": {
                        "tenant_id": "{{input.tenant_id}}",
                        "metrics": "{{metrics}}",
                    },
                    "output": "candidates",
                },
                {
                    "id": "manage_rollouts",
                    "type": "internal_api",
                    "method": "POST",
                    "path": "/api/v1/rl/rollouts/manage",
                    "body": {"tenant_id": "{{input.tenant_id}}"},
                    "output": "rollout_result",
                },
                {
                    "id": "self_simulation",
                    "type": "agent",
                    "agent": "luna",
                    "prompt": (
                        "Run self-simulation cycle.\n\n"
                        "Metrics:\n{{metrics}}\n"
                        "Candidates:\n{{candidates}}\n\n"
                        "Select personas, generate scenarios, execute simulations, "
                        "classify failures, and detect skill gaps."
                    ),
                    "output": "simulation_result",
                },
                {
                    "id": "process_feedback",
                    "type": "internal_api",
                    "method": "POST",
                    "path": "/api/v1/rl/feedback/process",
                    "body": {"tenant_id": "{{input.tenant_id}}"},
                    "output": "feedback_result",
                },
                {
                    "id": "auto_dream",
                    "type": "internal_api",
                    "method": "POST",
                    "path": "/api/v1/rl/dream/consolidate",
                    "body": {"tenant_id": "{{input.tenant_id}}"},
                    "output": "dream_result",
                },
                {
                    "id": "prune_knowledge",
                    "type": "internal_api",
                    "method": "POST",
                    "path": "/api/v1/knowledge/prune",
                    "body": {"tenant_id": "{{input.tenant_id}}"},
                    "output": "prune_result",
                },
                {
                    "id": "learn_preferences",
                    "type": "internal_api",
                    "method": "POST",
                    "path": "/api/v1/rl/preferences/learn",
                    "body": {"tenant_id": "{{input.tenant_id}}"},
                    "output": "preference_result",
                },
                {
                    "id": "morning_report",
                    "type": "agent",
                    "agent": "luna",
                    "prompt": (
                        "Generate the morning self-improvement report.\n\n"
                        "Metrics: {{metrics}}\n"
                        "Candidates: {{candidates.generated}} generated, {{candidates.evaluated}} evaluated\n"
                        "Rollouts: {{rollout_result.managed}} managed\n"
                        "Simulation: {{simulation_result}}\n"
                        "Feedback: {{feedback_result}}\n"
                        "Dream consolidation: {{dream_result}}\n"
                        "Knowledge pruned: {{prune_result}}\n"
                        "Preferences: {{preference_result}}\n\n"
                        "Summarize key improvements, regressions, and recommendations."
                    ),
                    "output": "report",
                },
                {
                    "id": "restart",
                    "type": "continue_as_new",
                    "interval_seconds": 86400,
                },
            ],
        },
    },
    # ── Tier 4 infrastructure workflow migrations ─────────────────────
    {
        "name": "Task Execution",
        "description": "Full agent task orchestration: dispatch to best agent, recall relevant memories, execute task via CLI orchestrator, persist extracted entities to knowledge graph, evaluate results and log RL score",
        "tier": "native",
        "public": True,
        "tags": ["orchestration", "tasks", "agents", "rl"],
        "trigger_config": {"type": "event", "event_type": "task_created"},
        "definition": {
            "steps": [
                {
                    "id": "dispatch_task",
                    "type": "internal_api",
                    "method": "POST",
                    "path": "/api/v1/tasks/dispatch",
                    "body": {
                        "task_id": "{{input.task_id}}",
                        "tenant_id": "{{input.tenant_id}}",
                        "task_data": "{{input.task_data}}",
                    },
                    "output": "dispatch_result",
                },
                {
                    "id": "recall_memory",
                    "type": "internal_api",
                    "method": "GET",
                    "path": "/api/v1/knowledge/recall",
                    "body": {
                        "tenant_id": "{{input.tenant_id}}",
                        "agent_id": "{{dispatch_result.agent_id}}",
                        "query": "{{input.task_data.objective}}",
                    },
                    "output": "memory",
                },
                {
                    "id": "execute_task",
                    "type": "agent",
                    "agent": "{{dispatch_result.agent_id}}",
                    "prompt": (
                        "Execute this task.\n\n"
                        "Objective: {{input.task_data.objective}}\n"
                        "Context: {{input.task_data}}\n\n"
                        "Relevant memories:\n{{memory.memories}}\n\n"
                        "Complete the task and return your output."
                    ),
                    "output": "execute_result",
                },
                {
                    "id": "persist_entities",
                    "type": "internal_api",
                    "method": "POST",
                    "path": "/api/v1/knowledge/extract-and-store",
                    "body": {
                        "tenant_id": "{{input.tenant_id}}",
                        "agent_id": "{{dispatch_result.agent_id}}",
                        "content": "{{execute_result}}",
                    },
                    "output": "entities_persisted",
                },
                {
                    "id": "evaluate_and_log_rl",
                    "type": "internal_api",
                    "method": "POST",
                    "path": "/api/v1/rl/evaluate",
                    "body": {
                        "task_id": "{{input.task_id}}",
                        "tenant_id": "{{input.tenant_id}}",
                        "agent_id": "{{dispatch_result.agent_id}}",
                        "output": "{{execute_result}}",
                    },
                    "output": "evaluation",
                },
            ],
        },
    },
    {
        "name": "Knowledge Extraction",
        "description": "Extract knowledge from a chat session: fetch session content, extract entities/relations/observations via agent, store in knowledge graph, embed for vector search",
        "tier": "native",
        "public": True,
        "tags": ["knowledge", "extraction", "entities", "embeddings"],
        "trigger_config": {"type": "event", "event_type": "session_completed"},
        "definition": {
            "steps": [
                {
                    "id": "fetch_session",
                    "type": "internal_api",
                    "method": "GET",
                    "path": "/api/v1/chat/sessions/{{input.session_id}}/messages",
                    "body": {"tenant_id": "{{input.tenant_id}}"},
                    "output": "session_content",
                },
                {
                    "id": "extract_entities",
                    "type": "agent",
                    "agent": "luna",
                    "prompt": (
                        "Extract knowledge entities from this chat session.\n\n"
                        "Session content:\n{{session_content}}\n\n"
                        "Extract: people, companies, projects, dates, decisions, action items, "
                        "and relationships between entities. Return structured JSON."
                    ),
                    "output": "extracted",
                },
                {
                    "id": "store_entities",
                    "type": "internal_api",
                    "method": "POST",
                    "path": "/api/v1/knowledge/entities/batch",
                    "body": {
                        "tenant_id": "{{input.tenant_id}}",
                        "entities": "{{extracted.entities}}",
                        "relations": "{{extracted.relations}}",
                        "observations": "{{extracted.observations}}",
                    },
                    "output": "stored",
                },
                {
                    "id": "embed_entities",
                    "type": "internal_api",
                    "method": "POST",
                    "path": "/api/v1/knowledge/embeddings/backfill",
                    "body": {
                        "tenant_id": "{{input.tenant_id}}",
                        "target": "entities",
                        "entity_ids": "{{stored.entity_ids}}",
                    },
                    "output": "embedded",
                },
            ],
        },
    },
    {
        "name": "Code Task",
        "description": "Autonomous coding via Claude Code CLI: execute a code task description in an isolated worker, producing a branch with commits and a PR",
        "tier": "native",
        "public": True,
        "tags": ["code", "cli", "github", "autonomous"],
        "trigger_config": {"type": "manual"},
        "definition": {
            "steps": [
                {
                    "id": "run_code",
                    "type": "cli_execute",
                    "task": "{{input.task_description}}",
                    "context": "{{input.context}}",
                    "output": "code_result",
                },
            ],
        },
    },
    {
        "name": "RL Policy Update",
        "description": "Nightly RL batch job: gather experiences per decision point, update tenant policy for each active decision point, anonymize and aggregate into global baseline, archive old experiences",
        "tier": "native",
        "public": True,
        "tags": ["rl", "policy", "learning", "batch"],
        "trigger_config": {"type": "cron", "schedule": "0 3 * * *", "timezone": "UTC"},
        "definition": {
            "steps": [
                {
                    "id": "gather_experiences",
                    "type": "internal_api",
                    "method": "GET",
                    "path": "/api/v1/rl/experiences/stats",
                    "body": {"tenant_id": "{{input.tenant_id}}"},
                    "output": "experience_stats",
                },
                {
                    "id": "update_policies",
                    "type": "for_each",
                    "collection": "{{experience_stats.decision_points}}",
                    "as": "decision_point",
                    "steps": [
                        {
                            "id": "update_policy",
                            "type": "internal_api",
                            "method": "POST",
                            "path": "/api/v1/rl/policy/update",
                            "body": {
                                "tenant_id": "{{input.tenant_id}}",
                                "decision_point": "{{decision_point}}",
                            },
                            "output": "policy_update_result",
                        },
                    ],
                },
                {
                    "id": "aggregate_global",
                    "type": "internal_api",
                    "method": "POST",
                    "path": "/api/v1/rl/global/aggregate",
                    "body": {"decision_points": "{{experience_stats.decision_points}}"},
                    "output": "aggregation_result",
                },
                {
                    "id": "archive_old",
                    "type": "internal_api",
                    "method": "POST",
                    "path": "/api/v1/rl/experiences/archive",
                    "body": {
                        "tenant_id": "{{input.tenant_id}}",
                        "retention_days": 90,
                    },
                    "output": "archive_result",
                },
            ],
        },
    },
    # -----------------------------------------------------------------------
    # Gap 1: Daily Journal Synthesis — nightly at 23:55
    # -----------------------------------------------------------------------
    {
        "name": "Daily Journal Synthesis",
        "description": "Nightly: synthesize today's conversation episodes into a session journal entry for morning continuity context (Gap 1).",
        "tier": "native",
        "public": True,
        "tags": ["memory", "journal", "continuity", "nightly", "gap-1"],
        "trigger_config": {"type": "cron", "schedule": "55 23 * * *", "timezone": "UTC"},
        "definition": {
            "steps": [
                {
                    "id": "synthesize_daily",
                    "type": "mcp_tool",
                    "tool": "synthesize_daily_journal",
                    "params": {},
                    "output": "daily_result",
                },
                {
                    "id": "expire_signals",
                    "type": "mcp_tool",
                    "tool": "expire_behavioral_signals",
                    "params": {},
                    "output": "signal_result",
                },
            ],
        },
    },
    # -----------------------------------------------------------------------
    # Gap 1: Weekly Journal Summary — Sundays at 23:00
    # -----------------------------------------------------------------------
    {
        "name": "Weekly Journal Summary",
        "description": "Every Sunday: aggregate this week's daily journals into a weekly narrative for longer-range continuity context (Gap 1).",
        "tier": "native",
        "public": True,
        "tags": ["memory", "journal", "continuity", "weekly", "gap-1"],
        "trigger_config": {"type": "cron", "schedule": "0 23 * * 0", "timezone": "UTC"},
        "definition": {
            "steps": [
                {
                    "id": "synthesize_weekly",
                    "type": "mcp_tool",
                    "tool": "synthesize_weekly_journal",
                    "params": {},
                    "output": "weekly_result",
                },
            ],
        },
    },
]


def seed_native_templates(db, tenant_id=None):
    """Seed native workflow templates. Idempotent — skips existing."""
    import uuid
    from app.models.dynamic_workflow import DynamicWorkflow
    from app.models.tenant import Tenant

    # Resolve tenant: explicit > first in DB (native templates are public, tenant is just the owner)
    if tenant_id:
        owner_id = uuid.UUID(tenant_id) if isinstance(tenant_id, str) else tenant_id
    else:
        first_tenant = db.query(Tenant.id).first()
        if not first_tenant:
            return 0  # No tenants yet — skip seeding
        owner_id = first_tenant.id

    created = 0
    for tmpl in NATIVE_TEMPLATES:
        existing = db.query(DynamicWorkflow).filter(
            DynamicWorkflow.name == tmpl["name"],
            DynamicWorkflow.tier == "native",
        ).first()
        if existing:
            continue

        wf = DynamicWorkflow(
            id=uuid.uuid4(),
            tenant_id=owner_id,
            name=tmpl["name"],
            description=tmpl["description"],
            definition=tmpl["definition"],
            trigger_config=tmpl["trigger_config"],
            tags=tmpl["tags"],
            tier="native",
            public=True,
            status="draft",
        )
        db.add(wf)
        created += 1

    db.commit()
    return created
