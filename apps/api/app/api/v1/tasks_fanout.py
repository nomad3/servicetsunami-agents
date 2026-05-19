"""
Backend for `alpha run` + `alpha watch`.

Python side of the CLI differentiation roadmap
(`docs/plans/2026-05-13-ap-cli-differentiation-roadmap.md` for Phase 1
shape, `docs/plans/2026-05-18-alpha-run-real-dispatch.md` for Phase 2).

The endpoints accept the dispatch shape produced by the CLI. When
`USE_REAL_FANOUT_WORKFLOW=True` (production) every dispatch — single
provider, `--providers` fallback chain, and `--fanout` parallel — is
routed to a real `FanoutChatCliWorkflow` on the `agentprovision-code`
Temporal queue. When the flag is off, the request lands on the
in-memory rollback stub that returns a synthetic lifecycle
(queued → running → completed) for demo / disaster recovery.

Why an in-memory dict and not a DB-backed scaffold:
  - The CLI flow is the demo. We want a fast, predictable lifecycle
    that does not depend on any other service (Temporal, db migration,
    Ollama warmup). The stub is replaced wholesale once the real
    workflow lands, so investing in persistence here is wasted work.
  - State is **per-worker, per-pod**, in-memory, non-durable. With
    `uvicorn --workers N>1` or gunicorn, each worker has its own
    `_TASKS` dict, so a task dispatched on worker A is not visible
    from worker B — pin replica count to 1 OR run with a single
    worker process during the prototype window.

Eviction (round-1 H1 — DoS bound on memory):
  - Per-tenant cap of `MAX_TASKS_PER_TENANT` in-flight task records
    (parent + children counted separately). Exceeding the cap returns
    `429 Too Many Requests`.
  - Opportunistic wall-clock TTL sweep on every dispatch: records
    older than `TASK_TTL_SECONDS` are evicted. No background thread —
    sweep runs inline on the request path so we don't fight uvicorn's
    event loop.

Auth: standard JWT bearer via `get_current_user`. We do NOT use
`/internal/*` here because this endpoint is hit by the human CLI on
the operator's laptop, not a service-to-service call from MCP or
code-worker.

Tenant spoofing protection (round-1 B1):
  - `tenant_id` of every stored task is bound to the JWT — never to
    request-body fields. The CLI's `--tenant` flag is also removed
    pending the `alpha tenant use` ergonomics (design open question #4).
  - `agent_id` and `session_id` in the body are not yet validated for
    tenant-ownership here because the prototype dispatch does not
    consume them downstream. They are stored on the record so the
    real `FanoutChatCliWorkflow` can pick them up; that workflow
    runs its own ownership check before any tool call, which is the
    correct gate (it sees the full Agent + ChatSession contexts).
"""

from __future__ import annotations

import time
import uuid
from collections import defaultdict
from typing import Any, Dict, List, Optional

import asyncio as _asyncio
import json as _json

from fastapi import APIRouter, Depends, Header, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field, field_validator, model_validator
from sqlalchemy.orm import Session

from datetime import datetime, timezone

from sqlalchemy import func

from app.api.deps import get_current_user, get_db
from app.core.config import settings
from app.models.agent_performance_snapshot import AgentPerformanceSnapshot
from app.models.tenant_features import TenantFeatures
from app.models.user import User
from app.services.cost_estimator import estimate_fanout_cost

router = APIRouter()


# ── Phase 2 (#177 follow-up, 2026-05-18) ──────────────────────────────
# Default provider when the caller passes neither `providers` nor
# `fanout`. Design doc open question #1 wants this auto-detected from
# `tenant_features.default_cli_platform`; until that lookup lands, we
# hard-code the safe ship-default. claude_code is the most-tested
# leaf CLI under ChatCliWorkflow and matches the chat hot path's
# tenant default in cli_session_manager.
DEFAULT_RUN_PROVIDER = "claude_code"


def _verify_tenant_header(
    current_user: User = Depends(get_current_user),
    x_tenant_id: Optional[str] = Header(default=None, alias="X-Tenant-Id"),
) -> User:
    """Round-1 review M5: shared sub-dependency that codifies the
    `X-Tenant-Id-must-match-JWT-when-present` contract across every
    route on this router. Previously only `/run` enforced it; the
    other routes silently let a stale header slip through. Now
    every route gets the same gate.

    Round-3 L3-2 (#435) leniency on whitespace-only headers is
    preserved — pure-whitespace == "no header" == accept.

    Returns `current_user` so route handlers can chain it directly:
      `current_user: User = Depends(_verify_tenant_header)`
    """
    _header_tenant = (x_tenant_id or "").strip()
    if _header_tenant and _header_tenant != str(current_user.tenant_id):
        raise HTTPException(
            status_code=400,
            detail=(
                "X-Tenant-Id header does not match the JWT tenant. "
                "Re-login against the intended tenant or clear the "
                "stale tenant_id from your CLI config."
            ),
        )
    return current_user


# ── Request / response schemas ─────────────────────────────────────────


class RunEstimate(BaseModel):
    estimated_duration_seconds: int
    estimated_cost_usd: float
    confidence: str


class RunChildDispatch(BaseModel):
    """Child returned by `POST /run` — minted IDs + provider only.

    Split out from `RunChildStatus` (round-1 M3) so future fields on
    one shape don't leak into the other.
    """

    task_id: str
    provider: str


class RunChildStatus(BaseModel):
    """Child returned by `GET /{id}/status` — adds per-child status.

    Forward-compatible: `error` will land here when failure paths
    materialize (round-1 M2 lands on the parent's `TaskStatusResponse`;
    children inherit the same shape in a follow-up).
    """

    task_id: str
    provider: str
    status: str


