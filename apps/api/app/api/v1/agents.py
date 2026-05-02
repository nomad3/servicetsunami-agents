import logging
import uuid
from datetime import datetime, timedelta
from typing import List, Optional

from fastapi import APIRouter, Depends, Header, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app import schemas
from app.api import deps
from app.core.config import settings
from app.models.agent import Agent
from app.models.agent_audit_log import AgentAuditLog
from app.models.agent_integration_config import AgentIntegrationConfig
from app.models.agent_performance_snapshot import AgentPerformanceSnapshot
from app.models.agent_version import AgentVersion
from app.models.integration_config import IntegrationConfig
from app.models.user import User
from app.schemas.audit import AuditLogEntry
from app.services import agents as agent_service
from app.services.agent_importer import parse_agent_definition
from app.services.agent_registry import registry
from app.services.audit_log import write_audit_log

logger = logging.getLogger(__name__)

router = APIRouter()

# Status transition order for promote
_PROMOTE_TRANSITIONS = {
    "draft": "staging",
    "staging": "production",
}


def _verify_internal_key_dep(
    x_internal_key: Optional[str] = Header(None, alias="X-Internal-Key"),
):
    """Internal key gate for MCP-driven endpoints."""
    valid = (settings.API_INTERNAL_KEY, settings.MCP_API_KEY)
    if x_internal_key not in valid:
        raise HTTPException(status_code=401, detail="Invalid internal key")


# Top-level Agent fields safe to patch from chat. Anything else is rejected
# so a chat-side edit can't reassign owner_user_id, status, version, etc.
_AGENT_TOPLEVEL_PATCHABLE = {
    "description",
    "persona_prompt",
    "tool_groups",
    "default_model_tier",
    "autonomy_level",
}

# Inside agent.config, only these keys can be rewritten from chat. Keeps
# accidental schema drift from getting baked in.
_AGENT_CONFIG_PATCHABLE = {
    "system_prompt",
    "temperature",
    "max_tokens",
    "skills",
}


class InternalAgentConfigPatch(BaseModel):
    """Chat-side patch payload for an Agent.

    ``actor_user_id`` and ``tenant_id`` are passed in the body — the MCP
    layer reads them from request headers (``X-User-Id`` / ``X-Tenant-Id``)
    and forwards them. The internal-key dep guards against unauthorized
    callers; tenant scoping is enforced below.
    """

    tenant_id: str
    actor_user_id: Optional[str] = None
    reason: Optional[str] = None
    updates: dict


