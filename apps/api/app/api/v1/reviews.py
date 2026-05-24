"""`alpha review` — cross-CLI consensus code-review endpoints.

Wire surface for the `alpha review` CLI subcommand. The heavy lifting
lives in `app/services/review_service.py`; this module is a thin
FastAPI shim that adds:

  * tenant isolation on every read/write
  * the dispatch hook into the Temporal ReviewWorkflow (which fans the
    review prompt out to N CLIs in parallel)
  * the SSE stream that replays state transitions to `alpha review
    watch <id>`

Endpoint surface:
  POST /api/v1/reviews/start          → create coalition + dispatch
  GET  /api/v1/reviews                 → list reviews for the tenant
  GET  /api/v1/reviews/{id}            → current state snapshot
  POST /api/v1/reviews/{id}/reply      → operator submits fixes
  POST /api/v1/reviews/{id}/record     → internal: a CLI reports its
                                          findings for the current
                                          round (used by the workflow)
  GET  /api/v1/reviews/{id}/events     → SSE state stream

The `record` endpoint is gated to the internal API key in production;
in dev it accepts a normal bearer so the test suite can drive it
without spinning up Temporal.

Dependency on #287:
  Until task #287 (real CLI dispatch in `alpha run`) lands, the
  workflow shim in app/workflows/review_workflow.py records mocked
  outputs. The wire surface here is final.
"""

from __future__ import annotations

import json
import logging
import time
import uuid
from typing import List, Optional

from fastapi import APIRouter, Body, Depends, Header, HTTPException, Query, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, get_db
from app.core.config import settings
from app.models.user import User
from app.models.agent import Agent
from app.schemas.review import (
    ALLOWED_SCOPES,
    CircularityCheckRequest,
    CircularityCheckResponse,
    CircularityFindingPayload,
    ReviewReplyRequest,
    ReviewStartRequest,
    ReviewStartResponse,
    ReviewState,
)
from app.services import review_service
from app.services.agent_token import verify_agent_token
from app.services.review_circularity import detect_self_modification
from app.services.reviewer_availability import ReviewerUnavailableError

logger = logging.getLogger(__name__)
router = APIRouter()


def _enforce_review_quota(db: Session, tenant_id: uuid.UUID) -> None:
    """I5: refuse to start a new review when the tenant has already
    burned its monthly token budget.

    Mirrors the projection logic in `insights_cost._quota_burn` but is
    a strict gate rather than a soft warning:

      * If the tenant has no `tenant_features` row, OR
      * the row's `monthly_token_limit` is None / 0,
    we don't enforce — same opt-in policy as the dashboard.

    Otherwise, sum month-to-date tokens from
    `agent_performance_snapshots` (same source the dashboard reads —
    no second source of truth) and raise 429 when usage has already
    met or exceeded the limit. Reviews are an opt-in heavy operation,
    not a hot path; the extra query is fine.
    """
    from datetime import datetime, timezone

    from sqlalchemy import func as _func

    from app.models.agent_performance_snapshot import AgentPerformanceSnapshot
    from app.models.tenant_features import TenantFeatures

    features = (
        db.query(TenantFeatures)
        .filter(TenantFeatures.tenant_id == tenant_id)
        .first()
    )
    if not features or not features.monthly_token_limit:
        return

    now = datetime.now(timezone.utc)
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    mtd_tokens = (
        db.query(_func.coalesce(_func.sum(AgentPerformanceSnapshot.total_tokens), 0))
        .filter(AgentPerformanceSnapshot.tenant_id == tenant_id)
        .filter(AgentPerformanceSnapshot.window_start >= month_start)
        .scalar()
    ) or 0

    if int(mtd_tokens) >= int(features.monthly_token_limit):
        raise HTTPException(
            status_code=429,
            detail=(
                f"tenant has exceeded monthly_token_limit "
                f"({mtd_tokens}/{features.monthly_token_limit}); "
                "raise the limit or wait for next month before starting "
                "another review"
            ),
        )


