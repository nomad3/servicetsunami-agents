import logging
from fastapi import APIRouter
from app.api.v1 import (
    admin_tenant_health,
    audit,
    auth,
    insights_resolver_metrics,
    spatial_orchestration,
    channels,
    data_sources,
    data_pipelines,
    notebooks,
    agents,
    agent_groups,
    agent_tasks,
    tasks_fanout,
    tools,
    connectors,
    deployments,
    analytics,
    vector_stores,
    datasets,
    chat,
    internal,
    emotion,
    habits,
    metacog,
    reflections,
    memories,
    team,
    memory_remember,
    agent_policies,
    knowledge,
    llm,
    branding,
    features,
    tenant_analytics,
    dataset_groups,
    integration_configs,
    integrations,
    users,
    workspace,
    gesture_dispatch,
    fleet,
    skills_new,
    skill_evals,
    mcp_bridge,
    workflows,
    remedia,
    webhook_connectors,
    webhooks,
    twilio_webhook,
    mcp_server_connectors,
    oauth,
    claude_auth,
    codex_auth,
    gemini_cli_auth,
    higgsfield_auth,
    notifications,
    reports,
    bookkeeper_exports,
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
    reviews,
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
    metrics,
    internal_orchestrator_events,
    internal_agent_tokens,
    internal_agent_heartbeat,
    internal_agent_tasks,
    internal_embed,
    agent_tokens,
    onboarding,
    memory_training,
    mcp_public,
    usage_costs,
    dashboard_tasks,
    luna_impact,
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
# Workspace file-tree navigator — left-panel Files mode in the dashboard.
# Routes already include `/workspace/` internally, so mount at root.
router.include_router(workspace.router, prefix="", tags=["workspace"])
router.include_router(gesture_dispatch.router, tags=["gestures"])
router.include_router(fleet.router, prefix="/fleet", tags=["fleet"])
router.include_router(data_sources.router, prefix="/data_sources", tags=["data_sources"])
router.include_router(data_pipelines.router, prefix="/data_pipelines", tags=["data_pipelines"])
router.include_router(notebooks.router, prefix="/notebooks", tags=["notebooks"])
# IMPORTANT: insights_fleet_health MUST mount before agents.router. Both
# share the /agents prefix, and agents.router has GET /{agent_id} which
# would otherwise match "fleet-health" as a UUID path param and respond
# with 422. FastAPI matches in registration order — specific first.
router.include_router(insights_fleet_health.router, prefix="/agents", tags=["agents"])
router.include_router(agents.router, prefix="/agents", tags=["agents"])
router.include_router(agent_groups.router, prefix="/agent_groups", tags=["agent_groups"])
router.include_router(agent_tasks.router, prefix="/tasks", tags=["tasks"])
router.include_router(
    tasks_fanout.router,
    prefix="/tasks-fanout",
    tags=["tasks-fanout (prototype)"],
)
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
# Emotion engine — affect observability + (PR C) PAD-injection state.
# Mounted at /api/v1 with no prefix so paths like /sessions/{id}/affect-trace
# land under the session namespace, not /memories.
router.include_router(emotion.router, prefix="", tags=["emotion"])
# Teamwork Engine — role contracts + norms observability endpoints.
# Empty prefix so paths like /team/roles + /team/norms land at the
# tenant-scoped /api/v1/team namespace.
router.include_router(team.router, prefix="", tags=["team"])
# Metacognition (M3 of #616) — calibration endpoint that powers the
# ECE dashboard. Empty prefix so GET /metacog/calibration lands at the
# tenant-scoped /api/v1/metacog namespace per the canonical design.
router.include_router(metacog.router, prefix="", tags=["metacog"])
router.include_router(reflections.router, prefix="", tags=["reflections"])
# Habit observation ingestion (#297) — Tauri client writes vision-derived
# signals via the internal POST endpoint (X-Internal-Key + X-Tenant-Id auth).
router.include_router(habits.router, prefix="", tags=["habits"])
# `alpha usage` + `alpha costs` (Phase 4 of the CLI roadmap, #181).
# Aggregates chat_messages per-provider and per-day for the tenant.
# Mounted at root (no prefix) so the routes read `/usage` and
# `/costs` matching the roadmap doc verbatim.
router.include_router(usage_costs.router, tags=["usage-costs"])
# Dashboard rollup for `alpha tasks` — cross-machine view of working
# + recently-completed workflow runs. Mounted under /dashboard
# because the v1 root already has /tasks claimed by agent_tasks
# (orchestration-internal AgentTask records).
router.include_router(dashboard_tasks.router, prefix="/dashboard", tags=["dashboard"])
# Luna-impact baseline dashboard (#327) — single tenant-scoped endpoint
# aggregating Layer-1 measurable signals (stability / routing / affect /
# coordination / metacog). Canonical doc:
# docs/plans/2026-05-20-luna-metacognition-and-dreams-canonical.md §6.
router.include_router(luna_impact.router, prefix="/luna", tags=["luna-impact"])
# `alpha remember` (Phase 2 of the CLI roadmap, #179) — free-form
# fact ingestion via knowledge.create_observation.
router.include_router(memory_remember.router, prefix="/memory", tags=["memory"])
# `alpha policy show` (Phase 2 of the CLI roadmap, #179) — read-only
# inspection of agent_policies. Mounted after agents.router; verified
# no `/{agent_id}/policies` route exists there, so registration order
# is moot. Kept here for OpenAPI tag grouping.
router.include_router(agent_policies.router, prefix="/agents", tags=["agents"])
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
# Skill-creator framework — Phase 1 grader endpoint. Mounted under /skills
# so the URL reads `/api/v1/skills/{skill_id}/evals/grade` per the design
# doc. Phases 2-7 add the rest of the authoring surface.
router.include_router(skill_evals.router, prefix="/skills", tags=["skill-evals"])
router.include_router(mcp_bridge.router, prefix="/mcp", tags=["mcp"])
router.include_router(workflows.router, prefix="/workflows", tags=["workflows"])
router.include_router(remedia.router, prefix="/remedia", tags=["remedia"])
router.include_router(webhook_connectors.router, prefix="/webhook-connectors", tags=["webhook-connectors"])
router.include_router(webhooks.router, prefix="/webhooks", tags=["webhooks"])
# Twilio SMS — webhook + internal send/list/read (mounted at root because the
# Twilio Console wants the public URL to be /api/v1/integrations/twilio/inbound,
# which already matches the route declared in twilio_webhook.py).
router.include_router(twilio_webhook.router, tags=["twilio-sms"])
router.include_router(mcp_server_connectors.router, prefix="/mcp-servers", tags=["mcp-servers"])
router.include_router(oauth.router, prefix="/oauth", tags=["oauth"])
router.include_router(claude_auth.router, prefix="/claude-auth", tags=["claude-auth"])
router.include_router(codex_auth.router, prefix="/codex-auth", tags=["codex-auth"])
router.include_router(gemini_cli_auth.router, prefix="/gemini-cli-auth", tags=["gemini-cli-auth"])
# Higgsfield creative-content MCP source — Wave 1a of the CLI catalog
# (#270). Mirrors the gemini-cli OAuth flow: api-owned PKCE + paste-back
# code exchange. The resulting OAuth blob is stored in the vault and
# fans out to a per-tenant MCP server registration that the Marketing/
# Sales specialist agent picks up via the standard discover_mcp_tools
# path.
router.include_router(higgsfield_auth.router, prefix="/higgsfield-auth", tags=["higgsfield-auth"])
router.include_router(notifications.router, prefix="/notifications", tags=["notifications"])
router.include_router(reports.router, prefix="/reports", tags=["reports"])
router.include_router(
    bookkeeper_exports.router,
    prefix="/bookkeeper-exports",
    tags=["bookkeeper-exports"],
)
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
router.include_router(reviews.router, prefix="/reviews", tags=["reviews"])
router.include_router(learning.router, prefix="/learning", tags=["learning"])
router.include_router(learning_dashboard.router, prefix="/learning/dashboard", tags=["learning-dashboard"])
router.include_router(branding_domain.router, tags=["domain-branding"])
router.include_router(media.router, prefix="/media", tags=["media"])
router.include_router(audit.router, prefix="/audit", tags=["audit"])
router.include_router(external_agents.router, prefix="/external-agents", tags=["external-agents"])
router.include_router(agent_marketplace.router, prefix="/marketplace", tags=["marketplace"])
router.include_router(agent_tests.router, prefix="/agents", tags=["agent-tests"])
router.include_router(insights_resolver_metrics.router, prefix="/insights", tags=["insights"])
router.include_router(spatial_orchestration.router, prefix="/spatial", tags=["spatial"])
router.include_router(admin_tenant_health.router, prefix="/admin", tags=["admin"])
# Phase 3 — Prometheus exposition (internal-key gated, scraped in-cluster).
router.include_router(metrics.router, tags=["metrics"])
# Phase 3 commit 8 — heartbeat-missed event ingestion (worker-side emit).
router.include_router(
    internal_orchestrator_events.router,
    prefix="/internal", tags=["internal"],
)
# Phase 4 commit 5 — agent-token mint endpoint (worker-side mint).
router.include_router(
    internal_agent_tokens.router,
    prefix="/internal", tags=["internal"],
)
# PR-E — user-scoped agent-token mint (no /internal prefix, Bearer-auth).
# This is the public-internet-reachable sibling that powers `alpha claude-code`,
# `alpha codex`, etc. — multi-runtime dispatch from the user's terminal.
router.include_router(agent_tokens.router, tags=["agent-tokens"])
# PR-Q0 — tenant onboarding state. Powers ap-quickstart and the web
# /onboarding/* route guard's auto-trigger on first login.
router.include_router(onboarding.router, tags=["onboarding"])
# PR-Q1 — initial-training bulk-ingest endpoint + TrainingIngestionWorkflow
# dispatch. Hot path that alpha quickstart hits after the wedge picker
# completes (POST /memory/training/bulk-ingest with the source items).
router.include_router(memory_training.router, tags=["memory-training"])
# Public MCP gateway (Phase 1 of #175) — JWT-auth wrapper that
# forwards SSE + JSON-RPC POSTs to the in-cluster mcp-tools server,
# so external MCP clients (Claude.ai, custom integrations) can
# connect to a tenant's tool surface without holding the shared
# X-Internal-Key. Mounted under `/mcp` (NOT `/internal`) so
# cloudflared lets the traffic through to /api/v1/mcp/*.
router.include_router(mcp_public.router, prefix="/mcp", tags=["mcp-public"])
# Internal embedding endpoint — replaces sentence-transformers in apps/mcp-server.
router.include_router(
    internal_embed.router,
    prefix="/internal", tags=["internal"],
)
# Phase 4 commit 8 — leaf-side heartbeat endpoint. Path is
# /api/v1/agents/internal/heartbeat per design §10.3(c) — declared in
# the router itself with the full path so we mount at the v1 root.
router.include_router(
    internal_agent_heartbeat.router,
    tags=["internal"],
)
# Phase 4 review fix — request-approval endpoint backing the
# request_human_approval MCP tool. Path is
# /api/v1/tasks/internal/{task_id}/request-approval declared in the
# router itself so we mount at the v1 root.
router.include_router(
    internal_agent_tasks.router,
    tags=["internal"],
)

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