@router.post("/internal/{agent_id}/update-config", response_model=schemas.agent.Agent)
def update_agent_config_internal(
    agent_id: uuid.UUID,
    payload: InternalAgentConfigPatch,
    db: Session = Depends(deps.get_db),
    _auth: None = Depends(_verify_internal_key_dep),
):
    """Patch a subset of an agent's config from chat-side MCP tools.

    Mutates only the allowlisted top-level + config keys, records a
    library_revisions row, and returns the updated agent. Caller is
    responsible for validating that ``actor_user_id`` actually maps to a
    user with edit rights on this agent — for now we trust the MCP shim
    because it's behind the internal-key boundary.
    """
    from app.services.library_revisions import record_revision

    agent = agent_service.get_agent(db, agent_id=agent_id)
    if not agent or str(agent.tenant_id) != payload.tenant_id:
        raise HTTPException(status_code=404, detail="Agent not found in tenant.")

    rejected = set(payload.updates.keys()) - (_AGENT_TOPLEVEL_PATCHABLE | {"config"})
    if rejected:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Cannot patch keys from chat: {sorted(rejected)}. "
                f"Allowed top-level: {sorted(_AGENT_TOPLEVEL_PATCHABLE)}; "
                f"config subset: {sorted(_AGENT_CONFIG_PATCHABLE)}."
            ),
        )

    config_updates = payload.updates.get("config") or {}
    if config_updates:
        rejected_cfg = set(config_updates.keys()) - _AGENT_CONFIG_PATCHABLE
        if rejected_cfg:
            raise HTTPException(
                status_code=400,
                detail=f"Cannot patch config keys from chat: {sorted(rejected_cfg)}.",
            )

    before_value = {
        "description": agent.description,
        "persona_prompt": agent.persona_prompt,
        "tool_groups": agent.tool_groups,
        "default_model_tier": agent.default_model_tier,
        "autonomy_level": agent.autonomy_level,
        "config": dict(agent.config or {}),
    }

    for key in _AGENT_TOPLEVEL_PATCHABLE:
        if key in payload.updates:
            setattr(agent, key, payload.updates[key])

    if config_updates:
        merged = dict(agent.config or {})
        merged.update(config_updates)
        agent.config = merged

    db.add(agent)
    db.commit()
    db.refresh(agent)

    after_value = {
        "description": agent.description,
        "persona_prompt": agent.persona_prompt,
        "tool_groups": agent.tool_groups,
        "default_model_tier": agent.default_model_tier,
        "autonomy_level": agent.autonomy_level,
        "config": dict(agent.config or {}),
    }

    actor_uuid = None
    if payload.actor_user_id:
        try:
            actor_uuid = uuid.UUID(payload.actor_user_id)
        except (ValueError, TypeError):
            actor_uuid = None

    record_revision(
        db,
        tenant_id=uuid.UUID(payload.tenant_id),
        target_type="agent",
        target_ref=str(agent.id),
        actor_user_id=actor_uuid,
        reason=payload.reason,
        before_value=before_value,
        after_value=after_value,
    )
    return agent

@router.get("", response_model=List[schemas.agent.Agent])
def read_agents(
    db: Session = Depends(deps.get_db),
    skip: int = 0,
    limit: int = 100,
    current_user: User = Depends(deps.get_current_active_user),
):
    """
    Retrieve agents for the current tenant.
    """
    agents = agent_service.get_agents_by_tenant(
        db, tenant_id=current_user.tenant_id, skip=skip, limit=limit
    )
    return agents


@router.post("", response_model=schemas.agent.Agent, status_code=status.HTTP_201_CREATED)
def create_agent(
    *,
    db: Session = Depends(deps.get_db),
    item_in: schemas.agent.AgentCreate,
    current_user: User = Depends(deps.get_current_active_user),
):
    """
    Create new agent for the current tenant.
    """
    item = agent_service.create_tenant_agent(db=db, item_in=item_in, tenant_id=current_user.tenant_id)
    item.owner_user_id = current_user.id
    db.commit()
    db.refresh(item)
    return item

class InternalDelegateRequest(BaseModel):
    """Chat-side handoff payload routed through Dynamic Workflows.

    ``actor_user_id`` and ``tenant_id`` come from MCP request headers
    (X-User-Id / X-Tenant-Id) and are forwarded by the MCP tool. The
    internal-key dep is the trust boundary; tenant scoping is enforced
    when we look up the recipient.
    """

    tenant_id: str
    recipient_agent_id: str
    task: str
    reason: Optional[str] = None
    actor_user_id: Optional[str] = None
    chat_session_id: Optional[str] = None


