"""Unified risk taxonomy and tenant/channel safety policy evaluation."""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
import re
from typing import Dict, Iterable, List, Optional
import uuid

from sqlalchemy.orm import Session

from app.models.safety_policy import TenantActionPolicy
from app.schemas.safety_policy import (
    ActionType,
    PolicyDecision,
    Reversibility,
    RiskClass,
    RiskLevel,
    SafetyActionCatalogEntry,
    SafetyActionEvaluation,
    SideEffectLevel,
    TenantActionPolicyUpsert,
)

ALL_CHANNEL = "*"
KNOWN_CHANNELS = ("web", "whatsapp", "workflow", "local_agent", "api", "webhook")
_DECISION_SEVERITY = {
    PolicyDecision.ALLOW: 0,
    PolicyDecision.ALLOW_WITH_LOGGING: 1,
    PolicyDecision.REQUIRE_CONFIRMATION: 2,
    PolicyDecision.REQUIRE_REVIEW: 3,
    PolicyDecision.BLOCK: 4,
}


@dataclass(frozen=True)
class ActionRiskProfile:
    action_type: ActionType
    action_name: str
    category: str
    risk_class: RiskClass
    risk_level: RiskLevel
    side_effect_level: SideEffectLevel
    reversibility: Reversibility

    @property
    def action_key(self) -> str:
        return f"{self.action_type.value}:{self.action_name}"


_WORKFLOW_PROFILES: Dict[str, ActionRiskProfile] = {
    "agent": ActionRiskProfile(
        action_type=ActionType.WORKFLOW_ACTION,
        action_name="agent",
        category="dynamic_workflow",
        risk_class=RiskClass.ORCHESTRATION_CONTROL,
        risk_level=RiskLevel.HIGH,
        side_effect_level=SideEffectLevel.EXTERNAL_WRITE,
        reversibility=Reversibility.UNKNOWN,
    ),
    "mcp_tool": ActionRiskProfile(
        action_type=ActionType.WORKFLOW_ACTION,
        action_name="mcp_tool",
        category="dynamic_workflow",
        risk_class=RiskClass.ORCHESTRATION_CONTROL,
        risk_level=RiskLevel.HIGH,
        side_effect_level=SideEffectLevel.EXTERNAL_WRITE,
        reversibility=Reversibility.UNKNOWN,
    ),
    "condition": ActionRiskProfile(
        action_type=ActionType.WORKFLOW_ACTION,
        action_name="condition",
        category="dynamic_workflow",
        risk_class=RiskClass.READ_ONLY,
        risk_level=RiskLevel.LOW,
        side_effect_level=SideEffectLevel.NONE,
        reversibility=Reversibility.REVERSIBLE,
    ),
    "transform": ActionRiskProfile(
        action_type=ActionType.WORKFLOW_ACTION,
        action_name="transform",
        category="dynamic_workflow",
        risk_class=RiskClass.READ_ONLY,
        risk_level=RiskLevel.LOW,
        side_effect_level=SideEffectLevel.NONE,
        reversibility=Reversibility.REVERSIBLE,
    ),
    "wait": ActionRiskProfile(
        action_type=ActionType.WORKFLOW_ACTION,
        action_name="wait",
        category="dynamic_workflow",
        risk_class=RiskClass.READ_ONLY,
        risk_level=RiskLevel.LOW,
        side_effect_level=SideEffectLevel.NONE,
        reversibility=Reversibility.REVERSIBLE,
    ),
    "human_approval": ActionRiskProfile(
        action_type=ActionType.WORKFLOW_ACTION,
        action_name="human_approval",
        category="dynamic_workflow",
        risk_class=RiskClass.ORCHESTRATION_CONTROL,
        risk_level=RiskLevel.MEDIUM,
        side_effect_level=SideEffectLevel.NONE,
        reversibility=Reversibility.REVERSIBLE,
    ),
    "parallel": ActionRiskProfile(
        action_type=ActionType.WORKFLOW_ACTION,
        action_name="parallel",
        category="dynamic_workflow",
        risk_class=RiskClass.ORCHESTRATION_CONTROL,
        risk_level=RiskLevel.MEDIUM,
        side_effect_level=SideEffectLevel.NONE,
        reversibility=Reversibility.REVERSIBLE,
    ),
    "for_each": ActionRiskProfile(
        action_type=ActionType.WORKFLOW_ACTION,
        action_name="for_each",
        category="dynamic_workflow",
        risk_class=RiskClass.ORCHESTRATION_CONTROL,
        risk_level=RiskLevel.MEDIUM,
        side_effect_level=SideEffectLevel.NONE,
        reversibility=Reversibility.REVERSIBLE,
    ),
    "workflow": ActionRiskProfile(
        action_type=ActionType.WORKFLOW_ACTION,
        action_name="workflow",
        category="dynamic_workflow",
        risk_class=RiskClass.ORCHESTRATION_CONTROL,
        risk_level=RiskLevel.HIGH,
        side_effect_level=SideEffectLevel.EXTERNAL_WRITE,
        reversibility=Reversibility.UNKNOWN,
    ),
}

