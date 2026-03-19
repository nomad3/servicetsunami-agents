from fastapi import APIRouter
from app.api.v1 import (
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
    oauth,
    notifications,
    reports,
    rl,
    branding_domain,
)

router = APIRouter()

@router.get("/")
def read_root():
    return {"message": "ServiceTsunami API"}

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
router.include_router(oauth.router, prefix="/oauth", tags=["oauth"])
router.include_router(notifications.router, prefix="/notifications", tags=["notifications"])
router.include_router(reports.router, prefix="/reports", tags=["reports"])
router.include_router(rl.router, prefix="/rl", tags=["reinforcement-learning"])
router.include_router(branding_domain.router, tags=["domain-branding"])
