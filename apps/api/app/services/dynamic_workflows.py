"""Service layer for dynamic workflow validation and helpers."""

import re
from typing import Any, Dict, List, Optional, Set


# ── MCP tool -> integration mapping ────────────────────────────────
# Maps known MCP tool names to the integration they require.
# None means the tool is built-in and needs no external integration.

TOOL_INTEGRATION_MAP: Dict[str, Optional[str]] = {
    # Gmail
    "search_emails": "gmail",
    "send_email": "gmail",
    "read_email": "gmail",
    "deep_scan_emails": "gmail",
    "download_attachment": "gmail",
    "list_connected_email_accounts": "gmail",
    # Google Calendar
    "list_calendar_events": "google_calendar",
    "create_calendar_event": "google_calendar",
    # Jira
    "create_jira_issue": "jira",
    "search_jira_issues": "jira",
    "get_jira_issue": "jira",
    "update_jira_issue": "jira",
    "list_jira_projects": "jira",
    # GitHub
    "search_github_code": "github",
    "read_github_file": "github",
    "list_github_repos": "github",
    "list_github_issues": "github",
    "list_github_pull_requests": "github",
    "get_github_issue": "github",
    "get_github_pull_request": "github",
    "get_github_repo": "github",
    "get_git_history": "github",
    "get_pr_status": "github",
    "deploy_changes": "github",
    # Google Drive
    "search_drive_files": "google_drive",
    "read_drive_file": "google_drive",
    "create_drive_file": "google_drive",
    "list_drive_folders": "google_drive",
    # Meta Ads
    "list_meta_campaigns": "meta_ads",
    "get_meta_campaign_insights": "meta_ads",
    "pause_meta_campaign": "meta_ads",
    "search_meta_ad_library": "meta_ads",
    # Google Ads
    "list_google_campaigns": "google_ads",
    "get_google_campaign_metrics": "google_ads",
    "pause_google_campaign": "google_ads",
    "search_google_ads_transparency": "google_ads",
    # TikTok Ads
    "list_tiktok_campaigns": "tiktok_ads",
    "get_tiktok_campaign_insights": "tiktok_ads",
    "pause_tiktok_campaign": "tiktok_ads",
    "search_tiktok_creative_center": "tiktok_ads",
    # WhatsApp
    "schedule_followup": "whatsapp",
    # Webhooks
    "register_webhook": "webhooks",
    "list_webhooks": "webhooks",
    "delete_webhook": "webhooks",
    "test_webhook": "webhooks",
    "send_webhook_event": "webhooks",
    "get_webhook_logs": "webhooks",
    # Built-in (no integration required)
    "create_entity": None,
    "find_entities": None,
    "update_entity": None,
    "merge_entities": None,
    "create_relation": None,
    "find_relations": None,
    "get_neighborhood": None,
    "get_entity_timeline": None,
    "record_observation": None,
    "search_knowledge": None,
    "ask_knowledge_graph": None,
    "recall_memory": None,
    "calculate": None,
    "query_sql": None,
    "query_data_source": None,
    "discover_datasets": None,
    "get_dataset_schema": None,
    "generate_excel_report": None,
    "generate_insights": None,
    "generate_proposal": None,
    "forecast": None,
    "compare_periods": None,
    "compare_campaigns": None,
    "get_pipeline_summary": None,
    "update_pipeline_stage": None,
    "qualify_lead": None,
    "draft_outreach": None,
    "extract_document_data": None,
    "execute_shell": None,
    "list_skills": None,
    "run_skill": None,
    "read_library_skill": None,
    "health_check_mcp_server": None,
}

# Regex to find template variable references like {{step_id.field}} or {{input.field}}
_TEMPLATE_VAR_RE = re.compile(r"\{\{\s*(\w+)(?:\.\w+)*\s*\}\}")


def _collect_step_ids(steps: List[Dict[str, Any]]) -> List[str]:
    """Recursively collect all step IDs from a step list."""
    ids: List[str] = []
    for step in steps:
        if "id" in step:
            ids.append(step["id"])
        # Recurse into sub-steps (for_each, parallel)
        if "steps" in step and step["steps"]:
            ids.extend(_collect_step_ids(step["steps"]))
    return ids


def _collect_output_names(steps: List[Dict[str, Any]]) -> Set[str]:
    """Collect declared output variable names from steps."""
    names: Set[str] = set()
    for step in steps:
        if step.get("output"):
            names.add(step["output"])
        if "steps" in step and step["steps"]:
            names.update(_collect_output_names(step["steps"]))
    return names