_MCP_SPECIAL_CASES: Dict[str, ActionRiskProfile] = {
    "execute_shell": ActionRiskProfile(ActionType.MCP_TOOL, "execute_shell", "shell", RiskClass.EXECUTION_CONTROL, RiskLevel.CRITICAL, SideEffectLevel.CODE_EXECUTION, Reversibility.UNKNOWN),
    "deploy_changes": ActionRiskProfile(ActionType.MCP_TOOL, "deploy_changes", "shell", RiskClass.EXECUTION_CONTROL, RiskLevel.CRITICAL, SideEffectLevel.CODE_EXECUTION, Reversibility.UNKNOWN),
    "send_email": ActionRiskProfile(ActionType.MCP_TOOL, "send_email", "email", RiskClass.EXTERNAL_WRITE, RiskLevel.HIGH, SideEffectLevel.EXTERNAL_WRITE, Reversibility.PARTIAL),
    "create_calendar_event": ActionRiskProfile(ActionType.MCP_TOOL, "create_calendar_event", "calendar", RiskClass.EXTERNAL_WRITE, RiskLevel.HIGH, SideEffectLevel.EXTERNAL_WRITE, Reversibility.PARTIAL),
    "create_jira_issue": ActionRiskProfile(ActionType.MCP_TOOL, "create_jira_issue", "jira", RiskClass.EXTERNAL_WRITE, RiskLevel.HIGH, SideEffectLevel.EXTERNAL_WRITE, Reversibility.PARTIAL),
    "update_jira_issue": ActionRiskProfile(ActionType.MCP_TOOL, "update_jira_issue", "jira", RiskClass.EXTERNAL_WRITE, RiskLevel.HIGH, SideEffectLevel.EXTERNAL_WRITE, Reversibility.PARTIAL),
    "create_entity": ActionRiskProfile(ActionType.MCP_TOOL, "create_entity", "knowledge", RiskClass.INTERNAL_MUTATION, RiskLevel.MEDIUM, SideEffectLevel.INTERNAL_STATE, Reversibility.PARTIAL),
    "update_entity": ActionRiskProfile(ActionType.MCP_TOOL, "update_entity", "knowledge", RiskClass.INTERNAL_MUTATION, RiskLevel.MEDIUM, SideEffectLevel.INTERNAL_STATE, Reversibility.PARTIAL),
    "merge_entities": ActionRiskProfile(ActionType.MCP_TOOL, "merge_entities", "knowledge", RiskClass.INTERNAL_MUTATION, RiskLevel.HIGH, SideEffectLevel.INTERNAL_STATE, Reversibility.PARTIAL),
    "create_relation": ActionRiskProfile(ActionType.MCP_TOOL, "create_relation", "knowledge", RiskClass.INTERNAL_MUTATION, RiskLevel.MEDIUM, SideEffectLevel.INTERNAL_STATE, Reversibility.PARTIAL),
    "record_observation": ActionRiskProfile(ActionType.MCP_TOOL, "record_observation", "knowledge", RiskClass.INTERNAL_MUTATION, RiskLevel.MEDIUM, SideEffectLevel.INTERNAL_STATE, Reversibility.PARTIAL),
    "update_pipeline_stage": ActionRiskProfile(ActionType.MCP_TOOL, "update_pipeline_stage", "sales", RiskClass.INTERNAL_MUTATION, RiskLevel.HIGH, SideEffectLevel.INTERNAL_STATE, Reversibility.PARTIAL),
    "schedule_followup": ActionRiskProfile(ActionType.MCP_TOOL, "schedule_followup", "sales", RiskClass.EXTERNAL_WRITE, RiskLevel.HIGH, SideEffectLevel.EXTERNAL_WRITE, Reversibility.PARTIAL),
    "register_webhook": ActionRiskProfile(ActionType.MCP_TOOL, "register_webhook", "webhooks", RiskClass.EXTERNAL_WRITE, RiskLevel.HIGH, SideEffectLevel.EXTERNAL_WRITE, Reversibility.PARTIAL),
    "delete_webhook": ActionRiskProfile(ActionType.MCP_TOOL, "delete_webhook", "webhooks", RiskClass.EXTERNAL_WRITE, RiskLevel.HIGH, SideEffectLevel.EXTERNAL_WRITE, Reversibility.PARTIAL),
    "send_webhook_event": ActionRiskProfile(ActionType.MCP_TOOL, "send_webhook_event", "webhooks", RiskClass.EXTERNAL_WRITE, RiskLevel.HIGH, SideEffectLevel.EXTERNAL_WRITE, Reversibility.PARTIAL),
    "connect_mcp_server": ActionRiskProfile(ActionType.MCP_TOOL, "connect_mcp_server", "mcp_servers", RiskClass.ORCHESTRATION_CONTROL, RiskLevel.HIGH, SideEffectLevel.EXTERNAL_WRITE, Reversibility.PARTIAL),
    "disconnect_mcp_server": ActionRiskProfile(ActionType.MCP_TOOL, "disconnect_mcp_server", "mcp_servers", RiskClass.ORCHESTRATION_CONTROL, RiskLevel.HIGH, SideEffectLevel.EXTERNAL_WRITE, Reversibility.PARTIAL),
    "call_mcp_tool": ActionRiskProfile(ActionType.MCP_TOOL, "call_mcp_tool", "mcp_servers", RiskClass.EXECUTION_CONTROL, RiskLevel.CRITICAL, SideEffectLevel.CODE_EXECUTION, Reversibility.UNKNOWN),
    "run_dynamic_workflow": ActionRiskProfile(ActionType.MCP_TOOL, "run_dynamic_workflow", "dynamic_workflows", RiskClass.ORCHESTRATION_CONTROL, RiskLevel.HIGH, SideEffectLevel.EXTERNAL_WRITE, Reversibility.UNKNOWN),
    "activate_dynamic_workflow": ActionRiskProfile(ActionType.MCP_TOOL, "activate_dynamic_workflow", "dynamic_workflows", RiskClass.ORCHESTRATION_CONTROL, RiskLevel.HIGH, SideEffectLevel.EXTERNAL_WRITE, Reversibility.PARTIAL),
    "install_workflow_template": ActionRiskProfile(ActionType.MCP_TOOL, "install_workflow_template", "dynamic_workflows", RiskClass.ORCHESTRATION_CONTROL, RiskLevel.HIGH, SideEffectLevel.INTERNAL_STATE, Reversibility.PARTIAL),
    "start_inbox_monitor": ActionRiskProfile(ActionType.MCP_TOOL, "start_inbox_monitor", "monitor", RiskClass.ORCHESTRATION_CONTROL, RiskLevel.HIGH, SideEffectLevel.EXTERNAL_WRITE, Reversibility.PARTIAL),
    "stop_inbox_monitor": ActionRiskProfile(ActionType.MCP_TOOL, "stop_inbox_monitor", "monitor", RiskClass.ORCHESTRATION_CONTROL, RiskLevel.HIGH, SideEffectLevel.EXTERNAL_WRITE, Reversibility.PARTIAL),
    "start_competitor_monitor": ActionRiskProfile(ActionType.MCP_TOOL, "start_competitor_monitor", "monitor", RiskClass.ORCHESTRATION_CONTROL, RiskLevel.HIGH, SideEffectLevel.EXTERNAL_WRITE, Reversibility.PARTIAL),
    "stop_competitor_monitor": ActionRiskProfile(ActionType.MCP_TOOL, "stop_competitor_monitor", "monitor", RiskClass.ORCHESTRATION_CONTROL, RiskLevel.HIGH, SideEffectLevel.EXTERNAL_WRITE, Reversibility.PARTIAL),
    "test_webhook": ActionRiskProfile(ActionType.MCP_TOOL, "test_webhook", "webhooks", RiskClass.EXTERNAL_WRITE, RiskLevel.MEDIUM, SideEffectLevel.EXTERNAL_WRITE, Reversibility.REVERSIBLE),
}