@router.post("/internal/delegate")
async def delegate_to_agent_internal(
    payload: InternalDelegateRequest,
    db: Session = Depends(deps.get_db),
    _auth: None = Depends(_verify_internal_key_dep),
):
    """Launch the 'Delegate To Agent' Dynamic Workflow and (optionally)
    drop a ``[handoff]`` chat message into the originating session.

    Returns the run id so the caller can poll. Audit + replay come from
    WorkflowRun + WorkflowStepLog — no separate ``agent_messages`` table.
    """
    from app.services.dynamic_workflow_launcher import start_dynamic_workflow
    from app.models.chat import ChatMessage

    # Tenant scoping: the recipient must belong to the requesting tenant.
    # We accept either a native Agent.id or an ExternalAgent.id; the
    # workflow's `agent` step resolves both via the platform's normal
    # dispatch path.
    tenant_uuid = uuid.UUID(payload.tenant_id)
    recipient_uuid = uuid.UUID(payload.recipient_agent_id)

    native = (
        db.query(Agent)
        .filter(Agent.id == recipient_uuid, Agent.tenant_id == tenant_uuid)
        .first()
    )
    if native is None:
        from app.models.external_agent import ExternalAgent
        ext = (
            db.query(ExternalAgent)
            .filter(ExternalAgent.id == recipient_uuid, ExternalAgent.tenant_id == tenant_uuid)
            .first()
        )
        if ext is None:
            raise HTTPException(status_code=404, detail="Recipient agent not found in tenant.")
        recipient_name = ext.name
    else:
        recipient_name = native.name

    temporal_wf_id = await start_dynamic_workflow(
        db=db,
        template_name="Delegate To Agent",
        tenant_id=tenant_uuid,
        input_data={
            "recipient_agent_id": str(recipient_uuid),
            "task": payload.task,
            "reason": payload.reason or "",
        },
        workflow_id_prefix="delegate",
    )

    # Optional in-chat surface: drop a [handoff] system message into the
    # originating session so the user sees "→ Handoff to {Agent} (run #abc)"
    # inline. The chat UI's CollaborationPanel doesn't open for this —
    # 1-step handoffs are lightweight.
    if payload.chat_session_id:
        try:
            session_uuid = uuid.UUID(payload.chat_session_id)
            short = temporal_wf_id.rsplit("-", 1)[-1]
            handoff_msg = ChatMessage(
                session_id=session_uuid,
                role="system",
                content=f"→ Handoff to {recipient_name} (run #{short})",
                context={
                    "kind": "handoff",
                    "run_id": temporal_wf_id,
                    "recipient_agent_id": str(recipient_uuid),
                    "recipient_name": recipient_name,
                    "reason": payload.reason or "",
                },
            )
            db.add(handoff_msg)
            db.flush()
        except Exception as exc:
            logger.warning("delegate_to_agent_internal: handoff message write failed: %s", exc)

    db.commit()
    return {
        "run_id": temporal_wf_id,
        "recipient_agent_id": str(recipient_uuid),
        "recipient_name": recipient_name,
    }


@router.get("/internal/handoff/{run_id}/status")
def handoff_status_internal(
    run_id: str,
    tenant_id: str,
    db: Session = Depends(deps.get_db),
    _auth: None = Depends(_verify_internal_key_dep),
):
    """Read the WorkflowRun + step logs for a handoff. Returns the
    delegate step's output once the run completes.
    """
    from app.models.dynamic_workflow import WorkflowRun, WorkflowStepLog
    run = (
        db.query(WorkflowRun)
        .filter(WorkflowRun.temporal_workflow_id == run_id, WorkflowRun.tenant_id == uuid.UUID(tenant_id))
        .first()
    )
    if run is None:
        raise HTTPException(status_code=404, detail=f"Run '{run_id}' not found.")

    step_logs = (
        db.query(WorkflowStepLog)
        .filter(WorkflowStepLog.run_id == run.id)
        .order_by(WorkflowStepLog.started_at)
        .all()
    )
    delegate_step = next((s for s in step_logs if s.step_id == "delegate"), None)

    return {
        "run_id": run_id,
        "status": run.status,
        "started_at": run.started_at.isoformat() if run.started_at else None,
        "completed_at": run.completed_at.isoformat() if run.completed_at else None,
        "reply": (delegate_step.output_data if delegate_step else None),
        "error": run.error,
    }


