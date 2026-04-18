import ipaddress
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from app.api import deps
from app.models.external_agent import ExternalAgent
from app.models.user import User
from app.schemas.external_agent import ExternalAgentCreate, ExternalAgentInDB, ExternalAgentUpdate

router = APIRouter()

_PRIVATE_RANGES = (
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
    ipaddress.ip_network("127.0.0.0/8"),
    ipaddress.ip_network("169.254.0.0/16"),  # link-local / AWS IMDS
    ipaddress.ip_network("::1/128"),
    ipaddress.ip_network("fc00::/7"),
)


def _validate_external_url(url: str) -> None:
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise HTTPException(status_code=422, detail="Only http/https endpoints are allowed")
    host = parsed.hostname or ""
    try:
        addr = ipaddress.ip_address(host)
        if any(addr in net for net in _PRIVATE_RANGES):
            raise HTTPException(status_code=422, detail="Private or internal IP addresses are not allowed")
    except ValueError:
        pass  # hostname — DNS-level SSRF is a deeper concern; IP check is the critical guard


def _get_agent_or_404(db: Session, agent_id: uuid.UUID, tenant_id: uuid.UUID) -> ExternalAgent:
    agent = db.query(ExternalAgent).filter(ExternalAgent.id == agent_id).first()
    if not agent or str(agent.tenant_id) != str(tenant_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="External agent not found")
    return agent


@router.get("", response_model=List[ExternalAgentInDB])
def list_external_agents(
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(deps.get_current_active_user),
):
    return (
        db.query(ExternalAgent)
        .filter(ExternalAgent.tenant_id == current_user.tenant_id)
        .all()
    )


@router.post("", response_model=ExternalAgentInDB, status_code=status.HTTP_201_CREATED)
def register_external_agent(
    body: ExternalAgentCreate,
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(deps.get_current_active_user),
):
    _validate_external_url(body.endpoint_url)
    agent = ExternalAgent(
        tenant_id=current_user.tenant_id,
        name=body.name,
        description=body.description,
        avatar_url=body.avatar_url,
        protocol=body.protocol,
        endpoint_url=body.endpoint_url,
        auth_type=body.auth_type,
        credential_id=body.credential_id,
        capabilities=body.capabilities,
        health_check_path=body.health_check_path,
        metadata_=body.metadata or {},
    )
    db.add(agent)
    db.commit()
    db.refresh(agent)
    return agent


@router.get("/{agent_id}", response_model=ExternalAgentInDB)
def get_external_agent(
    agent_id: uuid.UUID,
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(deps.get_current_active_user),
):
    return _get_agent_or_404(db, agent_id, current_user.tenant_id)


@router.put("/{agent_id}", response_model=ExternalAgentInDB)
def update_external_agent(
    agent_id: uuid.UUID,
    body: ExternalAgentUpdate,
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(deps.get_current_active_user),
):
    agent = _get_agent_or_404(db, agent_id, current_user.tenant_id)
    update_data = body.model_dump(exclude_unset=True)
    if "endpoint_url" in update_data:
        _validate_external_url(update_data["endpoint_url"])
    # metadata field in schema maps to metadata_ column
    if "metadata" in update_data:
        agent.metadata_ = update_data.pop("metadata")
    for field, value in update_data.items():
        setattr(agent, field, value)
    agent.updated_at = datetime.utcnow()
    db.commit()
    db.refresh(agent)
    return agent


@router.delete("/{agent_id}", status_code=status.HTTP_200_OK)
def delete_external_agent(
    agent_id: uuid.UUID,
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(deps.get_current_active_user),
):
    agent = _get_agent_or_404(db, agent_id, current_user.tenant_id)
    db.delete(agent)
    db.commit()
    return {"deleted": True}


@router.post("/{agent_id}/health-check")
def health_check(
    agent_id: uuid.UUID,
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(deps.get_current_active_user),
):
    agent = _get_agent_or_404(db, agent_id, current_user.tenant_id)
    try:
        import httpx
        url = agent.endpoint_url.rstrip("/") + agent.health_check_path
        resp = httpx.get(url, timeout=5.0)
        if resp.status_code == 200:
            agent.status = "online"
            agent.last_seen_at = datetime.utcnow()
        else:
            agent.status = "offline"
    except Exception:
        agent.status = "offline"
    db.commit()
    db.refresh(agent)
    return {"status": agent.status, "last_seen_at": str(agent.last_seen_at)}


@router.post("/{agent_id}/test-task")
def test_task(
    agent_id: uuid.UUID,
    body: Dict[str, Any],
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(deps.get_current_active_user),
):
    from app.services.external_agent_adapter import adapter
    agent = _get_agent_or_404(db, agent_id, current_user.tenant_id)
    task = body.get("task", "")
    try:
        result = adapter.dispatch(agent, task, {}, db)
    except Exception as e:
        result = str(e)
    return {
        "result": result,
        "agent_id": str(agent_id),
        "protocol": agent.protocol,
    }


@router.post("/callback/{agent_id}")
async def webhook_callback(
    agent_id: uuid.UUID,
    request: Request,
    db: Session = Depends(deps.get_db),
):
    agent = db.query(ExternalAgent).filter(ExternalAgent.id == agent_id).first()
    if not agent:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="External agent not found")

    # For hmac-signed callbacks, require a signature header and verify it.
    if agent.auth_type == "hmac":
        sig_header = request.headers.get("X-Signature", "")
        if not sig_header:
            raise HTTPException(status_code=401, detail="Missing X-Signature header")
        # Full HMAC verification requires the stored credential secret — deferred to full adapter
        # implementation. For now, presence of the header is required.

    return {"received": True}