class RunFanoutRequest(BaseModel):
    """Payload from `alpha run`.

    Tenant binding (round-1 B1): tenant identity is taken from the
    JWT — NOT from this body. We deliberately do NOT carry a
    `tenant_id` field; any tenant override needs `alpha tenant use`
    semantics (design open question #4) which are out of scope here.
    """

    prompt: str = Field(..., min_length=1, max_length=20_000)
    agent_id: Optional[str] = None
    session_id: Optional[str] = None

    # Fallback chain — tried in order; first non-quota-error wins.
    providers: List[str] = Field(default_factory=list)

    # Parallel-dispatch list. Mutually exclusive with `providers` —
    # enforced both at the schema level (round-1 M4, model_validator
    # below) and again in the route handler as belt-and-suspenders.
    fanout: List[str] = Field(default_factory=list)

    # `council` | `first-wins` | `all` — controls how fanout children
    # are merged. Ignored when `fanout` is empty.
    merge: str = "council"

    @field_validator("merge")
    @classmethod
    def _validate_merge(cls, v: str) -> str:
        allowed = {"council", "first-wins", "all"}
        if v not in allowed:
            raise ValueError(f"merge must be one of {allowed}, got {v!r}")
        return v

    @field_validator("fanout", "providers")
    @classmethod
    def _strip_provider_names(cls, v: List[str]) -> List[str]:
        # Round-1 N4: CLI users (and direct API consumers) sometimes
        # paste `",claude, ,codex"` from a comma-split. Drop empty /
        # whitespace-only entries silently — this is documented
        # leniency, not a bug. Pass a clean comma-split list if you
        # want strict validation.
        return [p.strip() for p in v if p and p.strip()]

    @model_validator(mode="after")
    def _exclusive_providers_or_fanout(self) -> "RunFanoutRequest":
        """Round-1 M4: schema-level rejection of `providers ∧ fanout`.

        Produces a 422 with field paths instead of the route's free-form
        400 + detail string, matching FastAPI / Pydantic conventions
        for direct API consumers.
        """
        if self.providers and self.fanout:
            raise ValueError(
                "providers and fanout are mutually exclusive — pass one or neither"
            )
        return self


class RunFanoutResponse(BaseModel):
    task_id: str
    status: str
    children: List[RunChildDispatch] = Field(default_factory=list)
    estimate: Optional[RunEstimate] = None
    # Round-3 follow-up (#573 review): non-fatal advisories about the
    # request — e.g. `agent_id` was passed but the dispatch path can't
    # yet honor per-agent binding. Empty list on the happy path; the
    # CLI surfaces non-empty entries with `[alpha] warning: ...`.
    warnings: List[str] = Field(default_factory=list)


class TaskStatusResponse(BaseModel):
    task_id: str
    status: str
    result: Optional[str] = None
    # Round-1 M2: surface failure reason on `failed`/`cancelled` so
    # the CLI can render something other than "[alpha] t_xxx — failed"
    # with no context. Stays `None` on `completed` / `running` /
    # `queued`. Free-form for the prototype; the real impl returns
    # a structured `{code, message, retryable}` discriminator.
    error: Optional[str] = None
    children: List[RunChildStatus] = Field(default_factory=list)


# ── In-memory task ledger (prototype only) ─────────────────────────────


# task_id → record. Keys are deterministic UUIDs minted at dispatch.
# Records hold dispatch-time params plus a `created_at` we use to
# derive the synthetic lifecycle:
#   t < QUEUED_SECS         → queued
#   t < RUNNING_SECS_TOTAL  → running
#   t ≥ RUNNING_SECS_TOTAL  → completed
# Round-1 N2: constants are public-by-convention (uppercase, no
# underscore prefix) matching scoring_rubrics.py / auto_quality_scorer.py.
_TASKS: dict[str, dict] = {}

# Round-2 N2-1: maintain an O(1) tenant-count alongside _TASKS so the
# cap check at dispatch is constant-time instead of O(n). Keys are
# tenant_id strings; values are the live record count for that tenant.
# Every mutation that touches `tenant_id` on a record (insert, evict,
# cancel) must update this map in lock-step.
_TENANT_COUNTS: dict[str, int] = defaultdict(int)

QUEUED_SECS = 2.0
RUNNING_SECS_TOTAL = 8.0

# Round-1 H1: DoS bound. MAX_TASKS_PER_TENANT is high enough for any
# normal interactive use, low enough that a misbehaving tenant cannot
# OOM the pod. TASK_TTL_SECONDS gives a 10-minute window after which
# a completed task disappears from /status (the real impl swaps to
# Temporal which already has its own visibility window).
MAX_TASKS_PER_TENANT = 50
TASK_TTL_SECONDS = 600.0


def _derive_status(created_at: float) -> str:
    elapsed = time.monotonic() - created_at
    if elapsed < QUEUED_SECS:
        return "queued"
    if elapsed < RUNNING_SECS_TOTAL:
        return "running"
    return "completed"


def _mint_task_id() -> str:
    """Mint a task id.

    Round-1 H2: 16 hex chars (64-bit entropy) instead of 8 (32-bit).
    Still typeable for humans resuming via `alpha watch`, but 65K× safer
    against collision in the real impl that replaces this stub
    (which will see thousands of concurrent tasks per tenant per day).
    """
    return f"t_{uuid.uuid4().hex[:16]}"


def _evict_record(task_id: str) -> None:
    """Pop a record and decrement the tenant counter in lock-step.
    All eviction paths (`_sweep_expired_tasks`, `cancel_task`,
    child-clean-on-cancel) go through here so the counter never drifts."""
    rec = _TASKS.pop(task_id, None)
    if rec is not None:
        tid = rec.get("tenant_id")
        if tid and _TENANT_COUNTS.get(tid, 0) > 0:
            _TENANT_COUNTS[tid] -= 1
            if _TENANT_COUNTS[tid] == 0:
                # Defensive: prevent the defaultdict from growing
                # unboundedly with stale tenant keys at 0.
                del _TENANT_COUNTS[tid]


def _sweep_expired_tasks() -> int:
    """Round-1 H1: opportunistic TTL sweep. Called inline at every
    dispatch — no background thread, no event-loop fight. Returns the
    number of records evicted (for tests / log lines)."""
    now = time.monotonic()
    expired = [
        tid
        for tid, rec in _TASKS.items()
        if now - rec["created_at"] >= TASK_TTL_SECONDS
    ]
    for tid in expired:
        _evict_record(tid)
    return len(expired)


