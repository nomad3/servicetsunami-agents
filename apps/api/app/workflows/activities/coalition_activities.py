"""Activities for CoalitionWorkflow."""
import json
import logging
from typing import Optional
from uuid import UUID, uuid4
from temporalio import activity

from app.db.session import SessionLocal
from app.services import blackboard_service
from app.schemas.blackboard import BlackboardCreate, BlackboardEntryInDB
from app.schemas.collaboration import CollaborationSessionCreate, CollaborationPattern

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _infer_pattern(task_lower: str) -> str:
    """Infer collaboration pattern from task description. Returns underscore format."""
    if any(k in task_lower for k in ["incident", "investigate", "outage", "degraded", "crash", "alert"]):
        return "incident_investigation"
    if any(k in task_lower for k in ["research", "market", "competitor"]):
        return "research_synthesize"
    if any(k in task_lower for k in ["deploy", "fix", "implement"]):
        return "plan_verify"
    return "propose_critique_revise"


def _required_roles_for_pattern(pattern: str) -> list:
    roles_map = {
        "incident_investigation": ["triage_agent", "investigator", "analyst", "commander"],
        "research_synthesize": ["researcher", "synthesizer"],
        "plan_verify": ["planner", "verifier"],
        "propose_critique_revise": ["planner", "critic"],
    }
    return roles_map.get(pattern, ["planner", "critic"])


def _build_blackboard_context(entries: list) -> str:
    """Format blackboard entries as readable context for the next agent."""
    if not entries:
        return "No prior contributions."
    lines = []
    for e in entries:
        entry_dict = e if isinstance(e, dict) else {
            "author_agent_slug": e.author_agent_slug,
            "author_role": e.author_role,
            "entry_type": e.entry_type,
            "content": e.content,
            "confidence": e.confidence,
            "board_version": e.board_version,
        }
        lines.append(
            f"[v{entry_dict['board_version']}] {entry_dict['author_agent_slug']} "
            f"({entry_dict['author_role']}/{entry_dict['entry_type']}, "
            f"confidence={entry_dict['confidence']:.2f}):\n{entry_dict['content']}"
        )
    return "\n\n---\n\n".join(lines)


def _build_phase_prompt(
    phase: str,
    agent_role: str,
    task_description: str,
    blackboard_context: str,
    agent_persona: str = "",
) -> str:
    """Build the CLI prompt for a collaboration phase."""
    role_instructions = {
        "triage_agent": (
            "Your job is to triage this incident. Classify the severity (P1/P2/P3), "
            "identify all affected systems using the knowledge graph context, and scope the blast radius. "
            "Be concise and structured."
        ),
        "investigator": (
            "Your job is to investigate the root data and timeline. "
            "Pull all relevant observations, correlate events in chronological order, "
            "and identify the most likely change that introduced the problem. "
            "Reference specific evidence from the blackboard."
        ),
        "analyst": (
            "Your job is to confirm and analyze the root cause. "
            "Validate the investigator's findings with quantitative reasoning. "
            "Calculate impact (how many records, how much revenue, which regions). "
            "If you disagree with any prior finding, say so explicitly with evidence."
        ),
        "commander": (
            "Your job is to synthesize a clear action plan. "
            "Provide: (1) immediate remediation steps, (2) validation steps, (3) preventive measures. "
            "Reference all prior blackboard entries. Be specific and actionable."
        ),
    }
    instruction = role_instructions.get(agent_role, f"Contribute as {agent_role} for the {phase} phase.")

    return f"""{agent_persona}

## Incident Investigation — {phase.upper()} Phase

You are the **{agent_role}**. {instruction}

## Task

{task_description}

## Blackboard (Prior Agent Contributions)

{blackboard_context}

## Your Contribution

Write your {phase} contribution below. Be thorough and structured.
"""


# ---------------------------------------------------------------------------
# Temporal activities
# ---------------------------------------------------------------------------

@activity.defn
async def select_coalition_template(tenant_id: str, chat_session_id: str, task_description: str) -> dict:
    """Select optimal coalition template and resolve roles from the session's AgentKit."""
    from app.models.agent import Agent

    db = SessionLocal()
    try:
        task_lower = task_description.lower()
        pattern = _infer_pattern(task_lower)
        required_roles = _required_roles_for_pattern(pattern)

        def _slug(name): return name.lower().replace(" ", "-")

        agents = db.query(Agent).filter(Agent.tenant_id == UUID(tenant_id)).all()
        role_agent_map = {}

        for role in required_roles:
            match = next((a for a in agents if a.role == role), None)
            if not match:
                match = next((a for a in agents if role in (a.name or "").lower()), None)
            # If no match found, use the first available agent as fallback
            if not match and agents:
                match = agents[0]
            role_agent_map[role] = _slug(match.name) if match else role

        return {
            "template_id": None,
            "pattern": pattern,
            "roles": role_agent_map,
            "name": f"Dynamic {pattern.replace('_', ' ').title()} Team",
        }
    finally:
        db.close()


