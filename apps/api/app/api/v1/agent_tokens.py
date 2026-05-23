"""User-scoped agent-token mint endpoint.

POST /api/v1/agent-tokens/mint

Lets a user — authenticated via Bearer JWT — mint an agent-scoped JWT for
a local subprocess they spawn from their terminal. The user-facing
sibling of the internal `/api/v1/internal/agent-tokens/mint` endpoint
(which is gated by X-Internal-Key and only reachable from inside the
cluster).

Why we need both:
* The internal endpoint is used by the code-worker / orchestration-worker
  when they dispatch a CLI runtime as part of a Temporal workflow. The
  worker passes X-Internal-Key — no user context is involved.
* This endpoint exists so the `alpha` CLI can pass the user's Bearer JWT
  and get back an agent-scoped token to inject into a local Claude Code
  / Codex / Gemini / Copilot subprocess. The same agent.tool_groups +
  scope constrains the leaf either way; only the dispatch path differs.

Security boundaries:
* Caller must have at least `editor` permission on the target agent
  (or be the owner / a superuser). Viewers cannot mint tokens — the
  agent is allowed to perform side-effect tool calls and viewers are
  expressly read-only.
* Tenant binding is enforced inside `require_agent_permission`; the
  caller cannot mint tokens for agents owned by another tenant.
* `parent_chain` is forced empty — user-initiated dispatch is always
  top-level. The internal endpoint accepts a parent_chain because
  workflow-side dispatch is hierarchical.
* `parent_workflow_id` is forced None for the same reason.
"""

from __future__ import annotations

import logging
import uuid
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api import deps
from app.core.rate_limit import limiter
from app.models.agent import Agent
from app.models.agent_permission import AgentPermission
from app.models.user import User
from app.services.agent_token import mint_agent_token

router = APIRouter()
logger = logging.getLogger(__name__)


class UserMintAgentTokenBody(BaseModel):
    agent_id: uuid.UUID = Field(..., description="Target agent UUID.")
    scope: Optional[List[str]] = Field(
        None,
        description=(
            "Optional scope claim — list of allowed bare MCP tool names. "
            "When set, the agent's tool_groups still apply; this is an *additional* "
            "per-call narrowing (intersection semantics). `None` means "
            "'use the agent's full allowlist as configured via tool_groups'."
        ),
    )
    heartbeat_timeout_seconds: int = Field(
        240,
        ge=30,
        le=3600,
        description=(
            "Expected interval between heartbeats from the leaf. The token "
            "`exp` is set to 2x this value, so a leaked token has bounded "
            "blast radius even if the leaf doesn't refresh."
        ),
    )


class MintTokenResponse(BaseModel):
    token: str
    agent_id: uuid.UUID
    task_id: uuid.UUID
    expires_in_seconds: int


@router.post("/agent-tokens/mint", response_model=MintTokenResponse)
@limiter.limit("30/minute")
def mint_user_agent_token(
    request: Request,  # required by slowapi to derive the rate-limit key
    body: UserMintAgentTokenBody,
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(deps.get_current_active_user),
) -> MintTokenResponse:
    """Mint an agent-scoped JWT for the caller's local subprocess.

    Returns 403 if the user lacks `editor`/`admin`/`owner` on the agent.
    Returns 404 if the agent doesn't exist or belongs to a different tenant.
    """
    # Tenant + existence check. Surfaces the same 404 the route-style
    # `require_agent_permission` dependency does, but inline because the
    # agent_id is in the body (not the path), so the existing dependency
    # can't be reused as-is.
    agent = db.query(Agent).filter(Agent.id == body.agent_id).first()
    if agent is None or str(agent.tenant_id) != str(current_user.tenant_id):
        # Same 404 shape regardless of cause — don't leak agent existence
        # to a cross-tenant probe.
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Agent not found")

    # Permission gate. Token minting is a side-effect-enabling capability,
    # so we require write-tier access (editor / admin / owner / superuser).
    # Viewers cannot mint — they're read-only by contract.
    is_owner = agent.owner_user_id and str(agent.owner_user_id) == str(current_user.id)
    if not (current_user.is_superuser or is_owner):
        has_grant = (
            db.query(AgentPermission)
            .filter(
                AgentPermission.agent_id == agent.id,
                AgentPermission.tenant_id == current_user.tenant_id,
                AgentPermission.principal_type == "user",
                AgentPermission.principal_id == current_user.id,
                AgentPermission.permission.in_(["editor", "admin"]),
            )
            .first()
        )
        if not has_grant:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="editor or admin permission required to mint agent tokens",
            )

    # A fresh task_id per mint so the leaf's heartbeats / audit entries
    # cluster under a single CLI invocation without collision. We don't
    # persist a Task row here — the leaf's audit comes through the
    # standard chat / tool-call paths once it starts running.
    task_id = uuid.uuid4()

    token = mint_agent_token(
        tenant_id=str(current_user.tenant_id),
        agent_id=str(agent.id),
        task_id=str(task_id),
        parent_workflow_id=None,  # user-initiated, no parent workflow
        scope=body.scope,
        parent_chain=(),  # top-level dispatch
        heartbeat_timeout_seconds=body.heartbeat_timeout_seconds,
    )

    logger.info(
        "user-scoped agent-token minted: user=%s tenant=%s agent=%s task=%s",
        str(current_user.id)[:8],
        str(current_user.tenant_id)[:8],
        str(agent.id)[:8],
        str(task_id)[:8],
    )

    return MintTokenResponse(
        token=token,
        agent_id=agent.id,
        task_id=task_id,
        expires_in_seconds=2 * body.heartbeat_timeout_seconds,
    )