def _discover_payload(matches, kind_filter: Optional[str]):
    if kind_filter in ("native", "external"):
        matches = [(k, a) for k, a in matches if k == kind_filter]
    return [
        {
            "kind": k,
            "id": str(a.id),
            "name": a.name,
            "description": a.description,
            "status": a.status,
            "capabilities": a.capabilities,
        }
        for k, a in matches
    ]


@router.get("/internal/discover")
def discover_agents_internal(
    capability: str,
    tenant_id: str,
    kind: Optional[str] = None,
    db: Session = Depends(deps.get_db),
    _auth: None = Depends(_verify_internal_key_dep),
):
    """Internal-key variant of /discover for MCP-side find_agent tool.

    Tenant scoping is forwarded as a query param since the internal-key
    boundary doesn't carry user identity.
    """
    matches = registry.find_by_capability(capability, uuid.UUID(tenant_id), db)
    return _discover_payload(matches, kind)


@router.get("/discover")
def discover_agents(
    capability: str,
    max_latency_ms: Optional[int] = None,
    kind: Optional[str] = None,
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(deps.get_current_active_user),
):
    """Discover native + external agents that declared ``capability``.

    The response includes a ``kind`` discriminator (``"native"`` or
    ``"external"``) so callers can route through the right dispatch path.
    Optional ``kind`` query param filters to one side.
    """
    matches = registry.find_by_capability(capability, current_user.tenant_id, db)
    return _discover_payload(matches, kind)


@router.get("/microsoft/discover")
async def discover_microsoft_agents(
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(deps.get_current_active_user),
):
    """Enumerate Copilot Studio + Azure AI Foundry agents the tenant owns.

    Closes the loop on PR #243's "paste JSON" import flow — the UI can
    show a list of discovered agents and let the user one-click import
    any subset. The returned ``raw`` payload per agent is compatible
    with ``parse_agent_definition`` so import is a single call.

    Pre-condition: the tenant has authorized the ``microsoft`` OAuth
    provider (typically via Outlook or Teams). Same access token is
    reused — no separate consent flow.

    Response shape:
        {
          "agents": [
            {"kind": "copilot_studio", "id": "...", "display_name": "...",
             "description": "...", "raw": {...}},
            {"kind": "ai_foundry", ...},
          ],
          "count": N,
          "reason": null | "not_connected" | "no_agents_found" | "partial_failure",
          "errors": [...] | null
        }
    """
    from app.services.microsoft_agent_discovery import discover
    return await discover(db, current_user.tenant_id)


@router.post("/import", response_model=schemas.agent.Agent, status_code=status.HTTP_201_CREATED)
def import_agent(
    *,
    db: Session = Depends(deps.get_db),
    body: schemas.agent.AgentImportRequest,
    current_user: User = Depends(deps.get_current_active_user),
):
    parsed = parse_agent_definition(body.content, body.filename)
    item_in = schemas.agent.AgentCreate(
        name=parsed.get("name", "Imported Agent"),
        description=parsed.get("description"),
        persona_prompt=parsed.get("persona_prompt"),
        capabilities=parsed.get("capabilities") or [],
        config=parsed.get("config"),
        status="draft",
    )
    item = agent_service.create_tenant_agent(db=db, item_in=item_in, tenant_id=current_user.tenant_id)
    item.owner_user_id = current_user.id
    db.commit()
    db.refresh(item)
    return item
