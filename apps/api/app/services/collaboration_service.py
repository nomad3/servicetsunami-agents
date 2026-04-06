"""Service layer for structured multi-agent collaboration patterns."""

from datetime import datetime
from typing import Dict, List, Optional
import uuid

from sqlalchemy.orm import Session

from app.models.collaboration import CollaborationSession
from app.schemas.blackboard import BlackboardEntryCreate, AuthorRole, EntryType
from app.schemas.collaboration import (
    CollaborationSessionCreate,
    PATTERN_PHASES,
    PHASE_REQUIRED_ROLES,
)
from app.services import blackboard_service


def _find_last_proposal(db: Session, blackboard_id: uuid.UUID) -> Optional[str]:
    """Find the content of the last proposal/revision entry on the blackboard."""
    from app.models.blackboard import BlackboardEntry
    entry = (
        db.query(BlackboardEntry)
        .filter(
            BlackboardEntry.blackboard_id == blackboard_id,
            BlackboardEntry.entry_type.in_(["proposal", "synthesis"]),
        )
        .order_by(BlackboardEntry.board_version.desc())
        .first()
    )
    return entry.content if entry else None


def create_session(
    db: Session,
    tenant_id: uuid.UUID,
    session_in: CollaborationSessionCreate,
) -> CollaborationSession:
    """Create a collaboration session linked to an existing blackboard."""
    board = blackboard_service.get_blackboard(db, tenant_id, session_in.blackboard_id)
    if not board:
        raise ValueError(f"Blackboard {session_in.blackboard_id} not found in this tenant")

    phases = PATTERN_PHASES.get(session_in.pattern.value)
    if not phases:
        raise ValueError(f"Unknown pattern: {session_in.pattern.value}")

    # Require role assignments for all roles needed by the pattern
    all_required_roles = set()
    for phase in phases:
        all_required_roles.update(PHASE_REQUIRED_ROLES.get(phase, []))
    assigned_roles = set(session_in.role_assignments.keys())
    missing = all_required_roles - assigned_roles
    if missing:
        raise ValueError(
            f"Pattern '{session_in.pattern.value}' requires role assignments for: "
            f"{sorted(missing)}. Provide them in role_assignments."
        )

    session = CollaborationSession(
        tenant_id=tenant_id,
        blackboard_id=session_in.blackboard_id,
        pattern=session_in.pattern.value,
        status="active",
        current_phase=phases[0],
        phase_index=0,
        role_assignments=session_in.role_assignments,
        pattern_config=session_in.pattern_config,
        max_rounds=session_in.max_rounds,
    )
    db.add(session)
    db.commit()
    db.refresh(session)
    return session


def get_session(
    db: Session,
    tenant_id: uuid.UUID,
    session_id: uuid.UUID,
) -> Optional[CollaborationSession]:
    return (
        db.query(CollaborationSession)
        .filter(
            CollaborationSession.id == session_id,
            CollaborationSession.tenant_id == tenant_id,
        )
        .first()
    )


def list_sessions(
    db: Session,
    tenant_id: uuid.UUID,
    status: Optional[str] = None,
    blackboard_id: Optional[uuid.UUID] = None,
    limit: int = 50,
) -> List[CollaborationSession]:
    q = db.query(CollaborationSession).filter(CollaborationSession.tenant_id == tenant_id)
    if status:
        q = q.filter(CollaborationSession.status == status)
    if blackboard_id:
        q = q.filter(CollaborationSession.blackboard_id == blackboard_id)
    return q.order_by(CollaborationSession.updated_at.desc()).limit(limit).all()


