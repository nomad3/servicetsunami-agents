import logging
from fastapi import APIRouter
from app.api.v1 import (
    audit,
    auth,
    insights_resolver_metrics,
    channels,
    data_sources,
    data_pipelines,
    notebooks,
    agents,
    agent_groups,
    agent_tasks,
    tools,
    connectors,
    deployments,
    analytics,
    vector_stores,
    datasets,
    chat,
    internal,
    memories,
    knowledge,
    llm,
    branding,
    features,
    tenant_analytics,
    dataset_groups,
    integration_configs,
    integrations,
    users,
    skills_new,
    mcp_bridge,
    workflows,
    remedia,
    webhook_connectors,
    webhooks,
    mcp_server_connectors,
    oauth,
    claude_auth,
    codex_auth,
    gemini_cli_auth,
    notifications,
    reports,
    rl,
    local_ml,
    dynamic_workflows,
    safety,
    goals,
    commitments,
    agent_identity,
    world_state,
    memory_admin,
    causal_edges,
    plans,
    blackboards,
    collaborations,
    coalitions,
    learning,
    learning_dashboard,
    branding_domain,
    media,
    external_agents,
    agent_marketplace,
    agent_tests,
    insights_fleet_health,
    insights_cost,
    insights_coalition_replay,
)

_logger = logging.getLogger(__name__)

# Optional modules — import failures won't block API startup
_optional_modules = [
    "activities", "sales", "unsupervised_learning", "presence",
    "devices", "robot", "session_journals", "memory_continuity_internal",
]
_loaded_optional = {}
for _mod_name in _optional_modules:
    try:
        _loaded_optional[_mod_name] = __import__(f"app.api.v1.{_mod_name}", fromlist=[_mod_name])
    except Exception as e:
        _logger.warning("Optional route %s failed to load: %s", _mod_name, e)

router = APIRouter()

@router.get("/")
@router.get("")
def read_root():
    return {"message": "agentprovision.com API"}