_SAFE_GENERATORS = {
    "draft_outreach",
    "generate_proposal",
    "generate_excel_report",
    "extract_document_data",
    "qualify_lead",
    "forecast",
    "calculate",
    "compare_periods",
    "match_skills_to_context",
}

_FALLBACK_MCP_TOOL_CATEGORIES: Dict[str, str] = {
    "list_meta_campaigns": "ads",
    "get_meta_campaign_insights": "ads",
    "pause_meta_campaign": "ads",
    "search_meta_ad_library": "ads",
    "list_google_campaigns": "ads",
    "get_google_campaign_metrics": "ads",
    "pause_google_campaign": "ads",
    "search_google_ads_transparency": "ads",
    "list_tiktok_campaigns": "ads",
    "get_tiktok_campaign_insights": "ads",
    "pause_tiktok_campaign": "ads",
    "search_tiktok_creative_center": "ads",
    "calculate": "analytics",
    "compare_periods": "analytics",
    "forecast": "analytics",
    "list_calendar_events": "calendar",
    "create_calendar_event": "calendar",
    "add_competitor": "competitor",
    "list_competitors": "competitor",
    "remove_competitor": "competitor",
    "get_competitor_report": "competitor",
    "compare_campaigns": "competitor",
    "query_data_source": "connectors",
    "discover_datasets": "data",
    "get_dataset_schema": "data",
    "query_sql": "data",
    "generate_insights": "data",
    "search_drive_files": "drive",
    "read_drive_file": "drive",
    "create_drive_file": "drive",
    "list_drive_folders": "drive",
    "create_dynamic_workflow": "dynamic_workflows",
    "list_dynamic_workflows": "dynamic_workflows",
    "run_dynamic_workflow": "dynamic_workflows",
    "get_workflow_run_status": "dynamic_workflows",
    "activate_dynamic_workflow": "dynamic_workflows",
    "install_workflow_template": "dynamic_workflows",
    "list_connected_email_accounts": "email",
    "search_emails": "email",
    "read_email": "email",
    "send_email": "email",
    "download_attachment": "email",
    "deep_scan_emails": "email",
    "list_github_repos": "github",
    "get_github_repo": "github",
    "list_github_issues": "github",
    "get_github_issue": "github",
    "list_github_pull_requests": "github",
    "get_github_pull_request": "github",
    "read_github_file": "github",
    "search_github_code": "github",
    "search_jira_issues": "jira",
    "get_jira_issue": "jira",
    "create_jira_issue": "jira",
    "update_jira_issue": "jira",
    "list_jira_projects": "jira",
    "create_entity": "knowledge",
    "find_entities": "knowledge",
    "update_entity": "knowledge",
    "merge_entities": "knowledge",
    "create_relation": "knowledge",
    "find_relations": "knowledge",
    "get_neighborhood": "knowledge",
    "record_observation": "knowledge",
    "get_entity_timeline": "knowledge",
    "search_knowledge": "knowledge",
    "get_git_history": "knowledge",
    "get_pr_status": "knowledge",
    "ask_knowledge_graph": "knowledge",
    "connect_mcp_server": "mcp_servers",
    "list_mcp_servers": "mcp_servers",
    "discover_mcp_tools": "mcp_servers",
    "call_mcp_tool": "mcp_servers",
    "disconnect_mcp_server": "mcp_servers",
    "health_check_mcp_server": "mcp_servers",
    "get_mcp_server_logs": "mcp_servers",
    "start_inbox_monitor": "monitor",
    "stop_inbox_monitor": "monitor",
    "check_inbox_monitor_status": "monitor",
    "start_competitor_monitor": "monitor",
    "stop_competitor_monitor": "monitor",
    "check_competitor_monitor_status": "monitor",
    "extract_document_data": "reports",
    "generate_excel_report": "reports",
    "qualify_lead": "sales",
    "draft_outreach": "sales",
    "update_pipeline_stage": "sales",
    "get_pipeline_summary": "sales",
    "generate_proposal": "sales",
    "schedule_followup": "sales",
    "execute_shell": "shell",
    "deploy_changes": "shell",
    "list_skills": "skills",
    "run_skill": "skills",
    "match_skills_to_context": "skills",
    "recall_memory": "skills",
    "register_webhook": "webhooks",
    "list_webhooks": "webhooks",
    "delete_webhook": "webhooks",
    "test_webhook": "webhooks",
    "send_webhook_event": "webhooks",
    "get_webhook_logs": "webhooks",
}


