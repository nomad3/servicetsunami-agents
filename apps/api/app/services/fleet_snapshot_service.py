"""Fleet snapshot — single-shot aggregator for the Luna OS Podium boot.

Returns everything the spatial scene needs in one round-trip so the user's
podium paints in <1.5s on first launch:
  - agents (production + staging) with their team_id
  - agent_groups (sections in the orchestra)
  - latest performance snapshot per agent (for halo intensity)
  - active collaborations (for comms beams)
  - recent unread notifications (inbox melody, top of scene)
  - open commitments (inbox melody)

No new tables. Pure read-only aggregation over existing models.
"""
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional
import uuid

from sqlalchemy import desc, func
from sqlalchemy.orm import Session

from app.models.agent import Agent
from app.models.agent_group import AgentGroup
from app.models.agent_performance_snapshot import AgentPerformanceSnapshot
from app.models.notification import Notification


def _agent_to_dict(a: Agent, latest_snapshot: Optional[AgentPerformanceSnapshot]) -> Dict[str, Any]:
    return {
        "id": str(a.id),
        "name": a.name,
        "role": a.role,
        "team_id": str(a.team_id) if a.team_id else None,
        "status": a.status,
        "version": a.version,
        "owner_user_id": str(a.owner_user_id) if a.owner_user_id else None,
        "personality": a.personality,
        # Halo signal — derived from the last performance window. The scene
        # multiplies these into pulse intensity / color.
        "activity": {
            "invocations": latest_snapshot.invocation_count if latest_snapshot else 0,
            "success_rate": (
                latest_snapshot.success_count / latest_snapshot.invocation_count
                if latest_snapshot and latest_snapshot.invocation_count
                else None
            ),
            "avg_quality_score": latest_snapshot.avg_quality_score if latest_snapshot else None,
            "p95_latency_ms": latest_snapshot.latency_p95_ms if latest_snapshot else None,
            "window_start": latest_snapshot.window_start.isoformat() if latest_snapshot else None,
        },
    }


def _group_to_dict(g: AgentGroup) -> Dict[str, Any]:
    return {
        "id": str(g.id),
        "name": g.name,
        "description": g.description,
        "goal": g.goal,
    }


def _notification_to_dict(n: Notification) -> Dict[str, Any]:
    return {
        "id": str(n.id),
        "title": n.title,
        "body": (n.body or "")[:200] if n.body else None,
        "source": n.source,
        "priority": n.priority,
        "created_at": n.created_at.isoformat(),
        "read": bool(n.read),
        "reference_type": n.reference_type,
    }


def build_snapshot(db: Session, tenant_id: uuid.UUID) -> Dict[str, Any]:
    """Assemble the full podium snapshot in one transaction."""
    now = datetime.utcnow()
    one_hour_ago = now - timedelta(hours=1)

    # ── Agents (production + staging only — drafts and deprecated stay off
    # the podium) ──────────────────────────────────────────────────────────
    agents = (
        db.query(Agent)
        .filter(
            Agent.tenant_id == tenant_id,
            Agent.status.in_(("production", "staging")),
        )
        .order_by(Agent.team_id.asc().nullslast(), Agent.name.asc())
        .all()
    )

    # ── Latest performance snapshot per agent (one query, in-memory bucket) ─
    snapshots = (
        db.query(AgentPerformanceSnapshot)
        .filter(
            AgentPerformanceSnapshot.tenant_id == tenant_id,
            AgentPerformanceSnapshot.window_start >= now - timedelta(hours=24),
        )
        .order_by(AgentPerformanceSnapshot.window_start.desc())
        .all()
    )
    snapshot_by_agent: Dict[uuid.UUID, AgentPerformanceSnapshot] = {}
    for s in snapshots:
        if s.agent_id and s.agent_id not in snapshot_by_agent:
            snapshot_by_agent[s.agent_id] = s

    agents_payload = [_agent_to_dict(a, snapshot_by_agent.get(a.id)) for a in agents]

    # ── Groups (sections) ──────────────────────────────────────────────────
    groups = (
        db.query(AgentGroup)
        .filter(AgentGroup.tenant_id == tenant_id)
        .order_by(AgentGroup.name.asc())
        .all()
    )
    groups_payload = [_group_to_dict(g) for g in groups]

    # ── Active collaborations (for comms beams) ────────────────────────────
    # Reuse the existing collaboration / blackboard infrastructure. We avoid
    # importing the model directly here to keep this service decoupled — the
    # consumer reads /collaborations/stream SSE for live updates anyway.
    # For the initial snapshot we just say "what's running right now" via a
    # raw query so we don't depend on whether the model exists in this build.
    active_collaborations: List[Dict[str, Any]] = []
    try:
        from app.models.blackboard import Blackboard  # type: ignore
        live = (
            db.query(Blackboard)
            .filter(
                Blackboard.tenant_id == tenant_id,
                Blackboard.created_at >= one_hour_ago,
            )
            .order_by(desc(Blackboard.created_at))
            .limit(20)
            .all()
        )
        for bb in live:
            participants = []
            try:
                participants = list((bb.shared_context or {}).get("participants", []))
            except Exception:
                participants = []
            active_collaborations.append(
                {
                    "id": str(bb.id),
                    "pattern": getattr(bb, "pattern", None),
                    "phase": getattr(bb, "phase", None),
                    "participants": participants,
                    "started_at": bb.created_at.isoformat() if bb.created_at else None,
                }
            )
    except Exception:
        # Blackboard model unavailable in this build — leave empty.
        active_collaborations = []

    # ── Inbox melody — recent notifications (unread, last 24h) ─────────────
    notifications = (
        db.query(Notification)
        .filter(
            Notification.tenant_id == tenant_id,
            Notification.dismissed.is_(False),
            Notification.created_at >= now - timedelta(hours=24),
        )
        .order_by(Notification.priority.asc(), Notification.created_at.desc())
        .limit(15)
        .all()
    )
    notifications_payload = [_notification_to_dict(n) for n in notifications]

    # ── Inbox melody — open commitments (best-effort import; tolerate absence) ─
    commitments_payload: List[Dict[str, Any]] = []
    try:
        from app.models.commitment_record import CommitmentRecord  # type: ignore
        commitments = (
            db.query(CommitmentRecord)
            .filter(
                CommitmentRecord.tenant_id == tenant_id,
                CommitmentRecord.state == "open",
            )
            .order_by(CommitmentRecord.due_at.asc().nullslast())
            .limit(10)
            .all()
        )
        for c in commitments:
            commitments_payload.append(
                {
                    "id": str(c.id),
                    "title": c.title,
                    "owner_agent_slug": c.owner_agent_slug,
                    "state": c.state,
                    "priority": c.priority,
                    "due_at": c.due_at.isoformat() if c.due_at else None,
                }
            )
    except Exception:
        commitments_payload = []

    return {
        "captured_at": now.isoformat(),
        "agents": agents_payload,
        "groups": groups_payload,
        "active_collaborations": active_collaborations,
        "notifications": notifications_payload,
        "commitments": commitments_payload,
    }