@activity.defn
async def initialize_collaboration(
    tenant_id: str, chat_session_id: str, template: dict, task_description: str = ""
) -> dict:
    """Create the Shared Blackboard and start the Collaboration Session."""
    from app.services import collaboration_service
    from app.services.collaboration_events import publish_session_event

    db = SessionLocal()
    try:
        board_title = task_description.strip() if task_description.strip() else template["name"]
        board_in = BlackboardCreate(
            title=board_title,
            chat_session_id=UUID(chat_session_id),
        )
        board = blackboard_service.create_blackboard(db, UUID(tenant_id), board_in)

        from app.schemas.collaboration import PATTERN_PHASES
        pattern_phases = PATTERN_PHASES.get(template["pattern"], [])
        collab_in = CollaborationSessionCreate(
            blackboard_id=board.id,
            pattern=template["pattern"],
            role_assignments=template["roles"],
            max_rounds=max(len(pattern_phases), 1),
        )
        session = collaboration_service.create_session(db, UUID(tenant_id), collab_in)

        # Publish session-level event so the frontend session stream picks it up
        agents_list = [
            {"slug": slug, "role": role}
            for role, slug in template["roles"].items()
        ]
        publish_session_event(chat_session_id, "collaboration_started", {
            "collaboration_id": str(session.id),
            "pattern": template["pattern"],
            "phases": pattern_phases,
            "agents": agents_list,
            "blackboard_id": str(board.id),
        })

        return {
            "blackboard_id": str(board.id),
            "collaboration_id": str(session.id),
            "max_rounds": session.max_rounds,
        }
    finally:
        db.close()


@activity.defn
async def prepare_collaboration_step(
    tenant_id: str,
    collaboration_id: str,
    round_index: int,
) -> dict:
    """Read blackboard, build phase prompt, resolve CLI platform.

    Returns a dict suitable for constructing ChatCliInput in the workflow.
    """
    from app.models.collaboration import CollaborationSession
    from app.models.agent import Agent
    from app.models.tenant_features import TenantFeatures
    from app.services.collaboration_events import publish_event
    from app.schemas.collaboration import PHASE_REQUIRED_ROLES

    db = SessionLocal()
    try:
        session = db.query(CollaborationSession).filter(
            CollaborationSession.id == UUID(collaboration_id),
            CollaborationSession.tenant_id == UUID(tenant_id),
        ).first()
        if not session:
            raise ValueError(f"CollaborationSession {collaboration_id} not found")

        current_phase = session.current_phase
        role_assignments = session.role_assignments or {}

        # Find agent slug for current phase's required role
        required_roles = PHASE_REQUIRED_ROLES.get(current_phase, [])
        agent_slug = None
        agent_role = required_roles[0] if required_roles else "contributor"
        for role in required_roles:
            if role in role_assignments:
                agent_slug = role_assignments[role]
                agent_role = role
                break

        # Get agent persona if available — prefer persona_prompt (text), fall back to personality JSON
        agent_persona = ""
        if agent_slug:
            agent = db.query(Agent).filter(
                Agent.tenant_id == UUID(tenant_id),
                Agent.name.ilike(agent_slug.replace("-", " ") + "%"),
            ).first()
            if agent:
                if agent.persona_prompt:
                    agent_persona = agent.persona_prompt
                elif agent.personality:
                    agent_persona = f"You are {agent.name}. {agent.personality.get('description', '')}"

        # Read all blackboard entries for context
        entries = blackboard_service.get_active_entries(db, UUID(tenant_id), session.blackboard_id)
        blackboard_context = _build_blackboard_context(entries)

        # Get original task description from the blackboard title (stored verbatim)
        board = blackboard_service.get_blackboard(db, UUID(tenant_id), session.blackboard_id)
        task_description = board.title if board and board.title else "investigate the incident"

        # Resolve CLI platform via RL routing or tenant default
        try:
            from app.services.rl_routing import get_best_platform
            rec = get_best_platform(db, UUID(tenant_id), task_type="collaboration_step")
            platform = rec.platform or "gemini_cli"
        except Exception:
            features = db.query(TenantFeatures).filter(
                TenantFeatures.tenant_id == UUID(tenant_id)
            ).first()
            platform = (features.default_cli_platform if features else None) or "gemini_cli"

        prompt = _build_phase_prompt(
            phase=current_phase,
            agent_role=agent_role,
            task_description=task_description,
            blackboard_context=blackboard_context,
            agent_persona=agent_persona,
        )

        # Publish phase_started event
        publish_event(collaboration_id, "phase_started", {
            "phase": current_phase,
            "agent_slug": agent_slug or "unknown",
            "agent_role": agent_role,
            "round": round_index + 1,
        })

        return {
            "platform": platform,
            "message": prompt,
            "tenant_id": tenant_id,
            "instruction_md_content": agent_persona,
            "collaboration_id": collaboration_id,
            "agent_slug": agent_slug or "unknown",
            "agent_role": agent_role,
            "current_phase": current_phase,
        }
    finally:
        db.close()