def _repo_root() -> Path:
    current = Path(__file__).resolve()
    for candidate in (current.parent, *current.parents):
        if (candidate / "apps" / "mcp-server" / "src" / "mcp_tools").exists():
            return candidate
        if (candidate / "mcp-server" / "src" / "mcp_tools").exists():
            return candidate
    return current.parents[3]


@lru_cache(maxsize=1)
def _discover_mcp_profiles() -> Dict[str, ActionRiskProfile]:
    tool_dir = _repo_root() / "apps" / "mcp-server" / "src" / "mcp_tools"
    pattern = re.compile(r"@mcp\.tool\(\)\s+async def\s+([a-zA-Z_][a-zA-Z0-9_]*)", re.MULTILINE)
    profiles: Dict[str, ActionRiskProfile] = {}

    if not tool_dir.exists():
        return {
            action_name: _classify_mcp_tool(action_name, category)
            for action_name, category in _FALLBACK_MCP_TOOL_CATEGORIES.items()
        }

    for path in sorted(tool_dir.glob("*.py")):
        if path.name == "__init__.py":
            continue
        text = path.read_text(encoding="utf-8")
        for match in pattern.finditer(text):
            action_name = match.group(1)
            profiles[action_name] = _classify_mcp_tool(action_name, path.stem)
    if not profiles:
        return {
            action_name: _classify_mcp_tool(action_name, category)
            for action_name, category in _FALLBACK_MCP_TOOL_CATEGORIES.items()
        }
    return profiles