def _count_tenant_tasks(tenant_id: str) -> int:
    """Round-1 H1 + round-2 N2-1: per-tenant active record count.
    O(1) via the `_TENANT_COUNTS` mirror map maintained on every
    insert / evict / cancel."""
    return _TENANT_COUNTS.get(tenant_id, 0)


# ── Real Temporal dispatch (#177 Phase 1 ship) ─────────────────────────


async def _dispatch_fanout_workflow(
    *,
    prompt: str,
    tenant_id: str,
    providers: List[str],
    merge: str,
    agent_id: Optional[str],
    session_id: Optional[str],
) -> Dict[str, Any]:
    """Start a `FanoutChatCliWorkflow` on the `agentprovision-code`
    Temporal queue. Returns `{task_id, run_id}` where `task_id` is
    the Temporal workflow_id (kept stable across resume / replay).

    Local import of `workflows` service avoids circular-import risk
    and lets `USE_REAL_FANOUT_WORKFLOW=False` paths skip it entirely.
    """
    # Round-1 review N1: use the module-level `uuid` import directly;
    # no need for a local rename that shadows it.
    from app.services import workflows as wf_service

    workflow_id = f"fanout-{tenant_id}-{uuid.uuid4()}"
    args = {
        "prompt": prompt,
        "tenant_id": tenant_id,
        "providers": providers,
        "merge": merge,
        "agent_id": agent_id,
        "session_id": session_id,
        "instruction_md_content": "",
        "mcp_config": "",
        "model": "",
        "allowed_tools": "",
    }
    handle = await wf_service.start_workflow(
        workflow_type="FanoutChatCliWorkflow",
        tenant_id=uuid.UUID(tenant_id),
        task_queue="agentprovision-code",
        arguments=args,
        workflow_id=workflow_id,
        # Round-1 review H3: memo carries tenant_id for the defense-in-
        # depth cross-check on /status reads. The prefix check on
        # workflow_id remains the cheap pre-filter; memo is the durable
        # gate that survives any future workflow_id template change.
        memo={"source": "ap_run_fanout", "merge": merge, "tenant_id": tenant_id},
    )
    return {
        "task_id": workflow_id,
        "run_id": handle.first_execution_run_id,
    }


def _extract_tenant_from_workflow_id(task_id: str) -> Optional[str]:
    """Round-2 H3 hardening: parse the tenant UUID out of a workflow_id
    of shape `fanout-<tenant_uuid>-<uuid4>`. Returns the inner UUID
    string or None if the shape doesn't match.

    This is the durable tenant-isolation gate. Memo cross-check was
    considered but the temporalio SDK's memo serialization (Payload
    proto vs decoded dict) is version-dependent (B2-1 round-2 finding).
    The workflow_id template is API-internal, single-producer, and
    deterministic — parse it instead."""
    if not task_id.startswith("fanout-"):
        return None
    tail = task_id[len("fanout-"):]
    # UUID is 36 chars with 4 hyphens (8-4-4-4-12). Split on the
    # next hyphen *after* the UUID to recover the tenant.
    # Pattern: <8>-<4>-<4>-<4>-<12>-<dispatch_uuid>
    parts = tail.split("-")
    if len(parts) < 6:
        return None
    candidate = "-".join(parts[:5])  # canonical UUID form
    try:
        uuid.UUID(candidate)
    except (ValueError, AttributeError):
        return None
    return candidate


async def _describe_fanout_workflow(task_id: str, *, expected_tenant_id: str) -> Dict[str, Any]:
    """Look up a real-dispatch task's status via Temporal. Returns
    a dict shaped to merge with the prototype's status response.

    Round-2 H3 hardening: tenant isolation is enforced by parsing
    the workflow_id (deterministic, API-minted template) instead of
    reading the memo (whose serialization is SDK-version dependent —
    see round-2 B2-1). The parsed tenant must exactly match the
    caller's JWT tenant; mismatch → PermissionError → route 404.

    Maps Temporal workflow status to the CLI's vocabulary:
      RUNNING / CONTINUED_AS_NEW → 'running'
      COMPLETED                  → 'completed'
      FAILED / TERMINATED        → 'failed'
      CANCELED                   → 'cancelled'
      TIMED_OUT                  → 'failed' (with error)"""
    parsed_tenant = _extract_tenant_from_workflow_id(task_id)
    if parsed_tenant != expected_tenant_id:
        raise PermissionError(
            "workflow_id tenant prefix does not match caller's JWT tenant"
        )

    from app.services import workflows as wf_service

    desc = await wf_service.describe_workflow(workflow_id=task_id)
    status_map = {
        "RUNNING": "running",
        "CONTINUED_AS_NEW": "running",
        "COMPLETED": "completed",
        "FAILED": "failed",
        "TERMINATED": "failed",
        "CANCELED": "cancelled",
        "TIMED_OUT": "failed",
    }
    cli_status = status_map.get(desc.get("status") or "", "running")
    return {
        "task_id": task_id,
        "status": cli_status,
        "result": None,  # body comes from result-fetch below if completed
        "error": None,
        "raw": desc,
    }


def _recount_tenant_tasks_from_records(tenant_id: str) -> int:
    """Round-3 N3-1: O(n) recount of records for a tenant by walking
    `_TASKS`. This is the slow ground-truth that `_TENANT_COUNTS`
    mirrors. Used **only** in tests to assert the counter never
    drifts from the dict. Do NOT call from request paths."""
    return sum(1 for rec in _TASKS.values() if rec["tenant_id"] == tenant_id)


