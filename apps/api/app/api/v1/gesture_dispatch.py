"""Gesture dispatch — best-effort audit + RL log when the Luna client fires a binding.

The frontend POSTs here every time a gesture matches a binding. We write a
MemoryActivity row (`event_type='gesture_triggered'`, `source='gesture'`) and
an RLExperience row (`decision_point='gesture_action'`). A passive observer
later assigns a reward (1.0 if the binding is still present after 24h, 0.0 if
the user reverted it).

Both writes are best-effort: failures are logged and swallowed so a transient
DB blip never breaks the user-facing gesture action. Note that
`memory_activity.log_activity` and `rl_experience_service.log_experience`
each commit internally — partial success is therefore possible (one row
written, the other failed). This is intentional given the audit-only nature
of these endpoints.
"""
import logging
import uuid
from typing import Optional

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api import deps
from app.core.rate_limit import limiter
from app.db.safe_ops import safe_rollback
from app.models.user import User as UserModel
from app.services import memory_activity, rl_experience_service

logger = logging.getLogger(__name__)

router = APIRouter()


class GestureDispatch(BaseModel):
    binding_id: str = Field(..., max_length=64)
    gesture: dict = Field(default_factory=dict)
    action_kind: str = Field(..., max_length=64)
    screen: Optional[str] = Field(default=None, max_length=256)
    frontmost_app: Optional[str] = Field(default=None, max_length=128)
    latency_ms: Optional[int] = None
    confidence: Optional[float] = None


@router.post("/gesture-dispatch", status_code=204)
@limiter.limit("120/minute")
def dispatch(
    request: Request,
    payload: GestureDispatch,
    *,
    db: Session = Depends(deps.get_db),
    current_user: UserModel = Depends(deps.get_current_active_user),
):
    description = f"{payload.action_kind} via {payload.gesture.get('pose', '?')}"

    try:
        memory_activity.log_activity(
            db,
            current_user.tenant_id,
            event_type="gesture_triggered",
            description=description,
            source="gesture",
            event_metadata={
                "gesture": payload.gesture,
                "action_kind": payload.action_kind,
                "binding_id": payload.binding_id,
                "screen": payload.screen,
                "frontmost_app": payload.frontmost_app,
                "latency_ms": payload.latency_ms,
                "confidence": payload.confidence,
            },
        )
    except Exception:
        logger.exception("[gesture-dispatch] memory_activity log failed")
        safe_rollback(db)

    try:
        rl_experience_service.log_experience(
            db,
            current_user.tenant_id,
            trajectory_id=uuid.uuid4(),
            step_index=0,
            decision_point="gesture_action",
            state={
                "screen": payload.screen,
                "frontmost_app": payload.frontmost_app,
                "binding_id": payload.binding_id,
            },
            action={"kind": payload.action_kind},
        )
    except Exception:
        logger.exception("[gesture-dispatch] rl_experience log failed")
        safe_rollback(db)

    return None