def _classify_mcp_tool(action_name: str, category: str) -> ActionRiskProfile:
    if action_name in _MCP_SPECIAL_CASES:
        return _MCP_SPECIAL_CASES[action_name]
    if action_name in _SAFE_GENERATORS:
        return ActionRiskProfile(
            action_type=ActionType.MCP_TOOL,
            action_name=action_name,
            category=category,
            risk_class=RiskClass.READ_ONLY,
            risk_level=RiskLevel.LOW,
            side_effect_level=SideEffectLevel.NONE,
            reversibility=Reversibility.REVERSIBLE,
        )

    read_prefixes = ("search_", "find_", "list_", "get_", "read_", "check_", "ask_", "recall_", "discover_", "download_")
    internal_mutation_prefixes = ("create_", "update_", "merge_", "record_", "activate_", "install_")
    external_write_prefixes = ("send_", "register_", "delete_", "start_", "stop_", "connect_", "disconnect_", "run_")

    if action_name.startswith(read_prefixes):
        return ActionRiskProfile(ActionType.MCP_TOOL, action_name, category, RiskClass.READ_ONLY, RiskLevel.LOW, SideEffectLevel.NONE, Reversibility.REVERSIBLE)
    if action_name.startswith("execute_") or action_name.startswith("deploy_"):
        return ActionRiskProfile(ActionType.MCP_TOOL, action_name, category, RiskClass.EXECUTION_CONTROL, RiskLevel.CRITICAL, SideEffectLevel.CODE_EXECUTION, Reversibility.UNKNOWN)
    if action_name.startswith(internal_mutation_prefixes):
        return ActionRiskProfile(ActionType.MCP_TOOL, action_name, category, RiskClass.INTERNAL_MUTATION, RiskLevel.MEDIUM, SideEffectLevel.INTERNAL_STATE, Reversibility.PARTIAL)
    if action_name.startswith(external_write_prefixes):
        return ActionRiskProfile(ActionType.MCP_TOOL, action_name, category, RiskClass.EXTERNAL_WRITE, RiskLevel.HIGH, SideEffectLevel.EXTERNAL_WRITE, Reversibility.PARTIAL)

    return ActionRiskProfile(ActionType.MCP_TOOL, action_name, category, RiskClass.ORCHESTRATION_CONTROL, RiskLevel.MEDIUM, SideEffectLevel.NONE, Reversibility.UNKNOWN)