def _tenant_over_monthly_token_limit(
    db: Session, tenant_id: uuid.UUID
) -> bool:
    """Round-3 follow-up (#573 review BLOCKER): cost gate.

    `alpha run` with USE_REAL_FANOUT_WORKFLOW=true spends real LLM
    tokens. A tenant that has already burned through their monthly
    budget should not be able to keep dispatching — otherwise the
    cap is just a moving target paid for by Anthropic.

    Mirrors the canonical lookup in `insights_cost._quota_burn`:
      - tenant_features.monthly_token_limit NULL / 0 -> unbounded
        (opt-in policy; same as the rest of the platform).
      - month-to-date tokens are summed from
        AgentPerformanceSnapshot.total_tokens for the current
        calendar month.
      - over limit -> True (caller returns 429).

    Returns False on every error path (DB hiccup, missing features
    row, etc.) so a transient infra issue never blocks dispatch —
    cost overruns are recoverable, dispatch failures are not.
    """
    try:
        features = (
            db.query(TenantFeatures)
            .filter(TenantFeatures.tenant_id == tenant_id)
            .first()
        )
        # Guard against the unit-test DB stub returning a MagicMock for
        # `.first()` — must be a real TenantFeatures row to consult the
        # limit. Same belt-and-suspenders shape used in insights_cost.
        if not isinstance(features, TenantFeatures):
            return False
        limit = features.monthly_token_limit
        if not limit or not isinstance(limit, int) or limit <= 0:
            return False

        now = datetime.now(timezone.utc)
        month_start = now.replace(
            day=1, hour=0, minute=0, second=0, microsecond=0
        )
        mtd_tokens = (
            db.query(
                func.coalesce(func.sum(AgentPerformanceSnapshot.total_tokens), 0)
            )
            .filter(AgentPerformanceSnapshot.tenant_id == tenant_id)
            .filter(AgentPerformanceSnapshot.window_start >= month_start)
            .scalar()
        )
        if not isinstance(mtd_tokens, (int, float)):
            return False

        return int(mtd_tokens) >= int(limit)
    except Exception:
        # Fail-open on DB / model errors — see docstring.
        return False


# ── Routes ────────────────────────────────────────────────────────────


