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

from fastapi import APIRouter, Body, Depends, HTTPException, Query, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, get_db
from app.models.user import User
from app.schemas.review import (
    ALLOWED_SCOPES,
    ReviewReplyRequest,
    ReviewStartRequest,
    ReviewStartResponse,
    ReviewState,
)
from app.services import review_service

logger = logging.getLogger(__name__)
router = APIRouter()


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
def start_review(
    request: ReviewStartRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Create a new cross-CLI review coalition and dispatch it.

    Returns 201 with the review_id. The CLI fanout runs asynchronously
    on the agentprovision-orchestration Temporal queue (same queue as
    CoalitionWorkflow). Poll GET /reviews/{id} or subscribe to
    /reviews/{id}/events.
    """
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
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    # Fire-and-forget Temporal dispatch. Import locally to keep the
    # router importable when temporal client isn't installed (test
    # suite).
    try:
        from app.services.review_dispatch import dispatch_review_workflow

        dispatch_review_workflow(
            tenant_id=current_user.tenant_id,
            review_id=review.id,
        )
    except Exception as e:
        # Don't fail the start — the operator can still drive the
        # review via /record (e.g. in tests, or while #287 is pending).
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
def reply_review(
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

            dispatch_review_workflow(
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
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Record one CLI's review output for the current round.

    Used by ReviewWorkflow activities (and by the test suite). Trips
    the consensus aggregator when all expected CLIs have reported.
    """
    review = review_service.record_cli_findings(
        db,
        current_user.tenant_id,
        review_id,
        cli=payload.cli.strip().lower(),
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