def advance_phase(
    db: Session,
    tenant_id: uuid.UUID,
    session_id: uuid.UUID,
    agent_slug: str,
    contribution: str,
    evidence: Optional[list] = None,
    confidence: float = 0.7,
    agrees_with_previous: Optional[bool] = None,
) -> Optional[Dict]:
    """Contribute to the current phase and advance if the phase is complete.

    Returns the current state of the session after the contribution.
    """
    session = get_session(db, tenant_id, session_id)
    if not session or session.status != "active":
        return None

    phases = PATTERN_PHASES.get(session.pattern)
    if not phases:
        return None

    current_phase = session.current_phase
    required_roles = PHASE_REQUIRED_ROLES.get(current_phase, [])

    # Check if this is the terminal phase — require explicit approval before any writes
    is_terminal_phase = (session.phase_index + 1) >= len(phases)
    if is_terminal_phase and agrees_with_previous is None:
        raise ValueError(
            f"Phase '{current_phase}' is the final phase — "
            f"'agrees_with_previous' must be true or false (not omitted)"
        )

    # Enforce role assignment: check the agent is assigned to a required role
    if required_roles and session.role_assignments:
        agent_assigned_role = None
        for role, assigned_agent in session.role_assignments.items():
            if assigned_agent == agent_slug:
                agent_assigned_role = role
                break
        if agent_assigned_role not in required_roles:
            raise ValueError(
                f"Agent '{agent_slug}' is not assigned to a required role "
                f"for phase '{current_phase}'. Required: {required_roles}, "
                f"agent's role: {agent_assigned_role or 'none'}"
            )

    # Map the phase to a blackboard entry type
    phase_to_entry_type = {
        "propose": EntryType.PROPOSAL,
        "critique": EntryType.CRITIQUE,
        "revise": EntryType.PROPOSAL,
        "verify": EntryType.EVIDENCE,
        "synthesize": EntryType.SYNTHESIS,
        "research": EntryType.EVIDENCE,
        "debate": EntryType.DISAGREEMENT if not agrees_with_previous else EntryType.EVIDENCE,
        "resolve": EntryType.RESOLUTION,
    }

    # Map the phase to an author role
    phase_to_role = {
        "propose": AuthorRole.PLANNER,
        "critique": AuthorRole.CRITIC,
        "revise": AuthorRole.PLANNER,
        "verify": AuthorRole.VERIFIER,
        "synthesize": AuthorRole.SYNTHESIZER,
        "research": AuthorRole.RESEARCHER,
        "debate": AuthorRole.CRITIC,
        "resolve": AuthorRole.SYNTHESIZER,
    }

    entry_type = phase_to_entry_type.get(current_phase, EntryType.PROPOSAL)
    author_role = phase_to_role.get(current_phase, AuthorRole.CONTRIBUTOR)

    # Post the contribution to the blackboard
    try:
        entry = blackboard_service.add_entry(
            db, tenant_id, session.blackboard_id,
            BlackboardEntryCreate(
                entry_type=entry_type,
                content=contribution,
                evidence=evidence or [],
                confidence=confidence,
                author_agent_slug=agent_slug,
                author_role=author_role,
            ),
        )
    except ValueError as e:
        raise ValueError(f"Failed to add contribution: {e}")

    if not entry:
        raise ValueError("Blackboard not active or not found")

    # Check if phase should advance
    result = {
        "session_id": str(session.id),
        "phase_completed": current_phase,
        "entry_id": str(entry.id),
        "board_version": entry.board_version,
        "current_phase": None,
        "rounds_completed": session.rounds_completed,
        "status": session.status,
        "consensus_reached": session.consensus_reached,
    }

    # Handle disagreement in debate phase
    if current_phase == "debate" and agrees_with_previous is False:
        result["disagreement_logged"] = True

    # Advance to next phase
    next_index = session.phase_index + 1
    if next_index < len(phases):
        session.current_phase = phases[next_index]
        session.phase_index = next_index
        result["current_phase"] = session.current_phase
        result["next_required_roles"] = PHASE_REQUIRED_ROLES.get(session.current_phase, [])
    else:
        # All phases complete — agrees_with_previous already validated above
        session.rounds_completed += 1

        if not agrees_with_previous and session.rounds_completed < session.max_rounds:
            # Disagreement → start another round from critique
            critique_index = phases.index("critique") if "critique" in phases else 1
            session.phase_index = critique_index
            session.current_phase = phases[critique_index]
            result["new_round"] = session.rounds_completed + 1
            result["current_phase"] = session.current_phase
        else:
            session.status = "completed"
            session.consensus_reached = "yes" if agrees_with_previous else "partial"
            # Store the accepted proposal (last 'propose' or 'revise' entry), not the verifier's note
            accepted = _find_last_proposal(db, session.blackboard_id)
            session.outcome = accepted if accepted else contribution
            result["completed"] = True

    result["rounds_completed"] = session.rounds_completed
    result["status"] = session.status
    result["consensus_reached"] = session.consensus_reached

    session.updated_at = datetime.utcnow()
    db.commit()
    db.refresh(session)
    return result