@router.post(
    "/run",
    response_model=RunFanoutResponse,
    summary="Dispatch a durable task (single, fallback chain, or fanout).",
)
async def run_fanout(
    body: RunFanoutRequest,
    current_user: User = Depends(_verify_tenant_header),
    db: Session = Depends(get_db),
) -> RunFanoutResponse:
    """Dispatch endpoint hit by `alpha run`.

    Behavior in the prototype:
      - Mints a parent task_id.
      - If `fanout` is non-empty: also mints one child task_id per
        provider; status of children evolves on the same clock as the
        parent (real impl uses Temporal child workflows).
      - Returns immediately. The CLI either tails `/status` or detaches
        (`--background`).

    Status codes:
      - 200: task dispatched successfully.
      - 422: malformed request (Pydantic) — incl. `providers ∧ fanout`.
      - 401: missing / invalid bearer (handled by `get_current_user`).
      - 429: tenant exceeded `MAX_TASKS_PER_TENANT` in-flight cap.
    """

    # Tenant identity is JWT-bound. Round-1 B1: we deliberately do not
    # accept a body field for this; it is the JWT's tenant or nothing.
    # Round-1 review M5 (#188): X-Tenant-Id mismatch check has been
    # lifted into `_verify_tenant_header` so /status, /cancel, and
    # /events/stream get the same gate.
    tenant_id = str(current_user.tenant_id)

    # Belt-and-suspenders mutual-exclusion check. The model_validator
    # on RunFanoutRequest catches this before we reach here for direct
    # API consumers; the clap-side `conflicts_with` catches it for the
    # CLI. We re-check here in case either side is ever bypassed.
    if body.providers and body.fanout:
        raise HTTPException(
            status_code=400,
            detail="Cannot pass both `providers` (fallback chain) and `fanout` (parallel).",
        )

    # Round-1 review B2: opportunistic eviction + cap check BEFORE the
    # dispatch-path branch. Previously only the stub path enforced the
    # cap; the real-dispatch path could OOM Temporal by unlimited
    # workflow creation. Now both paths bill against the same per-
    # tenant ceiling.
    #
    # Phase 2 (#177 follow-up, 2026-05-18): cap accounting now reflects
    # the effective dispatch shape — parent + one slot per child that
    # the workflow will actually spawn. For `--providers` chain or the
    # default single-provider path, the parent dispatches >=1 children
    # too, so they bill against the cap.
    _sweep_expired_tasks()
    if settings.USE_REAL_FANOUT_WORKFLOW:
        if body.fanout:
            _planned_children = len(body.fanout)
        elif body.providers:
            _planned_children = len(body.providers)
        else:
            _planned_children = 1
    else:
        # Stub path is unchanged: only fanout mints children.
        _planned_children = len(body.fanout)
    n_new = 1 + _planned_children
    if _count_tenant_tasks(tenant_id) + n_new > MAX_TASKS_PER_TENANT:
        raise HTTPException(
            status_code=429,
            detail=(
                f"Tenant has too many in-flight tasks "
                f"(max {MAX_TASKS_PER_TENANT}). Wait for some to complete or "
                f"call POST /{{task_id}}/cancel."
            ),
        )

    # Round-3 follow-up (#573 review BLOCKER): monthly token / cost gate.
    # The in-flight cap above bounds Temporal workflow inventory; it does
    # NOT bound LLM spend. With USE_REAL_FANOUT_WORKFLOW=True every run
    # spends real tokens against the provider account, so a tenant past
    # their `tenant_features.monthly_token_limit` should be told to wait
    # for the next billing window — not silently keep burning budget.
    #
    # NULL / 0 limit = unbounded (opt-in policy, same as
    # insights_cost._quota_burn). Stub path (flag off) is exempt — it
    # doesn't spend tokens, so the gate would just frustrate demos.
    if settings.USE_REAL_FANOUT_WORKFLOW and _tenant_over_monthly_token_limit(
        db, current_user.tenant_id
    ):
        raise HTTPException(
            status_code=429,
            detail="Monthly token limit reached",
        )

    # Round-3 follow-up (#573 review IMPORTANT): `agent_id` is accepted
    # by the request schema and stored on the record, but the underlying
    # FanoutChatCliWorkflow does not yet honor per-agent binding —
    # everything resolves through the tenant's default agent. Surface
    # this as a non-fatal warning so the CLI can flag the no-op instead
    # of silently dropping the user's intent.
    response_warnings: list[str] = []
    if body.agent_id:
        response_warnings.append(
            "agent_id ignored — worker not yet wired for per-agent dispatch "
            "(Phase 3 follow-up). Tenant default agent will handle the run."
        )

    # #177 Phase 1 ship: when `USE_REAL_FANOUT_WORKFLOW=true` AND the
    # request has a non-empty fanout, dispatch to Temporal instead of
    # the in-memory stub.
    #
    # Phase 2 (#177 follow-up, 2026-05-18): the flag now covers single-
    # provider and `--providers` fallback-chain dispatches too, so
    # `alpha run "..."` returns real LLM output instead of the synthetic
    # placeholder. All three paths land on FanoutChatCliWorkflow, which
    # already handles N=1 (single child, first-wins is a no-op merge)
    # and N=k+first-wins (closest analog to a quota-aware chain pending
    # a true sequential fallback workflow — see plan doc 2026-05-18).
    # The stub stays as the demo-safe fallback when the flag is off
    # so rollback is just an env-var flip.
    if settings.USE_REAL_FANOUT_WORKFLOW:
        # Resolve the effective provider list + merge mode for the
        # workflow, treating fanout / providers / neither uniformly.
        if body.fanout:
            effective_providers = list(body.fanout)
            effective_merge = body.merge
        elif body.providers:
            # Fallback chain: try each in order until one succeeds. We
            # approximate with `first-wins` (the first child to *complete*
            # wins, others cancel). True quota-aware sequential walk
            # is tracked as a Phase-3 follow-up in the plan doc.
            effective_providers = list(body.providers)
            effective_merge = "first-wins"
        else:
            # Single-provider default. design doc #1: tenant_features.
            # default_cli_platform lookup is the long-term home; until
            # that lands, use the safe ship-default.
            effective_providers = [DEFAULT_RUN_PROVIDER]
            effective_merge = "first-wins"

        dispatch = await _dispatch_fanout_workflow(
            prompt=body.prompt,
            tenant_id=tenant_id,
            providers=effective_providers,
            merge=effective_merge,
            agent_id=body.agent_id,
            session_id=body.session_id,
        )
        # Round-3 H3-1: store stub-shape records for parent + each
        # child so `_evict_record` (decrement-by-1-per-pop) keeps
        # `_TENANT_COUNTS` in lock-step with the n_new billed at
        # dispatch. Otherwise the counter would drift up monotonically
        # by `len(fanout)` per evicted real-path record.
        #
        # Round-2 L2-1: parent record populates the stub-shape fields
        # (prompt, fanout, merge, etc.) so a flag-flip back to
        # USE_REAL_FANOUT_WORKFLOW=False while records are warm
        # doesn't 500 on `record["prompt"]` KeyError.
        _TENANT_COUNTS[tenant_id] += n_new
        now_mono = time.monotonic()
        parent_record_id = dispatch["task_id"]
        # Mirror child IDs into _TASKS so the cap counter has a record
        # to decrement on eviction. These IDs are synthetic — they are
        # NOT resolvable through /status (the route only accepts the
        # real workflow_id for the parent). They exist purely for
        # cap-accounting parity with the stub path.
        #
        # Phase 2 (#177 follow-up, 2026-05-18): mirror children for the
        # effective_providers list, not just `body.fanout`, so the
        # single-provider and --providers chain paths get the same
        # accounting treatment.
        child_record_ids = [
            f"{parent_record_id}#child-{i}" for i in range(len(effective_providers))
        ]
        _TASKS[parent_record_id] = {
            "tenant_id": tenant_id,
            "created_at": now_mono,
            "children": [
                {"task_id": cid, "provider": p, "created_at": now_mono}
                for cid, p in zip(child_record_ids, effective_providers)
            ],
            "real_temporal": True,
            "prompt": body.prompt,
            # Round-2 L2-1 forward-compat: keep `fanout` populated when
            # the caller passed --fanout; otherwise leave empty so a
            # post-rollback stub-path read doesn't synthesize a fake
            # multi-provider council message for a single-provider run.
            "providers": list(body.providers) if body.providers and not body.fanout else [],
            "fanout": list(body.fanout) if body.fanout else [],
            "merge": effective_merge,
            "user_id": str(current_user.id),
            "agent_id": body.agent_id,
            "session_id": body.session_id,
        }
        for cid, p in zip(child_record_ids, effective_providers):
            _TASKS[cid] = {
                "tenant_id": tenant_id,
                "created_at": now_mono,
                "parent_id": parent_record_id,
                "provider": p,
                "real_temporal": True,
                "children": [],
                "prompt": body.prompt,
                "providers": [],
                "fanout": [],
                "merge": effective_merge,
                "user_id": str(current_user.id),
                "agent_id": body.agent_id,
                "session_id": body.session_id,
            }
        # #190: real estimate from chat_messages history (cost_usd
        # field from #174). Falls back to the static placeholder when
        # the tenant has zero history for the requested providers.
        ce = estimate_fanout_cost(
            db, tenant_id=current_user.tenant_id, providers=effective_providers
        )
        estimate = RunEstimate(
            estimated_duration_seconds=ce.estimated_duration_seconds,
            estimated_cost_usd=ce.estimated_cost_usd,
            confidence=ce.confidence,
        )
        # Round-1 review N2: do NOT mint synthetic `<wf_id>#child-<p>`
        # task IDs — those are never resolvable via /status. Surface an
        # empty children list; the /status response populates children
        # from the real Temporal child workflow handles in the follow-up.
        return RunFanoutResponse(
            task_id=dispatch["task_id"],
            status="queued",
            children=[],
            estimate=estimate,
            warnings=response_warnings,
        )

    parent_id = _mint_task_id()
    children: list[RunChildDispatch] = []
    if body.fanout:
        children = [
            RunChildDispatch(task_id=_mint_task_id(), provider=p) for p in body.fanout
        ]

    # Round-3 M3-1: write records FIRST, then increment the counter,
    # so a partial-write exception cannot leave _TENANT_COUNTS inflated
    # (no decrement-on-error gymnastics needed). The cap-check above
    # already gates against running over capacity; uvicorn is
    # single-threaded so we don't race ourselves between the check
    # and the increment.
    now = time.monotonic()
    _TASKS[parent_id] = {
        "prompt": body.prompt,
        "providers": list(body.providers),
        "fanout": list(body.fanout),
        "merge": body.merge,
        # Round-1 B1: tenant is JWT-bound. NEVER honor a body field.
        "tenant_id": tenant_id,
        "user_id": str(current_user.id),
        # Round-1 L4: store agent_id / session_id so /status can echo
        # them back; the real FanoutChatCliWorkflow consumes them.
        "agent_id": body.agent_id,
        "session_id": body.session_id,
        "created_at": now,
        "children": [
            {"task_id": c.task_id, "provider": c.provider, "created_at": now}
            for c in children
        ],
    }
    # Also store each child as a top-level record so the cap-counter
    # bills them, and so a future `/cancel <child_id>` can target a
    # specific provider without enumerating every parent record.
    for c in children:
        _TASKS[c.task_id] = {
            "prompt": body.prompt,
            "tenant_id": tenant_id,
            "user_id": str(current_user.id),
            "agent_id": body.agent_id,
            "session_id": body.session_id,
            "parent_id": parent_id,
            "provider": c.provider,
            "created_at": now,
            "children": [],
            "providers": [],
            "fanout": [],
            "merge": body.merge,
        }
    # All writes complete — now increment the counter in lock-step.
    _TENANT_COUNTS[tenant_id] += n_new

    # #190: real estimate from chat_messages history. Same helper as
    # the real-dispatch branch above; uses fanout list when present,
    # else providers chain (also a list of provider names), else a
    # single-slot fallback.
    estimate_providers = body.fanout or body.providers or []
    ce = estimate_fanout_cost(
        db, tenant_id=current_user.tenant_id, providers=estimate_providers
    )
    estimate = RunEstimate(
        estimated_duration_seconds=ce.estimated_duration_seconds,
        estimated_cost_usd=ce.estimated_cost_usd,
        confidence=ce.confidence,
    )

    return RunFanoutResponse(
        task_id=parent_id,
        status="queued",
        children=children,
        estimate=estimate,
        warnings=response_warnings,
    )