@router.get("/{agent_id}", response_model=schemas.agent.Agent)
def read_agent_by_id(
    agent_id: uuid.UUID,
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(deps.get_current_active_user),
):
    """
    Retrieve a specific agent by ID for the current tenant.
    """
    agent = agent_service.get_agent(db, agent_id=agent_id)
    if not agent or str(agent.tenant_id) != str(current_user.tenant_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Agent not found")
    return agent

@router.put("/{agent_id}", response_model=schemas.agent.Agent)
def update_agent(
    *,
    db: Session = Depends(deps.get_db),
    agent_id: uuid.UUID,
    item_in: schemas.agent.AgentCreate,
    current_user: User = Depends(deps.get_current_active_user),
):
    """
    Update an existing agent for the current tenant.
    """
    agent = agent_service.get_agent(db, agent_id=agent_id)
    if not agent or str(agent.tenant_id) != str(current_user.tenant_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Agent not found")
    item = agent_service.update_agent(db=db, db_obj=agent, obj_in=item_in)
    return item

@router.delete("/{agent_id}", status_code=status.HTTP_200_OK)
def delete_agent(
    *,
    db: Session = Depends(deps.get_db),
    agent_id: uuid.UUID,
    current_user: User = Depends(deps.get_current_active_user),
):
    """
    Delete an agent for the current tenant.
    """
    agent = agent_service.get_agent(db, agent_id=agent_id)
    if not agent or str(agent.tenant_id) != str(current_user.tenant_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Agent not found")
    agent_service.delete_agent(db=db, agent_id=agent_id)
    return {"deleted": True}


@router.post("/{agent_id}/promote", response_model=schemas.agent.Agent)
def promote_agent(
    *,
    db: Session = Depends(deps.get_db),
    body: schemas.agent.AgentPromoteRequest,
    current_user: User = Depends(deps.get_current_active_user),
    agent: Agent = Depends(deps.require_agent_permission("promote")),
):
    next_status = _PROMOTE_TRANSITIONS.get(agent.status)
    if next_status is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Cannot promote agent with status '{agent.status}'",
        )

    # Promotion gate: if the agent has enabled test cases, they must pass before
    # we transition the lifecycle state. Running the suite against an empty set
    # short-circuits (no regression gate to enforce).
    if body.skip_tests:
        # Auditable bypass — governance needs to see who skipped the regression gate.
        write_audit_log(
            agent_id=agent.id,
            tenant_id=agent.tenant_id,
            invoked_by_user_id=current_user.id,
            invocation_type="lifecycle",
            status="promote_bypass_gate",
        )
    else:
        from app.models.agent_test_suite import AgentTestCase
        from app.services import agent_test_runner

        has_cases = (
            db.query(AgentTestCase)
            .filter(
                AgentTestCase.agent_id == agent.id,
                AgentTestCase.tenant_id == agent.tenant_id,
                AgentTestCase.enabled.is_(True),
            )
            .count()
        )
        if has_cases > 0:
            test_run = agent_test_runner.run_test_suite(
                db,
                agent_id=agent.id,
                tenant_id=agent.tenant_id,
                triggered_by_user_id=current_user.id,
                run_type="promotion_gate",
            )
            if test_run.status != "passed":
                raise HTTPException(
                    status_code=status.HTTP_412_PRECONDITION_FAILED,
                    detail={
                        "message": "Promotion blocked by failing test cases",
                        "test_run_id": str(test_run.id),
                        "passed": test_run.passed_count,
                        "failed": test_run.failed_count,
                        "results": test_run.results,
                    },
                )

    # Snapshot current config before transitioning
    config_snapshot = {
        "name": agent.name,
        "description": agent.description,
        "status": agent.status,
        "persona_prompt": agent.persona_prompt,
        "capabilities": agent.capabilities,
        "tool_groups": agent.tool_groups,
        "config": agent.config,
    }

    version_record = AgentVersion(
        agent_id=agent.id,
        tenant_id=agent.tenant_id,
        version=agent.version,
        config_snapshot=config_snapshot,
        promoted_by=current_user.id,
        promoted_at=datetime.utcnow(),
        status=next_status,
        notes=body.notes,
    )
    db.add(version_record)

    agent.status = next_status
    agent.version = agent.version + 1
    db.commit()
    db.refresh(agent)
    write_audit_log(
        agent_id=agent.id,
        tenant_id=agent.tenant_id,
        invoked_by_user_id=current_user.id,
        invocation_type="lifecycle",
        status=f"promoted_to_{next_status}",
    )
    return agent


@router.post("/{agent_id}/deprecate", response_model=schemas.agent.Agent)
def deprecate_agent(
    *,
    db: Session = Depends(deps.get_db),
    body: schemas.agent.AgentDeprecateRequest,
    current_user: User = Depends(deps.get_current_active_user),
    agent: Agent = Depends(deps.require_agent_permission("deprecate")),
):
    if agent.status == "deprecated":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Agent is already deprecated",
        )

    if body.successor_agent_id:
        successor = db.query(Agent).filter(Agent.id == body.successor_agent_id).first()
        if not successor or str(successor.tenant_id) != str(agent.tenant_id):
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Successor agent not found",
            )
        agent.successor_agent_id = successor.id

    agent.status = "deprecated"
    db.commit()
    db.refresh(agent)
    write_audit_log(
        agent_id=agent.id,
        tenant_id=agent.tenant_id,
        invoked_by_user_id=current_user.id,
        invocation_type="lifecycle",
        status="deprecated",
    )
    return agent


