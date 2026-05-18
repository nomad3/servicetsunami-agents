"""Tool group registry for agent-scoped MCP tool loading.

Maps logical tool group keys to MCP tool names. Agents declare which groups
they need via agent.tool_groups. At runtime, only tools from declared groups
are passed to the CLI via --allowedTools.
"""

TOOL_GROUPS: dict[str, list[str]] = {
    "calendar": [
        "list_calendar_events",
        "create_calendar_event",
    ],
    "email": [
        "search_emails",
        "send_email",
        "read_email",
        "download_attachment",
    ],
    "ecommerce": [
        "query_sql",
        "generate_excel_report",
        "query_data_source",
    ],
    "knowledge": [
        "search_knowledge",
        "find_entities",
        "recall_memory",
        "record_observation",
        "create_entity",
        "create_relation",
        "find_relations",
        "get_neighborhood",
        "ask_knowledge_graph",
        "merge_entities",
        "update_entity",
        "get_entity_timeline",
    ],
    "sales": [
        "qualify_lead",
        "update_pipeline_stage",
        "draft_outreach",
        "get_pipeline_summary",
        "schedule_followup",
    ],
    # General web research — unblocks the leads-list / market-intelligence
    # use cases. ``discover_companies`` is the entry point most sales
    # agents will hit; ``web_search`` + ``fetch_url`` are the primitives
    # the underlying agent can compose for deeper research (e.g. read a
    # 10-K, summarise a press release).
    "web_research": [
        "web_search",
        "fetch_url",
        "discover_companies",
    ],
    "bookings": [
        "list_calendar_events",
        "create_calendar_event",
        "schedule_followup",
        "search_emails",
        "send_email",
    ],
    "data": [
        "query_sql",
        "query_data_source",
        "discover_datasets",
        "get_dataset_schema",
    ],
    "reports": [
        "generate_excel_report",
        "generate_insights",
        "forecast",
        "compare_periods",
    ],
    "github": [
        "list_github_repos",
        "list_github_issues",
        "list_github_pull_requests",
        "get_github_issue",
        "get_github_pull_request",
        "get_github_repo",
        "search_github_code",
        "read_github_file",
        "get_git_history",
        "get_pr_status",
    ],
    "jira": [
        "list_jira_projects",
        "search_jira_issues",
        "get_jira_issue",
        "create_jira_issue",
        "update_jira_issue",
    ],
    "competitor": [
        "list_competitors",
        "add_competitor",
        "remove_competitor",
        "get_competitor_report",
        "check_competitor_monitor_status",
        "start_competitor_monitor",
        "stop_competitor_monitor",
    ],
    # Higgsfield creative-content tools — Wave 1a of the CLI catalog
    # (#270). Per-tenant MCP source registered via
    # apps/api/app/services/higgsfield_mcp.py after the OAuth dance
    # completes. The list below is the static fallback used by the
    # Marketing/Sales specialist agent before live discovery has run;
    # discover_mcp_tools refreshes the real names against the tenant's
    # MCP server. Tool names mirror higgsfield_mcp.HIGGSFIELD_TOOL_NAMES.
    "higgsfield": [
        "higgsfield_soul",
        "higgsfield_cinema_studio",
        "higgsfield_flux",
        "higgsfield_seedream",
        "higgsfield_nano_banana",
        "higgsfield_seedance",
        "higgsfield_kling",
        "higgsfield_veo",
        "higgsfield_minimax_hailuo",
        "higgsfield_ad_engine",
        "higgsfield_virality_prediction",
    ],
    "ads": [
        "list_meta_campaigns",
        "list_google_campaigns",
        "list_tiktok_campaigns",
        "get_meta_campaign_insights",
        "get_google_campaign_metrics",
        "get_tiktok_campaign_insights",
        "pause_meta_campaign",
        "pause_google_campaign",
        "pause_tiktok_campaign",
        "compare_campaigns",
        "search_meta_ad_library",
        "search_google_ads_transparency",
        "search_tiktok_creative_center",
    ],
    "monitor": [
        "start_inbox_monitor",
        "stop_inbox_monitor",
        "check_inbox_monitor_status",
        "start_competitor_monitor",
        "stop_competitor_monitor",
        "check_competitor_monitor_status",
    ],
    "drive": [
        "search_drive_files",
        "read_drive_file",
        "create_drive_file",
        "list_drive_folders",
    ],
    "shell": [
        "execute_shell",
        "deploy_changes",
    ],
    "workflows": [
        "list_dynamic_workflows",
        "create_dynamic_workflow",
        "run_dynamic_workflow",
        "get_workflow_run_status",
        "activate_dynamic_workflow",
        "install_workflow_template",
    ],
    "skills": [
        "list_skills",
        "run_skill",
        "read_library_skill",
        "update_skill_definition",
        "update_agent_definition",
        "list_library_revisions",
        "get_skill_gaps",
    ],
    "a2a": [
        "delegate_to_agent",
        "read_handoff_status",
        "find_agent",
    ],
    "webhooks": [
        "register_webhook",
        "list_webhooks",
        "delete_webhook",
        "test_webhook",
        "send_webhook_event",
        "get_webhook_logs",
    ],
    "mcp_servers": [
        "list_mcp_servers",
        "connect_mcp_server",
        "disconnect_mcp_server",
        "health_check_mcp_server",
        "get_mcp_server_logs",
        "discover_mcp_tools",
        "call_mcp_tool",
    ],
    "learning": [
        "start_autonomous_learning",
        "stop_autonomous_learning",
        "check_autonomous_learning_status",
        "submit_learning_feedback",
        "get_simulation_summary",
    ],
    # Platform introspection — answers "what's on this tenant?" questions like
    # "list my agents", "what workflows do I have", "which MCP servers are
    # connected?". Without this group Luna falls back to "I couldn't access
    # the live MCP registry" because she has no tool to enumerate registry
    # state. All tool names below are real MCP tools registered in
    # apps/mcp-server/src/mcp_tools/* (verified 2026-05-10).
    "meta": [
        "find_agent",               # agent_messaging.py — wraps GET /agents/discover
        "list_dynamic_workflows",   # dynamic_workflows.py
        "list_skills",              # skills.py
        "read_library_skill",       # skills.py — inspect skill source
        "list_mcp_servers",         # mcp_servers.py
        "discover_mcp_tools",       # mcp_servers.py — enumerate tools on a server
    ],
    # Vet-vertical tool groups (Phase 4.5 — added to support the Animal Doctor
    # SOC agent fleet, see seed_animaldoctor_agent_fleet.py). Logical groupings
    # over the real MCP tools registered in apps/mcp-server/src/mcp_tools/.
    "pulse": [
        # Covetrus Pulse PIMS — patient/appointment/billing data of record.
        "pulse_get_patient",
        "pulse_list_appointments",
        "pulse_query_invoices",
    ],
    "scribblevet": [
        # ScribbleVet clinical-note pairing — SOAP synthesis from voice/chart.
        "scribblevet_list_recent_notes",
        "scribblevet_get_note",
        "scribblevet_search",
    ],
    "patient_records": [
        # Composite group: clinical reads. Pulls patient context across PIMS,
        # clinical notes, and the knowledge graph. Read-only — no mutations.
        "pulse_get_patient",
        "pulse_list_appointments",
        "scribblevet_get_note",
        "scribblevet_list_recent_notes",
        "search_knowledge",
        "find_entities",
        "recall_memory",
    ],
    "communication": [
        # Outbound: SMS + email + inter-agent messaging. Receptionist /
        # reminder paths use SMS for owner outreach; admin escalation uses
        # delegate_to_agent / find_agent.
        "send_sms",
        "list_sms_threads",
        "read_sms",
        "send_email",
        "search_emails",
        "delegate_to_agent",
        "find_agent",
    ],
    "bookkeeper_export": [
        # AAHA-coded ledger exports for the practice's accountant. Used by
        # the Billing Agent end-of-day routine.
        "bookkeeper_export_aaha",
    ],
}

