"""Agent-scoped JWT mint + verify (Phase 4 §8).

Used by:
  - api-side at task dispatch time (``cli_session_manager.run_agent_session``,
    ``code_worker.execute_chat_cli``, ``code_worker.execute_code_task``)
    to mint a token whose claims bind a leaf subprocess to one task.
  - mcp-server-side in ``agent_token_verify.decode_agent_token_if_present``
    to authenticate the third auth tier (agent-token > tenant_jwt >
    X-Tenant-Id header > X-Internal-Key).
  - api-side at ``/api/v1/agents/internal/heartbeat`` to authenticate
    leaf-side PostToolUse hook fires.

Reuses ``settings.SECRET_KEY`` + ``settings.ALGORITHM`` (HS256 today). No
new secret per design §8 + SR-2.

The double-check (``kind == "agent_token"`` AND ``sub.startswith("agent:")``)
is deliberate: ``kind`` is forgeable by anyone with SECRET_KEY (which the
attacker would need to forge a token at all), but the ``sub`` namespace
prefix is a second invariant — a regular user-login token has
``sub=<email>`` and ``kind=access`` (or absent), so even a token-pollution
mistake on our side cannot accidentally cross tiers. (SR-11.)

The ``parent_chain`` claim is hard-capped at MAX_FALLBACK_DEPTH=3
elements at MINT time — we refuse to embed a longer chain rather than
silently truncating. The §3.1 gate inside ResilientExecutor catches
length >= 3 at dispatch, so a token with parent_chain=3 is technically
admissible (the gate fires when execution starts, with a clean refusal).
But longer than 3 means we already mis-counted somewhere upstream;
fail loud at mint time. (SR-3 + D8.)
"""
from __future__ import annotations

import time
from datetime import datetime, timedelta
from typing import Any, Optional, Sequence, TypedDict

from jose import ExpiredSignatureError, JWTError

from app.core.jwt_signing import mint_token, verify_token

from app.core.config import settings


# Imported here (not from packages.cli_orchestrator) to avoid pulling the
# adapter machinery into the API auth path. Single source of truth lives
# in packages/cli_orchestrator/policy.py; we mirror the constant.
_MAX_PARENT_CHAIN_LEN = 3


class AgentTokenClaims(TypedDict, total=False):
    """Decoded agent-token claim shape.

    ``scope`` is ``None`` to mean "no per-call scope check" (the leaf gets
    the union of its agent's tool_groups, which is the same as the
    legacy --allowedTools flag). An empty list means "no tools allowed"
    — distinct semantics. ``parent_chain`` is the lineage of dispatching
    agent UUIDs for the §3.1 recursion gate.
    """

    sub: str  # always "agent:<agent_id>"
    kind: str  # always "agent_token"
    tenant_id: str
    agent_id: str
    task_id: str
    parent_workflow_id: Optional[str]
    scope: Optional[list[str]]
    parent_chain: list[str]
    iat: int
    exp: int


def mint_agent_token(
    *,
    tenant_id: str,
    agent_id: str,
    task_id: str,
    parent_workflow_id: Optional[str] = None,
    scope: Optional[Sequence[str]] = None,
    parent_chain: Sequence[str] = (),
    heartbeat_timeout_seconds: int = 240,
) -> str:
    """Issue an agent-scoped JWT.

    ``exp`` defaults to ``2 * heartbeat_timeout_seconds`` (per design
    §8 step 1) — long enough that the leaf's PostToolUse hook can fire
    a fresh heartbeat before the orchestrator gives up, but short
    enough that a leaked token has bounded blast radius.

    Raises:
        ValueError: parent_chain longer than MAX_FALLBACK_DEPTH.
    """
    parent_chain_list = [str(x) for x in (parent_chain or ())]
    if len(parent_chain_list) > _MAX_PARENT_CHAIN_LEN:
        raise ValueError(
            f"parent_chain too long: got {len(parent_chain_list)}, "
            f"max is {_MAX_PARENT_CHAIN_LEN} (MAX_FALLBACK_DEPTH)"
        )

    now = int(time.time())
    exp = now + 2 * heartbeat_timeout_seconds

    claims: dict[str, Any] = {
        "sub": f"agent:{agent_id}",
        "kind": "agent_token",
        "tenant_id": str(tenant_id),
        "agent_id": str(agent_id),
        "task_id": str(task_id),
        "parent_workflow_id": parent_workflow_id,
        "scope": list(scope) if scope is not None else None,
        "parent_chain": parent_chain_list,
        "iat": now,
        "exp": exp,
    }
    return mint_token(claims, domain="agent")


def verify_agent_token(token: str) -> AgentTokenClaims:
    """Decode + validate an agent-scoped JWT.

    Raises:
        ExpiredSignatureError: token expired.
        ValueError: missing/wrong ``kind`` or ``sub`` shape, bad signature
            (we wrap JWTError as ValueError so callers can handle a single
            "shape failure" exception).
    """
    try:
        payload = verify_token(token, expected_domain="agent")
    except ExpiredSignatureError:
        raise
    except JWTError as e:
        raise ValueError(f"agent_token: signature/decode error: {e}") from e

    kind = payload.get("kind")
    if kind != "agent_token":
        raise ValueError(
            f"agent_token: kind must be 'agent_token', got {kind!r}"
        )

    sub = payload.get("sub", "")
    if not isinstance(sub, str) or not sub.startswith("agent:"):
        raise ValueError(
            f"agent_token: sub must start with 'agent:', got {sub!r}"
        )

    return payload  # type: ignore[return-value]