@router.get("/{agent_id}/versions", response_model=List[schemas.agent.AgentVersionResponse])
def list_agent_versions(
    agent_id: uuid.UUID,
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(deps.get_current_active_user),
):
    agent = db.query(Agent).filter(Agent.id == agent_id).first()
    if not agent or str(agent.tenant_id) != str(current_user.tenant_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Agent not found")

    versions = (
        db.query(AgentVersion)
        .filter(AgentVersion.agent_id == agent_id)
        .order_by(AgentVersion.version.desc())
        .all()
    )
    return versions


@router.post("/{agent_id}/versions/{version_num}/rollback", response_model=schemas.agent.Agent)
def rollback_agent_version(
    version_num: int,
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(deps.get_current_active_user),
    agent: Agent = Depends(deps.require_agent_permission("promote")),
):
    target = (
        db.query(AgentVersion)
        .filter(AgentVersion.agent_id == agent.id, AgentVersion.version == version_num)
        .first()
    )
    if not target:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Version not found")

    snapshot = target.config_snapshot
    agent.name = snapshot.get("name", agent.name)
    agent.description = snapshot.get("description", agent.description)
    agent.status = snapshot.get("status", "production")
    for field in ("persona_prompt", "capabilities", "tool_groups", "config"):
        if field in snapshot and hasattr(agent, field):
            setattr(agent, field, snapshot[field])

    live_version = (
        db.query(AgentVersion)
        .filter(AgentVersion.agent_id == agent.id, AgentVersion.version == agent.version)
        .first()
    )
    if live_version:
        live_version.status = "rolled_back"

    new_version_num = agent.version + 1
    rollback_record = AgentVersion(
        agent_id=agent.id,
        tenant_id=agent.tenant_id,
        version=new_version_num,
        config_snapshot=snapshot,
        status="production",
        notes=f"Rollback to version {version_num}",
        promoted_by=current_user.id,
        promoted_at=datetime.utcnow(),
    )
    db.add(rollback_record)
    agent.version = new_version_num
    db.commit()
    db.refresh(agent)
    return agent


@router.get("/{agent_id}/versions/{version_num}/diff")
def diff_agent_version(
    agent_id: uuid.UUID,
    version_num: int,
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(deps.get_current_active_user),
):
    agent = db.query(Agent).filter(Agent.id == agent_id).first()
    if not agent or str(agent.tenant_id) != str(current_user.tenant_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Agent not found")

    target = (
        db.query(AgentVersion)
        .filter(AgentVersion.agent_id == agent_id, AgentVersion.version == version_num)
        .first()
    )
    if not target:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Version not found")

    snapshot = target.config_snapshot
    current_values = {
        "name": agent.name,
        "description": agent.description,
        "status": agent.status,
        "persona_prompt": agent.persona_prompt,
        "capabilities": agent.capabilities,
        "tool_groups": agent.tool_groups,
        "config": agent.config,
    }

    changed = []
    unchanged = []
    for field, old_val in snapshot.items():
        new_val = current_values.get(field)
        if old_val != new_val:
            changed.append({"field": field, "old": old_val, "new": new_val})
        else:
            unchanged.append(field)

    return {"changed": changed, "unchanged": unchanged}


@router.post("/{agent_id}/heartbeat")
def agent_heartbeat(
    agent_id: uuid.UUID,
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(deps.get_current_active_user),
):
    agent = db.query(Agent).filter(Agent.id == agent_id).first()
    if not agent or str(agent.tenant_id) != str(current_user.tenant_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Agent not found")

    try:
        import redis as redis_lib
        r = redis_lib.from_url(settings.REDIS_URL)
        r.set(f"agent:available:{agent_id}", "1", ex=90)
    except Exception as exc:
        logger.warning("Heartbeat Redis write failed for agent %s: %s", agent_id, exc)

    # Touch updated_at if the column exists (Agent model may not have it)
    if hasattr(agent, "updated_at"):
        agent.updated_at = datetime.utcnow()
        db.commit()

    return {"status": "ok", "agent_id": str(agent_id)}


@router.get("/{agent_id}/audit-log", response_model=List[AuditLogEntry])
def get_agent_audit_log(
    agent_id: uuid.UUID,
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(deps.get_current_active_user),
    from_dt: Optional[datetime] = None,
    to_dt: Optional[datetime] = None,
    status_filter: Optional[str] = None,
    limit: int = 50,
):
    agent = db.query(Agent).filter(Agent.id == agent_id).first()
    if not agent or str(agent.tenant_id) != str(current_user.tenant_id):
        raise HTTPException(status_code=404, detail="Agent not found")

    q = (
        db.query(AgentAuditLog)
        .filter(
            AgentAuditLog.agent_id == agent_id,
            AgentAuditLog.tenant_id == current_user.tenant_id,
        )
    )
    if from_dt:
        q = q.filter(AgentAuditLog.created_at >= from_dt)
    if to_dt:
        q = q.filter(AgentAuditLog.created_at <= to_dt)
    if status_filter:
        q = q.filter(AgentAuditLog.status == status_filter)

    return q.order_by(AgentAuditLog.created_at.desc()).limit(limit).all()


@router.get("/{agent_id}/performance")
def get_agent_performance(
    agent_id: uuid.UUID,
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(deps.get_current_active_user),
    window: str = "24h",
):
    agent = db.query(Agent).filter(Agent.id == agent_id).first()
    if not agent or str(agent.tenant_id) != str(current_user.tenant_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Agent not found")

    if window == "7d":
        start_dt = datetime.utcnow() - timedelta(days=7)
    elif window == "30d":
        start_dt = datetime.utcnow() - timedelta(days=30)
    else:
        start_dt = datetime.utcnow() - timedelta(hours=24)

    snapshots = (
        db.query(AgentPerformanceSnapshot)
        .filter(
            AgentPerformanceSnapshot.agent_id == agent_id,
            AgentPerformanceSnapshot.tenant_id == current_user.tenant_id,
            AgentPerformanceSnapshot.window_start >= start_dt,
        )
        .all()
    )

    invocation_count = sum(s.invocation_count for s in snapshots)
    success_count = sum(s.success_count for s in snapshots)
    error_count = sum(s.error_count for s in snapshots)
    timeout_count = sum(s.timeout_count for s in snapshots)
    success_rate = (success_count / invocation_count) if invocation_count > 0 else 0.0
    total_tokens = sum(s.total_tokens for s in snapshots)
    total_cost_usd = sum(s.total_cost_usd for s in snapshots)

    p50_vals = [s.latency_p50_ms for s in snapshots if s.latency_p50_ms is not None]
    p95_vals = [s.latency_p95_ms for s in snapshots if s.latency_p95_ms is not None]
    p99_vals = [s.latency_p99_ms for s in snapshots if s.latency_p99_ms is not None]
    qs_vals = [s.avg_quality_score for s in snapshots if s.avg_quality_score is not None]

    latency_p50_ms = (sum(p50_vals) / len(p50_vals)) if p50_vals else None
    latency_p95_ms = (sum(p95_vals) / len(p95_vals)) if p95_vals else None
    latency_p99_ms = (sum(p99_vals) / len(p99_vals)) if p99_vals else None
    avg_quality_score = (sum(qs_vals) / len(qs_vals)) if qs_vals else None

    return {
        "agent_id": str(agent_id),
        "window": window,
        "invocation_count": invocation_count,
        "success_rate": success_rate,
        "latency_p50_ms": latency_p50_ms,
        "latency_p95_ms": latency_p95_ms,
        "latency_p99_ms": latency_p99_ms,
        "avg_quality_score": avg_quality_score,
        "total_tokens": total_tokens,
        "total_cost_usd": total_cost_usd,
        "snapshot_count": len(snapshots),
    }


# --- Per-agent integration assignment ---

@router.get("/{agent_id}/integrations")
def list_agent_integrations(
    agent_id: uuid.UUID,
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(deps.get_current_active_user),
):
    agent = db.query(Agent).filter(Agent.id == agent_id).first()
    if not agent or str(agent.tenant_id) != str(current_user.tenant_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Agent not found")
    rows = (
        db.query(AgentIntegrationConfig)
        .filter(
            AgentIntegrationConfig.agent_id == agent_id,
            AgentIntegrationConfig.tenant_id == current_user.tenant_id,
        )
        .all()
    )
    return [str(r.integration_config_id) for r in rows]


@router.post("/{agent_id}/integrations")
def assign_integration(
    agent_id: uuid.UUID,
    body: dict,
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(deps.get_current_active_user),
):
    agent = db.query(Agent).filter(Agent.id == agent_id).first()
    if not agent or str(agent.tenant_id) != str(current_user.tenant_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Agent not found")

    cfg_id = body.get("integration_config_id")
    if not cfg_id:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="integration_config_id required")

    try:
        cfg_id = uuid.UUID(str(cfg_id))
    except ValueError:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Invalid integration_config_id")

    cfg = (
        db.query(IntegrationConfig)
        .filter(IntegrationConfig.id == cfg_id, IntegrationConfig.tenant_id == current_user.tenant_id)
        .first()
    )
    if not cfg:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Integration config not found")

    # Idempotent: return 200 if already assigned
    existing = (
        db.query(AgentIntegrationConfig)
        .filter(
            AgentIntegrationConfig.agent_id == agent_id,
            AgentIntegrationConfig.integration_config_id == cfg_id,
        )
        .first()
    )
    if not existing:
        row = AgentIntegrationConfig(
            agent_id=agent_id,
            integration_config_id=cfg_id,
            tenant_id=current_user.tenant_id,
        )
        db.add(row)
        db.commit()

    return {"agent_id": str(agent_id), "integration_config_id": str(cfg_id)}


@router.delete("/{agent_id}/integrations/{cfg_id}")
def remove_integration(
    agent_id: uuid.UUID,
    cfg_id: uuid.UUID,
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(deps.get_current_active_user),
):
    agent = db.query(Agent).filter(Agent.id == agent_id).first()
    if not agent or str(agent.tenant_id) != str(current_user.tenant_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Agent not found")

    row = (
        db.query(AgentIntegrationConfig)
        .filter(
            AgentIntegrationConfig.agent_id == agent_id,
            AgentIntegrationConfig.integration_config_id == cfg_id,
            AgentIntegrationConfig.tenant_id == current_user.tenant_id,
        )
        .first()
    )
    if not row:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Assignment not found")

    db.delete(row)
    db.commit()
    return {"deleted": True}
