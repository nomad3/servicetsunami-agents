from fastapi import APIRouter
from app.api.v1 import (
    activities,
    auth,
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
    agent_kits,
    datasets,
    chat,
    databricks,
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
    workflows,
    remedia,
    webhook_connectors,
    webhooks,
    mcp_server_connectors,
    oauth,
    codex_auth,
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
    causal_edges,
    plans,
    blackboards,
    collaborations,
    coalitions,
    learning,
    learning_dashboard,
    branding_domain,
    unsupervised_learning,
    presence,
    devices,
    robot,
)

router = APIRouter()

@router.get("/")
def read_root():
    return {"message": "agentprovision.com API"}

router.include_router(auth.router, prefix="/auth", tags=["auth"])
router.include_router(channels.router, prefix="/channels", tags=["channels"])
router.include_router(users.router, prefix="/users", tags=["users"])
router.include_router(data_sources.router, prefix="/data_sources", tags=["data_sources"])
router.include_router(data_pipelines.router, prefix="/data_pipelines", tags=["data_pipelines"])
router.include_router(notebooks.router, prefix="/notebooks", tags=["notebooks"])
router.include_router(agents.router, prefix="/agents", tags=["agents"])
router.include_router(agent_groups.router, prefix="/agent_groups", tags=["agent_groups"])
router.include_router(agent_tasks.router, prefix="/tasks", tags=["tasks"])
router.include_router(tools.router, prefix="/tools", tags=["tools"])
router.include_router(connectors.router, prefix="/connectors", tags=["connectors"])
router.include_router(deployments.router, prefix="/deployments", tags=["deployments"])
router.include_router(analytics.router, prefix="/analytics", tags=["analytics"])
router.include_router(vector_stores.router, prefix="/vector_stores", tags=["vector_stores"])
router.include_router(agent_kits.router, prefix="/agent-kits", tags=["agent_kits"])
router.include_router(datasets.router, prefix="/datasets", tags=["datasets"])
router.include_router(dataset_groups.router, prefix="/dataset-groups", tags=["dataset_groups"])
router.include_router(chat.router, prefix="/chat", tags=["chat"])
router.include_router(databricks.router, prefix="/databricks", tags=["databricks"])
router.include_router(internal.router, prefix="/internal", tags=["internal"])
router.include_router(memories.router, prefix="/memories", tags=["memories"])
router.include_router(knowledge.router, prefix="/knowledge", tags=["knowledge"])
router.include_router(llm.router, prefix="/llm", tags=["llm"])
router.include_router(branding.router, prefix="/branding", tags=["branding"])
router.include_router(features.router, prefix="/features", tags=["features"])
router.include_router(tenant_analytics.router, prefix="/tenant-analytics", tags=["tenant-analytics"])
router.include_router(integration_configs.router, prefix="/integration-configs", tags=["integration-configs"])
router.include_router(integrations.router, prefix="/integrations", tags=["integrations"])
router.include_router(skills_new.router, prefix="/skills", tags=["skills"])
router.include_router(workflows.router, prefix="/workflows", tags=["workflows"])
router.include_router(remedia.router, prefix="/remedia", tags=["remedia"])
router.include_router(webhook_connectors.router, prefix="/webhook-connectors", tags=["webhook-connectors"])
router.include_router(webhooks.router, prefix="/webhooks", tags=["webhooks"])
router.include_router(mcp_server_connectors.router, prefix="/mcp-servers", tags=["mcp-servers"])
router.include_router(oauth.router, prefix="/oauth", tags=["oauth"])
router.include_router(codex_auth.router, prefix="/codex-auth", tags=["codex-auth"])
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
router.include_router(causal_edges.router, prefix="/causal-edges", tags=["causal-edges"])
router.include_router(plans.router, prefix="/plans", tags=["plans"])
router.include_router(blackboards.router, prefix="/blackboards", tags=["blackboards"])
router.include_router(collaborations.router, prefix="/collaborations", tags=["collaborations"])
router.include_router(coalitions.router, prefix="/coalitions", tags=["coalitions"])
router.include_router(learning.router, prefix="/learning", tags=["learning"])
router.include_router(learning_dashboard.router, prefix="/learning/dashboard", tags=["learning-dashboard"])
router.include_router(unsupervised_learning.router, prefix="/unsupervised", tags=["unsupervised-learning"])
router.include_router(branding_domain.router, tags=["domain-branding"])
router.include_router(presence.router, tags=["presence"])
router.include_router(activities.router, tags=["activities"])
router.include_router(devices.router, tags=["devices"])
router.include_router(robot.router, tags=["robot"])
