"""Fleet endpoints — Luna OS podium boot snapshot.

`GET /fleet/snapshot` returns everything the spatial Podium scene needs in a
single round-trip:
  - agents (production + staging) with team_id and an `activity` envelope
    derived from the last 24h of agent_performance_snapshots
  - agent_groups (sections)
  - active collaborations (comms beams)
  - recent notifications + open commitments (inbox melody)

No new database tables. Pure read-only aggregation over existing models.
"""
import logging

from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from app.api import deps
from app.core.rate_limit import limiter
from app.models.user import User as UserModel
from app.services import fleet_snapshot_service

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/snapshot")
@limiter.limit("60/minute")
def get_fleet_snapshot(
    request: Request,
    *,
    db: Session = Depends(deps.get_db),
    current_user: UserModel = Depends(deps.get_current_active_user),
):
    """Return the full podium boot payload for the current user's tenant."""
    return fleet_snapshot_service.build_snapshot(db, current_user.tenant_id)
