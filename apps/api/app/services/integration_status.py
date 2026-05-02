"""Integration status service — checks which integrations are connected and maps tools to integrations."""

import uuid
from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session

from app.models.integration_config import IntegrationConfig
from app.models.integration_credential import IntegrationCredential


# ---------------------------------------------------------------------------
# Static mapping: MCP tool name -> required integration (None = no integration needed)
# ---------------------------------------------------------------------------

TOOL_INTEGRATION_MAP: Dict[str, Optional[str]] = {
    # Gmail
    "search_emails": "gmail",
    "send_email": "gmail",
    "read_email": "gmail",
    "deep_scan_emails": "gmail",
    "download_attachment": "gmail",
    # Google Calendar
    "list_calendar_events": "google_calendar",
    "create_calendar_event": "google_calendar",
    # Google Drive
    "search_drive_files": "google_drive",
    "read_drive_file": "google_drive",
    "create_drive_file": "google_drive",
    "list_drive_folders": "google_drive",
    # Jira
    "create_jira_issue": "jira",
    "get_jira_issue": "jira",
    "update_jira_issue": "jira",
    "search_jira_issues": "jira",
    "list_jira_projects": "jira",
    # GitHub
    "search_github_code": "github",
    "list_github_repos": "github",
    "list_github_issues": "github",
    "get_github_issue": "github",
    "list_github_pull_requests": "github",
    "get_github_pull_request": "github",
    "get_github_repo": "github",
    "read_github_file": "github",
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
    # Tools that don't need any integration
    "create_entity": None,
    "find_entities": None,
    "score_entity": None,
    "update_entity": None,
    "merge_entities": None,
    "find_relations": None,
    "create_relation": None,
    "search_knowledge": None,
    "ask_knowledge_graph": None,
    "record_observation": None,
    "recall_memory": None,
    "calculate": None,
    "query_sql": None,
    "execute_shell": None,
    "forecast": None,
}


# ---------------------------------------------------------------------------
# Display metadata per integration
# ---------------------------------------------------------------------------

INTEGRATION_DISPLAY: Dict[str, Dict[str, str]] = {
    "gmail": {"name": "Gmail", "icon": "FaEnvelope"},
    "google_calendar": {"name": "Google Calendar", "icon": "FaCalendar"},
    "google_drive": {"name": "Google Drive", "icon": "FaGoogleDrive"},
    "github": {"name": "GitHub Copilot CLI", "icon": "FaGithub"},
    "jira": {"name": "Jira", "icon": "FaTasks"},
    "meta_ads": {"name": "Meta Ads", "icon": "FaFacebook"},
    "google_ads": {"name": "Google Ads", "icon": "FaGoogle"},
    "tiktok_ads": {"name": "TikTok Ads", "icon": "FaTiktok"},
    "slack": {"name": "Slack", "icon": "FaSlack"},
    "whatsapp": {"name": "WhatsApp", "icon": "FaWhatsapp"},
    "notion": {"name": "Notion", "icon": "FaBook"},
    "outlook": {"name": "Outlook", "icon": "FaMicrosoft"},
    "linkedin": {"name": "LinkedIn", "icon": "FaLinkedin"},
    "claude_code": {"name": "Claude Code", "icon": "FaTerminal"},
    "codex": {"name": "Codex CLI", "icon": "FaTerminal"},
    "gemini_cli": {"name": "Gemini CLI", "icon": "FaGoogle"},
}


# ---------------------------------------------------------------------------
# Service functions
# ---------------------------------------------------------------------------

def get_connected_integrations(
    db: Session,
    tenant_id: uuid.UUID,
) -> Dict[str, Dict[str, Any]]:
    """
    Query IntegrationConfig + IntegrationCredential to determine which
    integrations are connected (enabled config with at least one active credential).

    Returns dict of integration_name -> {connected, name, icon}.
    """
    # Get all enabled configs for this tenant
    configs = (
        db.query(IntegrationConfig)
        .filter(
            IntegrationConfig.tenant_id == tenant_id,
            IntegrationConfig.enabled == True,
        )
        .all()
    )

    # Build a set of config IDs that have at least one active credential
    config_ids = [c.id for c in configs]
    connected_config_ids = set()
    if config_ids:
        creds = (
            db.query(IntegrationCredential.integration_config_id)
            .filter(
                IntegrationCredential.integration_config_id.in_(config_ids),
                IntegrationCredential.tenant_id == tenant_id,
                IntegrationCredential.status == "active",
            )
            .distinct()
            .all()
        )
        connected_config_ids = {row[0] for row in creds}

    # Map integration_name -> connected boolean
    connected_names: Dict[str, bool] = {}
    for config in configs:
        name = config.integration_name
        # OAuth integrations (gmail, google_calendar, etc.) are connected if
        # the config exists and is enabled — they don't necessarily store
        # credentials in the IntegrationCredential table (tokens are in the
        # credential vault or obtained via OAuth flow).  For manual-credential
        # integrations we require at least one active credential row.
        has_creds = config.id in connected_config_ids
        # If already marked connected from another config row, keep it
        if connected_names.get(name):
            continue
        connected_names[name] = has_creds or config.account_email is not None

    # Build the full result with display info for all known integrations
    result: Dict[str, Dict[str, Any]] = {}
    for integration_name, display in INTEGRATION_DISPLAY.items():
        result[integration_name] = {
            "connected": connected_names.get(integration_name, False),
            "name": display["name"],
            "icon": display["icon"],
        }

    return result


def get_tool_mapping() -> Dict[str, Optional[str]]:
    """Return the static TOOL_INTEGRATION_MAP."""
    return TOOL_INTEGRATION_MAP


def _collect_tool_names(definition: Dict[str, Any]) -> List[str]:
    """Recursively walk a workflow definition and collect all MCP tool names."""
    tools: List[str] = []
    for step in definition.get("steps", []):
        if step.get("type") == "mcp_tool" and step.get("tool"):
            tools.append(step["tool"])
        # Recurse into sub-steps (for_each, parallel, etc.)
        if step.get("steps"):
            tools.extend(_collect_tool_names({"steps": step["steps"]}))
    return tools


def check_workflow_integrations(
    db: Session,
    tenant_id: uuid.UUID,
    definition: Dict[str, Any],
) -> List[Dict[str, str]]:
    """
    Walk a workflow definition, collect all tool names, check which
    integrations they require, and return the list of disconnected
    integrations that would block activation.

    Returns a list of dicts: [{"integration": "gmail", "name": "Gmail", "icon": "FaEnvelope"}, ...]
    Empty list means all required integrations are connected.
    """
    tool_names = _collect_tool_names(definition)

    # Determine which integrations are needed
    needed_integrations: set = set()
    for tool_name in tool_names:
        integration = TOOL_INTEGRATION_MAP.get(tool_name)
        if integration is not None:
            needed_integrations.add(integration)

    if not needed_integrations:
        return []

    # Check which are connected
    connected = get_connected_integrations(db, tenant_id)

    missing: List[Dict[str, str]] = []
    for integration_name in sorted(needed_integrations):
        status = connected.get(integration_name, {})
        if not status.get("connected", False):
            display = INTEGRATION_DISPLAY.get(integration_name, {})
            missing.append({
                "integration": integration_name,
                "name": display.get("name", integration_name),
                "icon": display.get("icon", "FaCog"),
            })

    return missing