router.include_router(auth.router, prefix="/auth", tags=["auth"])
router.include_router(channels.router, prefix="/channels", tags=["channels"])
router.include_router(users.router, prefix="/users", tags=["users"])
router.include_router(data_sources.router, prefix="/data_sources", tags=["data_sources"])
router.include_router(data_pipelines.router, prefix="/data_pipelines", tags=["data_pipelines"])
router.include_router(notebooks.router, prefix="/notebooks", tags=["notebooks"])
router.include_router(agents.router, prefix="/agents", tags=["agents"])
# Tier 3 fleet-health endpoint mounts under /agents/fleet-health
router.include_router(insights_fleet_health.router, prefix="/agents", tags=["agents"])
router.include_router(agent_groups.router, prefix="/agent_groups", tags=["agent_groups"])
router.include_router(agent_tasks.router, prefix="/tasks", tags=["tasks"])
router.include_router(tools.router, prefix="/tools", tags=["tools"])
router.include_router(connectors.router, prefix="/connectors", tags=["connectors"])
router.include_router(deployments.router, prefix="/deployments", tags=["deployments"])
router.include_router(analytics.router, prefix="/analytics", tags=["analytics"])
router.include_router(vector_stores.router, prefix="/vector_stores", tags=["vector_stores"])
router.include_router(datasets.router, prefix="/datasets", tags=["datasets"])
router.include_router(dataset_groups.router, prefix="/dataset-groups", tags=["dataset_groups"])
router.include_router(chat.router, prefix="/chat", tags=["chat"])
router.include_router(internal.router, prefix="/internal", tags=["internal"])
router.include_router(memories.router, prefix="/memories", tags=["memories"])
router.include_router(knowledge.router, prefix="/knowledge", tags=["knowledge"])
router.include_router(llm.router, prefix="/llm", tags=["llm"])
router.include_router(branding.router, prefix="/branding", tags=["branding"])
router.include_router(features.router, prefix="/features", tags=["features"])
router.include_router(tenant_analytics.router, prefix="/tenant-analytics", tags=["tenant-analytics"])
# Tier 2 cost dashboard: GET /insights/cost
router.include_router(insights_cost.router, prefix="/insights", tags=["insights"])
router.include_router(insights_coalition_replay.router, prefix="/insights", tags=["insights"])
router.include_router(integration_configs.router, prefix="/integration-configs", tags=["integration-configs"])
router.include_router(integrations.router, prefix="/integrations", tags=["integrations"])
router.include_router(skills_new.router, prefix="/skills", tags=["skills"])
router.include_router(mcp_bridge.router, prefix="/mcp", tags=["mcp"])
router.include_router(workflows.router, prefix="/workflows", tags=["workflows"])
router.include_router(remedia.router, prefix="/remedia", tags=["remedia"])
router.include_router(webhook_connectors.router, prefix="/webhook-connectors", tags=["webhook-connectors"])
router.include_router(webhooks.router, prefix="/webhooks", tags=["webhooks"])
router.include_router(mcp_server_connectors.router, prefix="/mcp-servers", tags=["mcp-servers"])
router.include_router(oauth.router, prefix="/oauth", tags=["oauth"])
router.include_router(claude_auth.router, prefix="/claude-auth", tags=["claude-auth"])
router.include_router(codex_auth.router, prefix="/codex-auth", tags=["codex-auth"])
router.include_router(gemini_cli_auth.router, prefix="/gemini-cli-auth", tags=["gemini-cli-auth"])
router.include_router(notifications.router, prefix="/notifications", tags=["notifications"])
router.include_router(reports.router, prefix="/reports", tags=["reports"])
router.include_router(rl.router, prefix="/rl", tags=["reinforcement-learning"])
router.include_router(local_ml.router, prefix="/local-ml", tags=["local-ml"])
router.include_router(dynamic_workflows.router, prefix="/dynamic-workflows", tags=["dynamic-workflows"])
router.include_router(safety.router, prefix="/safety", tags=["safety"])
router.include_router(goals.router, prefix="/goals", tags=["goals"])
router.include_router(commitments.router, prefix="/commitments", tags=["commitments"])
router.include_router(agent_identity.router, prefix="/agent-identity", tags=["agent-identity"])
router.include_router(world_state.router, prefix="/world-state", tags=["world-state"])
router.include_router(memory_admin.router, prefix="/internal/memory", tags=["internal"])
router.include_router(causal_edges.router, prefix="/causal-edges", tags=["causal-edges"])
router.include_router(plans.router, prefix="/plans", tags=["plans"])
router.include_router(blackboards.router, prefix="/blackboards", tags=["blackboards"])
router.include_router(collaborations.router, prefix="/collaborations", tags=["collaborations"])
router.include_router(coalitions.router, prefix="/coalitions", tags=["coalitions"])
router.include_router(learning.router, prefix="/learning", tags=["learning"])
router.include_router(learning_dashboard.router, prefix="/learning/dashboard", tags=["learning-dashboard"])
router.include_router(branding_domain.router, tags=["domain-branding"])
router.include_router(media.router, prefix="/media", tags=["media"])
router.include_router(audit.router, prefix="/audit", tags=["audit"])
router.include_router(external_agents.router, prefix="/external-agents", tags=["external-agents"])
router.include_router(agent_marketplace.router, prefix="/marketplace", tags=["marketplace"])
router.include_router(agent_tests.router, prefix="/agents", tags=["agent-tests"])
router.include_router(insights_resolver_metrics.router, prefix="/insights", tags=["insights"])

# Register optional modules that loaded successfully
_optional_routes = {
    "unsupervised_learning": {"prefix": "/unsupervised", "tags": ["unsupervised-learning"]},
    "presence": {"tags": ["presence"]},
    "activities": {"tags": ["activities"]},
    "devices": {"tags": ["devices"]},
    "robot": {"tags": ["robot"]},
    "session_journals": {"tags": ["session-journals"]},
    "memory_continuity_internal": {"prefix": "/internal", "tags": ["internal-memory"]},
    "sales": {"tags": ["sales"]},
}
for _name, _kwargs in _optional_routes.items():
    _mod = _loaded_optional.get(_name)
    if _mod and hasattr(_mod, "router"):
        router.include_router(_mod.router, **_kwargs)