def _state_payload(review) -> dict:
    """Render a ReviewCoalition row as a dict matching ReviewState."""
    return {
        "id": review.id,
        "tenant_id": review.tenant_id,
        "ref": review.ref,
        "scope": review.scope,
        "clis": review.clis or [],
        "blackboard_id": review.blackboard_id,
        "chat_session_id": review.chat_session_id,
        "rounds_completed": review.rounds_completed,
        "max_rounds": review.max_rounds,
        "status": review.status,
        "findings": review.findings or {},
        "agreed_findings": review.agreed_findings or [],
        "last_reply_ref": review.last_reply_ref,
        "created_at": review.created_at,
        "updated_at": review.updated_at,
    }


@router.post("/start", response_model=ReviewStartResponse, status_code=status.HTTP_201_CREATED)
async def start_review(
    request: ReviewStartRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Create a new cross-CLI review coalition and dispatch it.

    Returns 201 with the review_id. The CLI fanout runs asynchronously
    on the agentprovision-orchestration Temporal queue (same queue as
    CoalitionWorkflow). Poll GET /reviews/{id} or subscribe to
    /reviews/{id}/events.

    I5: Cost gate — a single review fans out to N CLIs × M rounds,
    each with non-trivial LLM token cost. Refuse to start a new
    review when the tenant has already exceeded its
    `tenant_features.monthly_token_limit` for the month. Per
    insights_cost.py the limit is opt-in: tenants with no
    tenant_features row (or a NULL/0 limit) are unbounded as today.
    """
    # I5: monthly-token-limit gate.
    _enforce_review_quota(db, current_user.tenant_id)

    # Soft-validate scope. Unknown scopes are accepted (we pass them to
    # the leaf CLI verbatim so tenants can customize) but log a warning.
    if request.scope not in ALLOWED_SCOPES:
        logger.info(
            "alpha review: unknown scope '%s' — passing through verbatim",
            request.scope,
        )

    try:
        review, board = review_service.start_review(
            db,
            current_user.tenant_id,
            ref=request.ref,
            clis=request.clis,
            scope=request.scope,
            max_rounds=request.max_rounds,
            chat_session_id=request.chat_session_id,
            changed_files=request.changed_files,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except ReviewerUnavailableError as e:
        # Surface the structured reasons + operator next-steps as 409.
        # No silent fail-open. Each reason carries a per-code
        # next_steps hint so operators have a path forward inline.
        raise HTTPException(
            status_code=409,
            detail={
                "error": "reviewer_unavailable",
                "reasons": [
                    {
                        "agent_slug": r.agent_slug,
                        "code": r.code,
                        "detail": r.detail,
                        "next_steps": r.next_steps,
                    }
                    for r in e.reasons
                ],
            },
        )

    # Temporal-native dispatch from the request handler. The
    # `dispatch_review_workflow` coroutine awaits `Client.connect` +
    # `start_workflow` synchronously; the daemon-thread wrapper that
    # used to fire-and-forget here silently failed under gunicorn
    # workers, leaving the review row with no Temporal workflow behind
    # it. Import locally to keep the router importable when temporal
    # client isn't installed (test suite).
    try:
        from app.services.review_dispatch import dispatch_review_workflow

        await dispatch_review_workflow(
            tenant_id=current_user.tenant_id,
            review_id=review.id,
        )
    except Exception as e:
        # Don't fail the start — the operator can still drive the
        # review via /record (e.g. in tests, or while #287 is pending).
        # The row stays in `running`; the operator can recover by
        # POST /reply or by retrying.
        logger.warning(
            "ReviewWorkflow dispatch failed for review %s: %s",
            review.id,
            e,
        )

    return ReviewStartResponse(
        review_id=review.id,
        status=review.status,
        clis=review.clis or [],
        blackboard_id=review.blackboard_id,
        chat_session_id=review.chat_session_id,
        message=(
            f"Cross-CLI review dispatched to {len(review.clis or [])} CLI(s). "
            "Poll GET /api/v1/reviews/{id} or stream /events."
        ),
    )


@router.post("/check-circularity", response_model=CircularityCheckResponse)
def check_circularity(
    request: CircularityCheckRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Dry-run the introduction-PR circularity gate.

    Lets an operator (or Luna) inspect, before dispatching reviewers,
    whether the PR's changed files would force any candidate
    reviewer out of the fanout. Returns the filtered reviewer list
    plus per-dropped-reviewer findings with the escalation target.

    Design: docs/plans/2026-05-24-review-gate-medium-followups-design.md
    """
    filtered, findings = detect_self_modification(
        db,
        current_user.tenant_id,
        request.changed_files,
        request.candidate_slugs,
    )
    return CircularityCheckResponse(
        filtered_reviewers=filtered,
        findings=[
            CircularityFindingPayload(
                agent_slug=f.agent_slug,
                bundled_path=f.bundled_path,
                escalation_slug=f.escalation_slug,
            )
            for f in findings
        ],
    )


@router.get("", response_model=List[ReviewState])
def list_reviews(
    review_status: Optional[str] = Query(default=None, alias="status"),
    limit: int = Query(default=50, ge=1, le=200),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    reviews = review_service.list_reviews(
        db, current_user.tenant_id, status=review_status, limit=limit,
    )
    return [_state_payload(r) for r in reviews]


@router.get("/{review_id}", response_model=ReviewState)
def get_review(
    review_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    review = review_service.get_review(db, current_user.tenant_id, review_id)
    if not review:
        raise HTTPException(status_code=404, detail="Review not found")
    return _state_payload(review)


@router.post("/{review_id}/reply", response_model=ReviewState)
async def reply_review(
    review_id: uuid.UUID,
    request: ReviewReplyRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Operator submits fixes — re-fan-out for another consensus round."""
    review = review_service.apply_reply(
        db,
        current_user.tenant_id,
        review_id,
        updated_ref=request.updated_ref,
    )
    if not review:
        raise HTTPException(status_code=404, detail="Review not found")

    # Re-dispatch the workflow if we're not terminal.
    if review.status == "running":
        try:
            from app.services.review_dispatch import dispatch_review_workflow

            await dispatch_review_workflow(
                tenant_id=current_user.tenant_id,
                review_id=review.id,
            )
        except Exception as e:
            logger.warning(
                "ReviewWorkflow re-dispatch failed for review %s: %s",
                review.id,
                e,
            )

    return _state_payload(review)


class RecordFindingsRequest(BaseModel):
    cli: str = Field(..., min_length=1, max_length=64)
    raw_text: str = Field(default="", max_length=200_000)
    findings: Optional[List[dict]] = None


@router.post("/{review_id}/record", response_model=ReviewState)
def record_findings(
    review_id: uuid.UUID,
    payload: RecordFindingsRequest = Body(...),
    authorization: Optional[str] = Header(default=None, alias="Authorization"),
    x_internal_key: Optional[str] = Header(default=None, alias="X-Internal-Key"),
    x_tenant_id: Optional[str] = Header(default=None, alias="X-Tenant-Id"),
    db: Session = Depends(get_db),
):
    """Record one CLI's review output for the current round.

    Three accepted auth tiers, in precedence order:

      1. ``X-Internal-Key`` matching ``settings.API_INTERNAL_KEY`` or
         ``settings.MCP_API_KEY`` → trusted service path (used by the
         Temporal ReviewWorkflow). ``X-Tenant-Id`` MUST be supplied
         alongside. Any ``cli`` value is accepted.

      2. Agent-scoped JWT (``kind=agent_token``) → leaf CLI calling
         back into the API. The token's ``agent_id`` is resolved to
         its ``Agent.name`` (lower-cased); the request is rejected
         with 403 unless that name matches ``payload.cli``. This
         prevents a buggy or compromised leaf inside the tenant from
         submitting findings under another CLI's name and forging a
         fake consensus.

      3. Human tenant bearer JWT (``kind=access``) → operator override
         (e.g. replaying captured CLI output manually). Any ``cli``
         value is accepted; tenant scope comes from the user row.

    Trips the consensus aggregator when all expected CLIs have reported.
    """
    cli_name = payload.cli.strip().lower()

    # ── Tier 1: internal API key ────────────────────────────────────
    if x_internal_key and x_internal_key in (
        settings.API_INTERNAL_KEY, settings.MCP_API_KEY,
    ):
        if not x_tenant_id:
            raise HTTPException(
                status_code=400,
                detail="X-Tenant-Id required with X-Internal-Key",
            )
        try:
            tenant_id = uuid.UUID(x_tenant_id)
        except ValueError:
            raise HTTPException(status_code=400, detail="X-Tenant-Id must be a UUID")
    else:
        if not authorization:
            raise HTTPException(status_code=401, detail="Authorization header required")
        parts = authorization.split(None, 1)
        if len(parts) != 2 or parts[0].lower() != "bearer":
            raise HTTPException(status_code=401, detail="Bearer scheme required")
        token = parts[1].strip()

        # ── Tier 2: agent-scoped JWT ────────────────────────────────
        agent_claims = None
        try:
            agent_claims = verify_agent_token(token)
        except Exception:
            agent_claims = None

        if agent_claims is not None:
            agent_id_claim = agent_claims.get("agent_id")
            tenant_id_claim = agent_claims.get("tenant_id")
            if not agent_id_claim or not tenant_id_claim:
                raise HTTPException(
                    status_code=403,
                    detail="agent_token missing agent_id/tenant_id",
                )
            agent = db.query(Agent).filter(Agent.id == agent_id_claim).first()
            if not agent or str(agent.tenant_id) != str(tenant_id_claim):
                raise HTTPException(
                    status_code=403,
                    detail="agent_token references unknown agent",
                )
            bound_slug = (agent.name or "").strip().lower()
            if bound_slug != cli_name:
                raise HTTPException(
                    status_code=403,
                    detail=(
                        f"agent_token bound to '{bound_slug}' cannot record "
                        f"findings as '{cli_name}'"
                    ),
                )
            tenant_id = uuid.UUID(str(tenant_id_claim))
        else:
            # ── Tier 3: human tenant bearer ─────────────────────────
            user = get_current_user(db=db, token=token)
            tenant_id = user.tenant_id

    review = review_service.record_cli_findings(
        db,
        tenant_id,
        review_id,
        cli=cli_name,
        raw_text=payload.raw_text,
        findings=payload.findings,
    )
    if not review:
        raise HTTPException(status_code=404, detail="Review not found")
    return _state_payload(review)


@router.get("/{review_id}/events")
def review_events(
    review_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
):
    """SSE of review state transitions.

    Reuses the same Redis pub/sub plumbing as
    `/collaborations/{id}/stream`. Events are emitted by the
    consensus aggregator on each round close.

    Heartbeat is 15s, but Cloudflare's edge cuts long-lived HTTP
    responses at ~100s idle regardless — a review that sits in
    `running` past that ceiling will see the stream killed with 524.
    The CLI's `alpha review watch <id>` doc-comment tells operators
    to re-run the command to resubscribe; missed transitions are
    re-emitted via the snapshot replay on reconnect.

    TODO(#570): migrate to the async/queue-buffered SSE pattern shared
    with collaborations once that lands so the Cloudflare ceiling
    stops applying.
    """
    import redis as redis_lib

    from app.core.config import settings
    from app.db.session import SessionLocal

    db = SessionLocal()
    try:
        review = review_service.get_review(db, current_user.tenant_id, review_id)
        if not review:
            raise HTTPException(status_code=404, detail="Review not found")
        initial = json.dumps({
            "event_type": "review_snapshot",
            "payload": {
                "id": str(review.id),
                "status": review.status,
                "rounds_completed": review.rounds_completed,
                "max_rounds": review.max_rounds,
                "agreed_findings_count": len(review.agreed_findings or []),
            },
        })
    finally:
        db.close()

    channel = f"review:{review_id}"

    def _stream():
        yield f"data: {initial}\n\n"
        pubsub = None
        try:
            r = redis_lib.Redis.from_url(settings.REDIS_URL, decode_responses=True)
            pubsub = r.pubsub()
            pubsub.subscribe(channel)
        except Exception as e:
            logger.warning("Redis subscribe failed for review %s: %s", review_id, e)
            return

        last_heartbeat = time.time()
        try:
            for message in pubsub.listen():
                if message["type"] == "message":
                    yield f"data: {message['data']}\n\n"
                    try:
                        data = json.loads(message["data"])
                        if data.get("event_type") == "review_completed":
                            pubsub.unsubscribe(channel)
                            return
                    except Exception:
                        pass
                if time.time() - last_heartbeat > 15:
                    yield ": heartbeat\n\n"
                    last_heartbeat = time.time()
        except Exception as e:
            logger.warning("Redis pubsub error for review %s: %s", review_id, e)
        finally:
            try:
                if pubsub is not None:
                    pubsub.close()
            except Exception:
                pass

    return StreamingResponse(
        _stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