def _all_profiles() -> Dict[str, ActionRiskProfile]:
    profiles = {profile.action_key: profile for profile in _discover_mcp_profiles().values()}
    profiles.update({profile.action_key: profile for profile in _WORKFLOW_PROFILES.values()})
    return profiles


def _normalize_channel(channel: Optional[str]) -> str:
    if not channel:
        return "web"
    return channel.strip().lower()


def _default_decision_for(profile: ActionRiskProfile, channel: str) -> tuple[PolicyDecision, str]:
    if channel == "local_agent":
        if profile.side_effect_level == SideEffectLevel.NONE and profile.risk_level == RiskLevel.LOW:
            return PolicyDecision.ALLOW_WITH_LOGGING, "Local runtime is restricted to low-risk read-only actions."
        return PolicyDecision.BLOCK, "Local runtime is read-only and blocks mutating or high-risk actions."

    if profile.side_effect_level == SideEffectLevel.CODE_EXECUTION:
        if channel in ("workflow", "webhook"):
            return PolicyDecision.BLOCK, "Code execution and deployment actions are blocked for automated channels."
        return PolicyDecision.REQUIRE_REVIEW, "Execution-control actions require explicit human review."

    if profile.side_effect_level == SideEffectLevel.EXTERNAL_WRITE:
        if channel in ("workflow", "webhook"):
            return PolicyDecision.REQUIRE_REVIEW, "External side effects on automated channels require review."
        return PolicyDecision.REQUIRE_CONFIRMATION, "External side effects require human confirmation."

    if profile.side_effect_level == SideEffectLevel.INTERNAL_STATE:
        if channel in ("workflow", "webhook"):
            return PolicyDecision.REQUIRE_REVIEW, "State mutations from automated channels require review."
        return PolicyDecision.REQUIRE_CONFIRMATION, "State mutations require confirmation before execution."

    if profile.risk_level == RiskLevel.MEDIUM:
        return PolicyDecision.ALLOW_WITH_LOGGING, "Medium-risk read paths are allowed with audit logging."

    return PolicyDecision.ALLOW_WITH_LOGGING, "Low-risk read-only actions are allowed with audit logging."


def _action_key(action_type: ActionType, action_name: str) -> str:
    return f"{action_type.value}:{action_name}"


def _get_profile(action_type: ActionType, action_name: str) -> ActionRiskProfile:
    profile = _all_profiles().get(_action_key(action_type, action_name))
    if not profile:
        raise ValueError(f"Unknown governed action: {action_type.value}:{action_name}")
    return profile


def _get_policy_override(
    db: Session,
    tenant_id: uuid.UUID,
    action_type: ActionType,
    action_name: str,
    channel: str,
) -> Optional[TenantActionPolicy]:
    return (
        db.query(TenantActionPolicy)
        .filter(
            TenantActionPolicy.tenant_id == tenant_id,
            TenantActionPolicy.action_type == action_type.value,
            TenantActionPolicy.action_name == action_name,
            TenantActionPolicy.channel.in_([channel, ALL_CHANNEL]),
            TenantActionPolicy.enabled.is_(True),
        )
        .order_by(TenantActionPolicy.channel.desc())
        .first()
    )


def _validate_override_ceiling(
    profile: ActionRiskProfile,
    channel: str,
    requested_decision: PolicyDecision,
) -> None:
    if profile.risk_level not in (RiskLevel.HIGH, RiskLevel.CRITICAL):
        return

    channels = KNOWN_CHANNELS if channel == ALL_CHANNEL else (channel,)
    for candidate_channel in channels:
        default_decision, _ = _default_decision_for(profile, candidate_channel)
        if _DECISION_SEVERITY[requested_decision] < _DECISION_SEVERITY[default_decision]:
            raise ValueError(
                "Tenant overrides cannot relax "
                f"{profile.action_key} on channel '{candidate_channel}' below the default "
                f"{default_decision.value} policy for {profile.risk_level.value}-risk actions."
            )