@activity.defn
async def record_collaboration_step(
    tenant_id: str,
    collaboration_id: str,
    response_text: str,
    agent_slug: str,
    agent_role: str,
    current_phase: str,
) -> dict:
    """Write CLI response to blackboard, advance phase, publish Redis events, score async."""
    from app.services import collaboration_service
    from app.services.collaboration_events import publish_event

    db = SessionLocal()
    try:
        result = collaboration_service.advance_phase(
            db,
            UUID(tenant_id),
            UUID(collaboration_id),
            agent_slug=agent_slug,
            contribution=response_text,
            confidence=0.8,
            agrees_with_previous=True,  # Always True for incident_investigation phases
            # Note on outcome: advance_phase calls _find_last_proposal() to set session.outcome.
            # For incident_investigation (no propose/revise entries), _find_last_proposal returns None
            # and advance_phase falls back to using the contribution text as session.outcome.
            # This is correct — the Commander's synthesis becomes the final outcome.
        )
        if not result:
            raise ValueError(f"advance_phase failed for {collaboration_id}")

        # Publish blackboard_entry event
        publish_event(collaboration_id, "blackboard_entry", {
            "entry_id": result.get("entry_id"),
            "entry_type": current_phase,
            "author_slug": agent_slug,
            "author_role": agent_role,
            "content_preview": response_text[:200],
            "content_full": response_text,
            "confidence": 0.8,
            "board_version": result.get("board_version"),
        })

        # Publish phase_completed event
        publish_event(collaboration_id, "phase_completed", {
            "phase": current_phase,
            "agent_slug": agent_slug,
            "entry_id": result.get("entry_id"),
            "board_version": result.get("board_version"),
        })

        # Async quality scoring — fire and forget
        try:
            from app.services.auto_quality_scorer import score_and_log_async
            score_and_log_async(
                tenant_id=tenant_id,
                response_text=response_text,
                decision_point="collaboration_step",
                metadata={"phase": current_phase, "agent_slug": agent_slug},
            )
        except Exception as e:
            logger.debug("Quality scoring skipped: %s", e)

        return {
            "consensus_reached": result.get("status") == "completed",
            "phase_completed": current_phase,
            "board_version": result.get("board_version"),
        }
    finally:
        db.close()


@activity.defn
async def finalize_collaboration(tenant_id: str, collaboration_id: str) -> str:
    """Conclude the collaboration and publish the final report."""
    from app.models.collaboration import CollaborationSession
    from app.services.collaboration_events import publish_event, publish_session_event

    db = SessionLocal()
    try:
        session = db.query(CollaborationSession).filter(
            CollaborationSession.id == UUID(collaboration_id),
            CollaborationSession.tenant_id == UUID(tenant_id),
        ).first()

        final_report = session.outcome or "Collaboration complete. See blackboard for full agent reasoning."

        # Get chat_session_id from blackboard
        board = blackboard_service.get_blackboard(db, UUID(tenant_id), session.blackboard_id)
        chat_session_id = str(board.chat_session_id) if board and board.chat_session_id else None

        publish_event(collaboration_id, "collaboration_completed", {
            "collaboration_id": collaboration_id,
            "consensus": session.consensus_reached or "yes",
            "rounds": session.rounds_completed,
            "final_report": final_report,
        })

        if chat_session_id:
            publish_session_event(chat_session_id, "collaboration_completed", {
                "collaboration_id": collaboration_id,
                "final_report": final_report,
            })

        return final_report
    finally:
        db.close()
