"""Tool group registry for agent-scoped MCP tool loading.

Maps logical tool group keys to MCP tool names. Agents declare which groups
they need via agent.tool_groups. At runtime, only tools from declared groups
are passed to the CLI via --allowedTools.
"""

from app.services.higgsfield_mcp import HIGGSFIELD_TOOL_NAMES

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
        # NOTE: this group is the historical full set (read + write).
        # Backwards-compatible. Prefer "knowledge_readonly" for new
        # read-only agents (reviewers, sentinels) so claims like
        # "this agent is read-only" actually hold. See the split
        # introduced 2026-05-24 after Luna's step-4 self-application
        # finding (concern observation b0533a44).
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
    "knowledge_readonly": [
        # Read-only subset of "knowledge". Agents claiming a read-only
        # tool surface (Code Reviewer, Substrate Sentinel, audit
        # agents) should use this group instead of "knowledge" so
        # their advertised capability matches their actual capability.
        # If an agent later needs to write observations or mutate
        # entities, grant the full "knowledge" group — opt-in, not
        # default. There is intentionally no "write-only" knowledge
        # group; mutation requires read context.
        "search_knowledge",
        "find_entities",
        "recall_memory",
        "find_relations",
        "get_neighborhood",
        "ask_knowledge_graph",
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
    # completes. The list is the static fallback used by the
    # Marketing/Sales specialist agent before live discovery has run;
    # discover_mcp_tools refreshes the real names against the tenant's
    # MCP server. Sourced from `higgsfield_mcp.HIGGSFIELD_TOOL_NAMES`
    # so a tool rename only has to land in one place.
    "higgsfield": list(HIGGSFIELD_TOOL_NAMES),
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
        # Autonomous-learning subsystem (pre-existing). KEEP EXACTLY THESE
        # 5 tools — PR #728 review IMPORTANT4 found that merging the
        # Luna Learn primitives into this group is a silent privilege
        # escalation for every agent already granted `learning` (they'd
        # gain install_skill + diffuse_learning + 5 more without an
        # operator-visible change). The Luna Learn primitives now live
        # in the separate `luna_learn` group below.
        "start_autonomous_learning",
        "stop_autonomous_learning",
        "check_autonomous_learning_status",
        "submit_learning_feedback",
        "get_simulation_summary",
    ],
    "luna_learn": [
        # Luna Learn from Media (PR #726, gated PR #728 review) — video
        # → skill synthesis primitives. Split out of `learning` to keep
        # the install_skill / diffuse_learning grant explicit per-agent
        # (review IMPORTANT4).
        # Spec: docs/superpowers/specs/2026-05-25-luna-learn-from-media-design.md
        "extract_media",
        "transcribe_url",
        "synthesize_skill_draft",
        "dispatch_skill_review",
        "run_synthetic_test",
        "install_skill",
        "diffuse_learning",
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
        "qwen_code": "qwen-coder-turbo",
        "opencode": "gemma4",
    },
    "full": {
        "claude_code": "sonnet",
        "codex": "codex",
        "gemini_cli": "gemini-2.5-pro",
        "qwen_code": "qwen-coder",
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


def format_allowed_tools(
    tool_names: list[str], cli_platform: str | None = None
) -> str:
    """Format tool names for --allowedTools CLI flag.

    Prefixes each tool with the MCP namespace the target CLI actually
    registers tools under. Per `cli_session_manager.generate_cli_instructions`
    (lines 124-126, verified from production logs 2026-04-25):

      - Claude Code (and clones honoring Claude Code's MCP shape) register
        tools as `mcp__agentprovision__<tool>` (double underscore).
      - Gemini CLI registers tools as `mcp_agentprovision_<tool>`
        (single underscore).

    `cli_platform` accepts the values in
    `cli_session_manager.SUPPORTED_CLI_PLATFORMS`. When None or unknown,
    we emit BOTH shapes so the allow-list still matches whichever
    convention the runtime CLI uses — false-positive entries against the
    wrong CLI are harmless because the CLI silently ignores unknown tool
    names.

    Per-tenant external MCP connectors (e.g. Higgsfield) live behind their
    own `mcp__<server_key>__*` namespace once injected by
    `apps/api/app/services/cli_session_manager.py::generate_mcp_config`.
    They are NOT served by the agentprovision tool surface, so prefixing
    `higgsfield_*` tool names with `mcp__agentprovision__` would produce
    allow-list entries the CLI can never match — calls get silently
    filtered out. Detect the higgsfield-prefixed names and emit the
    connector-namespaced wildcard (`mcp__higgsfield__*` for double-
    underscore CLIs, `mcp_higgsfield_*` for Gemini's single-underscore
    shape) instead. The individual `higgsfield_*` names in
    `HIGGSFIELD_TOOL_NAMES` are static fallback hints used by docs /
    discovery refresh; the real tool names come from live
    `discover_mcp_tools` against the tenant's connector, so a wildcard is
    the only allow-list shape that survives a tool rename on Higgsfield's
    side without an api redeploy.
    """
    # CLI platforms that register MCP tools using Gemini's single-
    # underscore convention. Anything else falls back to the Claude Code
    # double-underscore shape — which is what Claude Code, Codex, Copilot,
    # qwen-code, and opencode all observe in production (their MCP
    # registries are derived from the Claude Code reference impl).
    GEMINI_SHAPE_PLATFORMS = {"gemini_cli"}

    if cli_platform is None:
        emit_double = True
        emit_single = True
    elif cli_platform in GEMINI_SHAPE_PLATFORMS:
        emit_double = False
        emit_single = True
    else:
        emit_double = True
        emit_single = False

    parts: list[str] = []
    has_higgsfield = False
    for name in tool_names:
        if name.startswith("higgsfield_"):
            has_higgsfield = True
            continue
        if emit_double:
            parts.append(f"mcp__agentprovision__{name}")
        if emit_single:
            parts.append(f"mcp_agentprovision_{name}")
    if has_higgsfield:
        if emit_double:
            parts.append("mcp__higgsfield__*")
        if emit_single:
            parts.append("mcp_higgsfield_*")
    return ",".join(parts)