def list_action_catalog(
    db: Session,
    tenant_id: uuid.UUID,
    channel: str = "web",
    action_type: Optional[ActionType] = None,
) -> List[SafetyActionCatalogEntry]:
    channel = _normalize_channel(channel)
    rows: List[SafetyActionCatalogEntry] = []

    for profile in sorted(_all_profiles().values(), key=lambda item: (item.action_type.value, item.category, item.action_name)):
        if action_type and profile.action_type != action_type:
            continue
        evaluation = evaluate_action(db, tenant_id, profile.action_type, profile.action_name, channel)
        default_channel_policies = {
            known_channel: _default_decision_for(profile, known_channel)[0]
            for known_channel in KNOWN_CHANNELS
        }
        rows.append(
            SafetyActionCatalogEntry(
                action_key=profile.action_key,
                action_type=profile.action_type,
                action_name=profile.action_name,
                category=profile.category,
                risk_class=profile.risk_class,
                risk_level=profile.risk_level,
                side_effect_level=profile.side_effect_level,
                reversibility=profile.reversibility,
                default_channel_policies=default_channel_policies,
                effective_decision=evaluation.decision,
                decision_source=evaluation.decision_source,
                rationale=evaluation.rationale,
                policy_override_id=evaluation.policy_override_id,
            )
        )
    return rows


def list_tenant_policies(db: Session, tenant_id: uuid.UUID) -> List[TenantActionPolicy]:
    return (
        db.query(TenantActionPolicy)
        .filter(TenantActionPolicy.tenant_id == tenant_id)
        .order_by(TenantActionPolicy.action_type, TenantActionPolicy.action_name, TenantActionPolicy.channel)
        .all()
    )


def upsert_tenant_policy(
    db: Session,
    tenant_id: uuid.UUID,
    created_by: uuid.UUID,
    policy_in: TenantActionPolicyUpsert,
) -> TenantActionPolicy:
    channel = _normalize_channel(policy_in.channel)
    profile = _get_profile(policy_in.action_type, policy_in.action_name)
    _validate_override_ceiling(profile, channel, policy_in.decision)

    policy = (
        db.query(TenantActionPolicy)
        .filter(
            TenantActionPolicy.tenant_id == tenant_id,
            TenantActionPolicy.action_type == policy_in.action_type.value,
            TenantActionPolicy.action_name == policy_in.action_name,
            TenantActionPolicy.channel == channel,
        )
        .first()
    )
    if not policy:
        policy = TenantActionPolicy(
            tenant_id=tenant_id,
            created_by=created_by,
            action_type=policy_in.action_type.value,
            action_name=policy_in.action_name,
            channel=channel,
        )
        db.add(policy)

    policy.decision = policy_in.decision.value
    policy.rationale = policy_in.rationale
    policy.enabled = policy_in.enabled
    policy.created_by = created_by
    db.commit()
    db.refresh(policy)
    return policy


def delete_tenant_policy(db: Session, tenant_id: uuid.UUID, policy_id: uuid.UUID) -> bool:
    policy = (
        db.query(TenantActionPolicy)
        .filter(
            TenantActionPolicy.id == policy_id,
            TenantActionPolicy.tenant_id == tenant_id,
        )
        .first()
    )
    if not policy:
        return False
    db.delete(policy)
    db.commit()
    return True


def evaluate_action(
    db: Session,
    tenant_id: uuid.UUID,
    action_type: ActionType,
    action_name: str,
    channel: str = "web",
) -> SafetyActionEvaluation:
    normalized_channel = _normalize_channel(channel)
    profile = _get_profile(action_type, action_name)
    default_decision, default_rationale = _default_decision_for(profile, normalized_channel)
    override = _get_policy_override(db, tenant_id, action_type, action_name, normalized_channel)

    if override:
        decision = PolicyDecision(override.decision)
        rationale = override.rationale or f"Tenant override for channel '{override.channel}'."
        source = "tenant_policy"
        policy_override_id = override.id
    else:
        decision = default_decision
        rationale = default_rationale
        source = "default_risk_policy"
        policy_override_id = None

    return SafetyActionEvaluation(
        action_key=profile.action_key,
        action_type=profile.action_type,
        action_name=profile.action_name,
        category=profile.category,
        channel=normalized_channel,
        risk_class=profile.risk_class,
        risk_level=profile.risk_level,
        side_effect_level=profile.side_effect_level,
        reversibility=profile.reversibility,
        default_decision=default_decision,
        decision=decision,
        decision_source=source,
        rationale=rationale,
        policy_override_id=policy_override_id,
    )