def _extract_template_refs(obj: Any) -> Set[str]:
    """Extract all template variable root names from a nested structure."""
    refs: Set[str] = set()
    if isinstance(obj, str):
        refs.update(m.group(1) for m in _TEMPLATE_VAR_RE.finditer(obj))
    elif isinstance(obj, dict):
        for v in obj.values():
            refs.update(_extract_template_refs(v))
    elif isinstance(obj, list):
        for item in obj:
            refs.update(_extract_template_refs(item))
    return refs


def _describe_step(step: Dict[str, Any]) -> str:
    """Build a human-readable description for a planned step."""
    step_type = step.get("type", "unknown")
    step_id = step.get("id", "?")
    if step_type == "mcp_tool":
        return f"[{step_id}] call MCP tool '{step.get('tool', '?')}'"
    elif step_type == "agent":
        return f"[{step_id}] delegate to agent '{step.get('agent', '?')}'"
    elif step_type == "condition":
        return f"[{step_id}] conditional branch"
    elif step_type == "for_each":
        return f"[{step_id}] loop over '{step.get('collection', '?')}'"
    elif step_type == "parallel":
        sub_count = len(step.get("steps", []))
        return f"[{step_id}] parallel ({sub_count} branches)"
    elif step_type == "wait":
        return f"[{step_id}] wait {step.get('duration', '?')}"
    elif step_type == "transform":
        return f"[{step_id}] transform ({step.get('operation', '?')})"
    elif step_type == "human_approval":
        return f"[{step_id}] human approval gate"
    elif step_type == "webhook_trigger":
        return f"[{step_id}] webhook trigger"
    elif step_type == "workflow":
        return f"[{step_id}] sub-workflow"
    return f"[{step_id}] {step_type}"


def validate_workflow_definition(
    definition: Dict[str, Any],
    input_data: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Validate a workflow definition structurally without executing it.

    Returns a dict with:
        steps_planned   - list of human-readable step descriptions
        integrations_required - list of unique integration names needed
        validation_errors - list of error strings (empty = valid)
        step_count      - total number of steps (including nested)
    """
    errors: List[str] = []
    steps: List[Dict[str, Any]] = definition.get("steps", [])

    if not steps:
        errors.append("Workflow has no steps")
        return {
            "steps_planned": [],
            "integrations_required": [],
            "validation_errors": errors,
            "step_count": 0,
        }

    # 1. Unique step IDs
    all_ids = _collect_step_ids(steps)
    seen: Set[str] = set()
    for sid in all_ids:
        if sid in seen:
            errors.append(f"Duplicate step ID: '{sid}'")
        seen.add(sid)

    # 2. Validate MCP tool names and collect integrations
    integrations: Set[str] = set()
    unknown_tools: List[str] = []

    def _check_tools(step_list: List[Dict[str, Any]]) -> None:
        for step in step_list:
            if step.get("type") == "mcp_tool" and step.get("tool"):
                tool_name = step["tool"]
                if tool_name in TOOL_INTEGRATION_MAP:
                    integration = TOOL_INTEGRATION_MAP[tool_name]
                    if integration is not None:
                        integrations.add(integration)
                else:
                    unknown_tools.append(tool_name)
            if step.get("steps"):
                _check_tools(step["steps"])

    _check_tools(steps)

    for tool_name in unknown_tools:
        errors.append(f"Unrecognized MCP tool: '{tool_name}'")

    # 3. Validate template variable references
    output_names = _collect_output_names(steps)
    # "input" is always available as a reference source
    valid_sources = output_names | {"input"}

    template_refs = _extract_template_refs(definition)
    for ref in template_refs:
        if ref not in valid_sources:
            errors.append(
                f"Template variable '{{{{{ref}}}}}' references unknown output "
                f"(available: {', '.join(sorted(valid_sources))})"
            )

    # 4. Build planned steps list
    steps_planned: List[str] = []

    def _plan(step_list: List[Dict[str, Any]]) -> None:
        for step in step_list:
            steps_planned.append(_describe_step(step))
            if step.get("steps"):
                _plan(step["steps"])

    _plan(steps)

    return {
        "steps_planned": steps_planned,
        "integrations_required": sorted(integrations),
        "validation_errors": errors,
        "step_count": len(all_ids),
    }