@router.get(
    "/{task_id}/status",
    response_model=TaskStatusResponse,
    summary="Get task status — poll target for `alpha watch`.",
)
async def task_status(
    task_id: str,
    current_user: User = Depends(_verify_tenant_header),
) -> TaskStatusResponse:
    """Status endpoint hit by `alpha watch` (poll loop in the prototype).

    #177 Phase 1 ship: when the task_id is a real Temporal workflow id
    (prefix `fanout-<tenant_id>-...` minted by `_dispatch_fanout_workflow`)
    we route to Temporal `describe_workflow`. Otherwise we fall back to
    the in-memory stub.
    """

    # Round-1 review L3: declare result/error once above the branches.
    result: Optional[str] = None
    error: Optional[str] = None
    tenant_str = str(current_user.tenant_id)

    if settings.USE_REAL_FANOUT_WORKFLOW and task_id.startswith("fanout-"):
        # Tenant isolation pre-filter via workflow_id prefix. The
        # durable gate is the memo cross-check inside
        # `_describe_fanout_workflow` (round-1 review H3).
        prefix = f"fanout-{tenant_str}-"
        if not task_id.startswith(prefix):
            raise HTTPException(status_code=404, detail=f"task {task_id} not found")
        try:
            payload = await _describe_fanout_workflow(
                task_id, expected_tenant_id=tenant_str
            )
        except PermissionError:
            # Memo cross-check failed — defense-in-depth tenant gate.
            # Return 404 (not 403) to avoid the existence oracle.
            raise HTTPException(status_code=404, detail=f"task {task_id} not found")
        except Exception as exc:  # noqa: BLE001
            # Workflow vanished or temporal unreachable — treat as not
            # found rather than 500 so the CLI poll loop exits cleanly.
            # Round-1 review L2: include exception message (truncated)
            # so debugging doesn't lose the cause chain.
            err_msg = f"{type(exc).__name__}: {str(exc)[:200]}"
            raise HTTPException(
                status_code=404,
                detail=f"task {task_id} not found ({err_msg})",
            )

        if payload["status"] == "completed":
            # Round-1 review H1: use the public `fetch_workflow_result`
            # helper instead of reaching into `_get_temporal_client`.
            from app.services import workflows as wf_service

            try:
                raw_result = await wf_service.fetch_workflow_result(task_id)
                if isinstance(raw_result, dict):
                    result = raw_result.get("merged_text")
                    if not raw_result.get("success", True):
                        error = "All providers failed."
                else:
                    result = getattr(raw_result, "merged_text", None)
            except Exception as exc:  # noqa: BLE001
                # Round-1 review L2: surface exception message + class.
                error = f"result-fetch failed: {type(exc).__name__}: {str(exc)[:200]}"
        return TaskStatusResponse(
            task_id=task_id,
            status=payload["status"],
            result=result,
            error=error,
            children=[],  # children resolve in a follow-up; not needed for the demo
        )

    record = _TASKS.get(task_id)
    if not record:
        # 404 also surfaces if the pod restarted between dispatch and
        # watch, or if TTL eviction (H1) evicted the record. The real
        # implementation looks up the workflow run via Temporal; this
        # stub does not survive restarts (documented in the module
        # docstring).
        raise HTTPException(status_code=404, detail=f"task {task_id} not found")

    # Round-1 B1: tenant isolation against the JWT-bound stored value.
    # 404 (not 403) is intentional — do not leak existence to other
    # tenants.
    if record["tenant_id"] != str(current_user.tenant_id):
        raise HTTPException(status_code=404, detail=f"task {task_id} not found")

    status = _derive_status(record["created_at"])
    children = [
        RunChildStatus(
            task_id=c["task_id"],
            provider=c["provider"],
            status=_derive_status(c["created_at"]),
        )
        for c in record["children"]
    ]

    # Round-2 M2-1: result/error already declared above the branches
    # (round-1 L3 fix). Reset them here for the stub path's lifecycle
    # computation — early-return on real-path means these only see
    # the stub side.
    result = None
    error = None
    if status == "completed":
        # Rollback-mode synthetic body. When USE_REAL_FANOUT_WORKFLOW
        # is on (production target) every dispatch lands on the
        # real-path branch above; this is only ever reached when the
        # operator has deliberately disabled real dispatch via env-var
        # flip. Phase 2 (#177 follow-up, 2026-05-18): wording tightened
        # to mark this as a fallback state, not a normal demo response.
        if record.get("fanout"):
            providers = ", ".join(record["fanout"])
            result = (
                f"[stub] Fanout over [{providers}] merged via "
                f"`{record['merge']}`.\n\n"
                f"Prompt: {record['prompt']}\n\n"
                f"USE_REAL_FANOUT_WORKFLOW is disabled on the API pod; "
                f"this is the in-memory rollback response. Set the flag "
                f"to True to dispatch real ChatCliWorkflow runs."
            )
        else:
            result = (
                f"[stub] Completed.\n\n"
                f"Prompt: {record['prompt']}\n\n"
                f"USE_REAL_FANOUT_WORKFLOW is disabled on the API pod; "
                f"this is the in-memory rollback response."
            )

    # Prototype lifecycle never reaches `failed`/`cancelled` from the
    # timer logic. `/cancel` deletes the record outright, so subsequent
    # `/status` returns 404, not "cancelled". The `error` field is on
    # the response schema (round-1 M2) so the real impl can populate
    # it without changing the wire contract.

    return TaskStatusResponse(
        task_id=task_id,
        status=status,
        result=result,
        error=error,
        children=children,
    )


