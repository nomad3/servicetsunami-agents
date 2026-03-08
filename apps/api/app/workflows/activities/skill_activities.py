"""Reusable Temporal activities for skill execution."""
import uuid
import logging

from temporalio import activity

logger = logging.getLogger(__name__)


@activity.defn
async def execute_skill(tenant_id: str, skill_name: str, entity_id: str, params: dict) -> dict:
    """Execute a skill by name on an entity.

    This activity is importable from ANY workflow -- prospecting, deal pipeline,
    knowledge extraction, etc.
    """
    logger.info("execute_skill: tenant=%s skill=%s entity=%s", tenant_id, skill_name, entity_id)

    # Import here to avoid circular imports at module level
    from app.db.session import SessionLocal
    from app.services.skills import get_skill_by_name, execute_skill as run_skill

    db = SessionLocal()
    try:
        tid = uuid.UUID(tenant_id)
        eid = uuid.UUID(entity_id)

        skill = get_skill_by_name(db, skill_name, tid)
        if not skill:
            return {"error": f"Skill '{skill_name}' not found", "status": "failed"}

        result = run_skill(db, skill.id, tid, eid, params)
        return {"status": "completed", "output": result}
    except Exception as e:
        logger.error("execute_skill failed: %s", e)
        return {"error": str(e), "status": "failed"}
    finally:
        db.close()