TIER_MODEL_MAP: dict[str, dict[str, str]] = {
    "light": {
        "claude_code": "haiku",
        "codex": "codex-mini",
        "gemini_cli": "gemini-2.5-flash",
        "opencode": "gemma4",
    },
    "full": {
        "claude_code": "sonnet",
        "codex": "codex",
        "gemini_cli": "gemini-2.5-pro",
        "opencode": "gemma4",
    },
}

# Platform to use for light tier when no tenant-specific override exists
LIGHT_TIER_PLATFORM = "opencode"

TIER_LIMITS: dict[str, dict] = {
    "light": {
        "entities": 3,
        "observations_per_entity": 1,
        "include_relations": False,
        "include_episodes": False,
        "include_world_state": False,
        "include_goals": False,
        "include_commitments": False,
        "history_messages": 4,
    },
    "full": {
        "entities": 10,
        "observations_per_entity": 3,
        "include_relations": True,
        "include_episodes": True,
        "include_world_state": True,
        "include_goals": True,
        "include_commitments": True,
        "history_messages": 6,
    },
}


def resolve_tool_names(tool_groups: list[str] | None) -> list[str] | None:
    """Convert tool group keys to flat list of MCP tool names.

    Returns None if tool_groups is None (meaning load all tools).
    """
    if tool_groups is None:
        return None
    names = set()
    for group in tool_groups:
        if group in TOOL_GROUPS:
            names.update(TOOL_GROUPS[group])
    return sorted(names)


def format_allowed_tools(tool_names: list[str]) -> str:
    """Format tool names for --allowedTools CLI flag.

    Prefixes each tool with 'mcp__agentprovision__' for MCP tool matching.
    """
    return ",".join(f"mcp__agentprovision__{name}" for name in tool_names)