@router.post(
    "/{task_id}/cancel",
    summary="Cancel an in-flight task. (`alpha cancel <task_id>`)",
    status_code=204,
    responses={204: {"description": "Cancelled."}, 404: {"description": "Not found."}},
)
async def cancel_task(
    task_id: str,
    current_user: User = Depends(_verify_tenant_header),
) -> None:
    """Cancel endpoint for the CLI's eventual `alpha cancel`. In the
    prototype we just drop the record; real impl issues
    `RequestCancelWorkflowExecution` to Temporal.

    Round-1 B1: tenant isolation enforced before the drop so a caller
    in tenant A cannot delete tenant B's record by guessing its id.

    Round-2 M2-2: when cancelling a **child** task, also remove the
    child from its parent's `children` list so subsequent
    `GET /<parent>/status` doesn't keep computing the child's
    synthetic lifecycle from wall-clock forever.

    #177 Phase 1 ship: when the task_id is a real Temporal workflow,
    issue `cancel_workflow` instead of dropping the in-memory record.
    """

    if settings.USE_REAL_FANOUT_WORKFLOW and task_id.startswith("fanout-"):
        tenant_str = str(current_user.tenant_id)
        prefix = f"fanout-{tenant_str}-"
        if not task_id.startswith(prefix):
            raise HTTPException(status_code=404, detail=f"task {task_id} not found")

        # Round-2 M2-2: separate the tenant-mismatch case (PermissionError)
        # from the operational-failure case (Temporal unreachable). Both
        # return 404 (no existence oracle) but with distinguishable detail.
        try:
            await _describe_fanout_workflow(task_id, expected_tenant_id=tenant_str)
        except PermissionError:
            raise HTTPException(status_code=404, detail=f"task {task_id} not found")
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(
                status_code=404,
                detail=f"task {task_id} not found ({type(exc).__name__}: {str(exc)[:200]})",
            )

        # Round-1 H1: public cancel_workflow helper, no private SDK reach.
        from app.services import workflows as wf_service

        try:
            await wf_service.cancel_workflow(task_id)
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(
                status_code=404,
                detail=f"task {task_id} not found ({type(exc).__name__}: {str(exc)[:200]})",
            )
        # Round-3 H3-1: evict the parent record AND its mirrored child
        # records so `_TENANT_COUNTS` decrements symmetrically with the
        # +n_new billed at dispatch. `_evict_record` is idempotent.
        parent_record = _TASKS.get(task_id)
        if parent_record:
            for child in parent_record.get("children", []):
                _evict_record(child["task_id"])
        _evict_record(task_id)
        return

    record = _TASKS.get(task_id)
    if not record or record["tenant_id"] != str(current_user.tenant_id):
        raise HTTPException(status_code=404, detail=f"task {task_id} not found")

    parent_id = record.get("parent_id")
    if parent_id:
        # This is a child being cancelled directly. Surgical removal
        # from the parent's children list so /status no longer reports
        # it. Round-2 M2-2.
        parent = _TASKS.get(parent_id)
        if parent is not None:
            parent["children"] = [
                c for c in parent.get("children", []) if c["task_id"] != task_id
            ]
        _evict_record(task_id)
        return

    # Parent (or single-provider task): drop its own children first,
    # then itself. Preserves the cap-counter invariant — orphan child
    # records would otherwise count against the tenant forever.
    for child in record.get("children", []):
        _evict_record(child["task_id"])
    _evict_record(task_id)


# ─── #188: SSE event stream for `alpha watch` ────────────────────────────


@router.get(
    "/{task_id}/events/stream",
    summary="Server-Sent Events stream of task status transitions.",
)
async def task_events_stream(
    task_id: str,
    current_user: User = Depends(_verify_tenant_header),
):
    """Long-lived SSE stream for `alpha watch <task_id>`. Emits
    `event: status` records on each parent + child status transition;
    terminates on the parent reaching a terminal state.

    Implementation:
      - Stub-path: reads `_TASKS[task_id]` directly, computes
        synthetic status from the wall-clock lifecycle, emits on
        change. No external service.
      - Real-path (`fanout-<tenant_uuid>-...` prefix): polls
        `_describe_fanout_workflow` every 1s server-side; the
        Cloudflare-tunnel hop sees a steady stream instead of the
        524 timeout that a single long request would trip
        (`docs/plans/2026-05-09-resilient-cli-orchestrator-design.md`).
      - Heartbeat comments every 15s keep the connection alive
        through intermediaries that idle-kill silent SSE streams.

    Tenant isolation: same gates as `/status`. Stub-path enforces
    `record["tenant_id"] == JWT tenant`; real-path enforces via
    workflow_id parser inside `_describe_fanout_workflow`. 404 on
    mismatch.
    """

    tenant_str = str(current_user.tenant_id)
    is_real = settings.USE_REAL_FANOUT_WORKFLOW and task_id.startswith("fanout-")

    # Pre-flight tenant + existence check. We do this BEFORE the
    # StreamingResponse so a 404 surfaces cleanly to the CLI instead
    # of an empty SSE stream that closes immediately.
    if is_real:
        prefix = f"fanout-{tenant_str}-"
        if not task_id.startswith(prefix):
            raise HTTPException(status_code=404, detail=f"task {task_id} not found")
        try:
            await _describe_fanout_workflow(task_id, expected_tenant_id=tenant_str)
        except PermissionError:
            raise HTTPException(status_code=404, detail=f"task {task_id} not found")
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(
                status_code=404,
                detail=f"task {task_id} not found ({type(exc).__name__}: {str(exc)[:200]})",
            )
    else:
        record = _TASKS.get(task_id)
        if not record or record["tenant_id"] != tenant_str:
            raise HTTPException(status_code=404, detail=f"task {task_id} not found")

    async def _generator():
        # Send an initial comment so the client sees the connection
        # opened even before the first status transition.
        yield ": stream open\n\n"

        last_status: Optional[str] = None
        last_child_status: dict[str, str] = {}
        last_heartbeat = time.monotonic()
        # Hard cap on stream duration so a wedged workflow doesn't
        # park an SSE forever (matches CLI --timeout default).
        deadline = time.monotonic() + 1800.0

        while time.monotonic() < deadline:
            try:
                if is_real:
                    payload = await _describe_fanout_workflow(
                        task_id, expected_tenant_id=tenant_str
                    )
                    status = payload["status"]
                    # Round-1 review M3: emit real-path child status by
                    # reading the mirrored child records stored at
                    # dispatch (the round-3 H3-1 fix in #435 already
                    # wrote these into `_TASKS`). They use the same
                    # _derive_status timer as stub-path; a follow-up
                    # PR replaces this with real Temporal child handle
                    # state once the FanoutChatCliWorkflow exposes it.
                    parent_record = _TASKS.get(task_id)
                    child_payload: list[dict] = []
                    if parent_record is not None:
                        for c in parent_record.get("children", []):
                            child_payload.append({
                                "task_id": c["task_id"],
                                "provider": c["provider"],
                                "status": _derive_status(c["created_at"]),
                            })
                else:
                    record = _TASKS.get(task_id)
                    if not record:
                        # TTL-evicted or cancelled — terminate cleanly.
                        # Round-1 review L5: emit `cancelled` (a known
                        # terminal status) instead of `gone` so
                        # downstream tooling (jq filters, render code)
                        # doesn't see an out-of-vocab status string.
                        yield 'event: ended\ndata: {"status": "cancelled"}\n\n'
                        return
                    status = _derive_status(record["created_at"])
                    child_payload = [
                        {
                            "task_id": c["task_id"],
                            "provider": c["provider"],
                            "status": _derive_status(c["created_at"]),
                        }
                        for c in record.get("children", [])
                    ]
            except Exception as exc:  # noqa: BLE001
                # Soft-fail: emit error event + terminate. The CLI's
                # SSE consumer will see the event and exit gracefully.
                yield f"event: error\ndata: {_json.dumps({'detail': type(exc).__name__})}\n\n"
                return

            # Emit transitions only.
            if status != last_status:
                yield (
                    f"event: status\ndata: "
                    f"{_json.dumps({'task_id': task_id, 'status': status})}\n\n"
                )
                last_status = status

            for c in child_payload:
                prev = last_child_status.get(c["task_id"])
                if prev != c["status"]:
                    yield (
                        f"event: child_status\ndata: {_json.dumps(c)}\n\n"
                    )
                    last_child_status[c["task_id"]] = c["status"]

            if status in ("completed", "failed", "cancelled"):
                # Round-1 review M2: emit `result` event for stub-path
                # completions too. The CLI's finalize_terminal then
                # GETs /status to pick up the canonical body — both
                # paths render identically.
                if status == "completed":
                    try:
                        if is_real:
                            from app.services import workflows as wf_service

                            raw = await wf_service.fetch_workflow_result(task_id)
                            merged = (
                                raw.get("merged_text") if isinstance(raw, dict)
                                else getattr(raw, "merged_text", None)
                            )
                        else:
                            # Stub-path: reconstruct the rollback-mode
                            # body the same way `/status` does inline.
                            r = _TASKS.get(task_id) or {}
                            if r.get("fanout"):
                                providers = ", ".join(r["fanout"])
                                merged = (
                                    f"[stub] Fanout over [{providers}] merged via "
                                    f"`{r.get('merge', 'council')}`.\n\n"
                                    f"Prompt: {r.get('prompt', '')}\n\n"
                                    f"USE_REAL_FANOUT_WORKFLOW is disabled."
                                )
                            else:
                                merged = (
                                    f"[stub] Completed.\n\n"
                                    f"Prompt: {r.get('prompt', '')}\n\n"
                                    f"USE_REAL_FANOUT_WORKFLOW is disabled."
                                )
                        if merged:
                            yield (
                                f"event: result\ndata: "
                                f"{_json.dumps({'merged_text': merged})}\n\n"
                            )
                    except Exception:  # noqa: BLE001
                        pass  # best-effort; result is also available via /status
                yield 'event: ended\ndata: ' + _json.dumps({"status": status}) + '\n\n'
                return

            # Heartbeat every 15s to keep the connection alive through
            # idle-killing intermediaries.
            now = time.monotonic()
            if now - last_heartbeat >= 15.0:
                yield ": heartbeat\n\n"
                last_heartbeat = now

            await _asyncio.sleep(1.0)

        # Hit the deadline without reaching terminal.
        yield (
            f"event: timeout\ndata: "
            f"{_json.dumps({'detail': 'SSE deadline (30min) hit; task still running'})}\n\n"
        )

    return StreamingResponse(_generator(), media_type="text/event-stream")
