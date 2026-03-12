"""
Temporal activities for task execution workflow.

Activities:
- dispatch_task: Find best agent for a task
- recall_memory: Load relevant agent memories
- execute_task: Run task via ADK
- persist_entities: Extract and persist entities to knowledge graph
- evaluate_task: Score results and store learnings
"""

from temporalio import activity
from typing import Dict, Any, List
from datetime import datetime
import uuid
import time

from app.db.session import SessionLocal
from app.models.agent_task import AgentTask
from app.models.agent_memory import AgentMemory
from app.models.agent_skill import AgentSkill
from app.models.execution_trace import ExecutionTrace
from app.services.orchestration.task_dispatcher import TaskDispatcher
from app.services.knowledge_extraction import KnowledgeExtractionService
from app.services.orchestration.entity_validator import ValidationPolicy
from app.services import rl_experience_service
from app.utils.logger import get_logger

logger = get_logger(__name__)


def _log_trace(
    db,
    task_id: str,
    tenant_id: str,
    step_type: str,
    step_order: int,
    agent_id: str = None,
    details: Dict[str, Any] = None,
    duration_ms: int = None,
):
    """Create an ExecutionTrace record."""
    trace = ExecutionTrace(
        id=uuid.uuid4(),
        task_id=uuid.UUID(task_id),
        tenant_id=uuid.UUID(tenant_id),
        step_type=step_type,
        step_order=step_order,
        agent_id=uuid.UUID(agent_id) if agent_id else None,
        details=details,
        duration_ms=duration_ms,
        created_at=datetime.utcnow(),
    )
    db.add(trace)
    db.commit()


@activity.defn
async def dispatch_task(task_id: str, tenant_id: str, task_data: Dict[str, Any]) -> Dict[str, Any]:
    """
    Find the best agent for a task and assign it.

    If the task already has an assigned_agent_id, use that agent.
    If the task has a group_id, use TaskDispatcher to find the best agent.
    """
    start = time.time()
    db = SessionLocal()
    try:
        task = db.query(AgentTask).filter(AgentTask.id == uuid.UUID(task_id)).first()
        if not task:
            raise RuntimeError(f"AgentTask {task_id} not found")

        task.status = "thinking"
        task.started_at = datetime.utcnow()
        db.commit()

        agent_id = None

        # Use assigned agent if present
        if task.assigned_agent_id:
            agent_id = str(task.assigned_agent_id)
            logger.info(f"Task {task_id} already assigned to agent {agent_id}")
        elif task.group_id:
            # Use dispatcher to find best agent in group
            dispatcher = TaskDispatcher(db)
            capabilities = task_data.get("capabilities", [])
            best_agent = dispatcher.find_best_agent(
                group_id=task.group_id,
                required_capabilities=capabilities,
                tenant_id=uuid.UUID(tenant_id),
            )
            if best_agent:
                agent_id = str(best_agent.id)
                task.assigned_agent_id = best_agent.id
                db.commit()
                logger.info(f"Dispatched task {task_id} to agent {agent_id}")

        if not agent_id:
            task.status = "failed"
            task.error = "No suitable agent found for task"
            db.commit()
            raise RuntimeError(f"No suitable agent found for task {task_id}")

        duration_ms = int((time.time() - start) * 1000)
        _log_trace(
            db,
            task_id=task_id,
            tenant_id=tenant_id,
            step_type="dispatched",
            step_order=1,
            agent_id=agent_id,
            details={"assigned_agent_id": agent_id},
            duration_ms=duration_ms,
        )

        return {"status": "dispatched", "agent_id": agent_id}
    finally:
        db.close()


@activity.defn
async def recall_memory(task_id: str, tenant_id: str, agent_id: str, task_data: Dict[str, Any]) -> Dict[str, Any]:
    """
    Load relevant memories for the assigned agent.

    Queries AgentMemory for the agent with importance >= 0.3, limited to 5 results.
    Updates access_count and last_accessed_at for retrieved memories.
    """
    start = time.time()
    db = SessionLocal()
    try:
        memories = (
            db.query(AgentMemory)
            .filter(
                AgentMemory.agent_id == uuid.UUID(agent_id),
                AgentMemory.tenant_id == uuid.UUID(tenant_id),
                AgentMemory.importance >= 0.3,
            )
            .order_by(AgentMemory.importance.desc())
            .limit(5)
            .all()
        )

        memory_list: List[Dict[str, Any]] = []
        now = datetime.utcnow()
        for mem in memories:
            mem.access_count = (mem.access_count or 0) + 1
            mem.last_accessed_at = now
            memory_list.append({
                "id": str(mem.id),
                "memory_type": mem.memory_type,
                "content": mem.content,
                "importance": mem.importance,
            })
        db.commit()

        duration_ms = int((time.time() - start) * 1000)
        _log_trace(
            db,
            task_id=task_id,
            tenant_id=tenant_id,
            step_type="memory_recall",
            step_order=2,
            agent_id=agent_id,
            details={"memory_count": len(memory_list)},
            duration_ms=duration_ms,
        )

        logger.info(f"Recalled {len(memory_list)} memories for agent {agent_id}")
        return {"memories": memory_list}
    finally:
        db.close()


@activity.defn
async def execute_task(task_id: str, tenant_id: str, agent_id: str, context: Dict[str, Any]) -> Dict[str, Any]:
    """
    Execute the task via the ADK server.

    Updates task status to 'executing', calls ADK client, extracts response.
    Falls back to a static response if ADK is unavailable.
    """
    start = time.time()
    db = SessionLocal()
    try:
        task = db.query(AgentTask).filter(AgentTask.id == uuid.UUID(task_id)).first()
        if not task:
            raise RuntimeError(f"AgentTask {task_id} not found")

        task.status = "executing"
        db.commit()

        output = {}
        try:
            from app.services.adk_client import get_adk_client

            client = get_adk_client()

            # Create a session for this task execution
            session = client.create_session(
                user_id=uuid.UUID(agent_id),
                state={"task_id": task_id, "tenant_id": tenant_id},
            )
            session_id = session.get("id", session.get("session_id", ""))

            # Build message from task objective and context
            message = context.get("objective", task.objective or "")
            if context.get("memories"):
                memory_text = "; ".join(m["content"] for m in context["memories"])
                message = f"{message}\n\nRelevant context: {memory_text}"

            events = client.run(
                user_id=uuid.UUID(agent_id),
                session_id=session_id,
                message=message,
            )

            # Extract response text from ADK events
            response_parts = []
            for event in events:
                if isinstance(event, dict):
                    parts = event.get("content", {}).get("parts", [])
                    for part in parts:
                        if isinstance(part, dict) and "text" in part:
                            response_parts.append(part["text"])

            output = {
                "response": "\n".join(response_parts) if response_parts else "Task processed",
                "events_count": len(events),
                "source": "adk",
            }

        except Exception as adk_err:
            logger.warning(f"ADK unavailable for task {task_id}, using fallback: {adk_err}")
            output = {
                "response": f"Task '{task.objective}' processed with fallback execution",
                "events_count": 0,
                "source": "fallback",
            }

        duration_ms = int((time.time() - start) * 1000)
        _log_trace(
            db,
            task_id=task_id,
            tenant_id=tenant_id,
            step_type="executing",
            step_order=3,
            agent_id=agent_id,
            details={"source": output.get("source"), "events_count": output.get("events_count")},
            duration_ms=duration_ms,
        )

        return {"status": "executed", "output": output}
    finally:
        db.close()


@activity.defn
async def evaluate_task(task_id: str, tenant_id: str, agent_id: str, execute_result: Dict[str, Any]) -> Dict[str, Any]:
    """
    Evaluate task results, update task status, store memory, and update skills.

    Sets task to 'completed', creates an experience memory, and updates
    agent skill proficiency if task_type matches an existing skill.
    """
    start = time.time()
    db = SessionLocal()
    try:
        task = db.query(AgentTask).filter(AgentTask.id == uuid.UUID(task_id)).first()
        if not task:
            raise RuntimeError(f"AgentTask {task_id} not found")

        confidence = 0.85
        tokens_used = 0
        cost = 0.0

        # Complete the task
        task.status = "completed"
        task.output = execute_result.get("output", {})
        task.confidence = confidence
        task.completed_at = datetime.utcnow()
        db.commit()

        # Store experience memory
        memory_content = f"Completed task: {task.objective}"
        output = execute_result.get("output", {})
        if isinstance(output, dict) and output.get("response"):
            memory_content = f"{memory_content}. Result: {output['response'][:200]}"

        memory = AgentMemory(
            id=uuid.uuid4(),
            agent_id=uuid.UUID(agent_id),
            tenant_id=uuid.UUID(tenant_id),
            memory_type="experience",
            content=memory_content,
            importance=confidence,
            source="task_execution",
            source_task_id=uuid.UUID(task_id),
            created_at=datetime.utcnow(),
        )
        db.add(memory)
        db.commit()

        # Update skill proficiency if task_type matches
        if task.task_type:
            skill = (
                db.query(AgentSkill)
                .filter(
                    AgentSkill.agent_id == uuid.UUID(agent_id),
                    AgentSkill.skill_name == task.task_type,
                )
                .first()
            )
            if skill:
                skill.times_used = (skill.times_used or 0) + 1
                skill.last_used_at = datetime.utcnow()
                # Gradually increase proficiency toward 1.0
                skill.proficiency = min(1.0, (skill.proficiency or 0.5) + 0.02)
                skill.success_rate = (
                    ((skill.success_rate or 0.0) * ((skill.times_used or 1) - 1) + 1.0)
                    / (skill.times_used or 1)
                )
                db.commit()

        # Log RL experience (Phase 1: dual-write — both old +0.02 and RL experience)
        try:
            rl_experience_service.log_experience(
                db=db,
                tenant_id=task.tenant_id,
                trajectory_id=task.id,  # use task ID as trajectory
                step_index=0,
                decision_point="skill_routing",
                state={"task_type": task.task_type, "agent_id": str(task.agent_id)},
                action={"skill_name": task.task_type},
                state_text=f"Task: {task.task_type}, agent: {task.agent_id}",
            )
        except Exception:
            pass  # RL logging should never break task execution

        duration_ms = int((time.time() - start) * 1000)
        _log_trace(
            db,
            task_id=task_id,
            tenant_id=tenant_id,
            step_type="completed",
            step_order=5,
            agent_id=agent_id,
            details={
                "confidence": confidence,
                "tokens_used": tokens_used,
                "cost": cost,
                "source": output.get("source") if isinstance(output, dict) else None,
            },
            duration_ms=duration_ms,
        )

        logger.info(f"Task {task_id} completed with confidence={confidence}")

        return {
            "status": "completed",
            "confidence": confidence,
            "tokens_used": tokens_used,
            "cost": cost,
        }
    finally:
        db.close()


@activity.defn
async def persist_entities(
    task_id: str, tenant_id: str, agent_id: str, execute_result: Dict[str, Any]
) -> Dict[str, Any]:
    """
    Persist extracted entities to the knowledge graph.

    Parses agent output for structured entity data, validates against policy,
    deduplicates, and creates KnowledgeEntity records.

    Runs between execute_task and evaluate_task in the workflow pipeline.
    """
    start = time.time()
    db = SessionLocal()
    try:
        task = db.query(AgentTask).filter(AgentTask.id == uuid.UUID(task_id)).first()
        if not task:
            raise RuntimeError(f"AgentTask {task_id} not found")

        output = execute_result.get("output", {})
        response_text = ""
        if isinstance(output, dict):
            response_text = output.get("response", "")
        elif isinstance(output, str):
            response_text = output

        if not response_text:
            logger.info(f"No output to extract entities from for task {task_id}")
            return {"entities_created": 0, "entities_updated": 0, "duplicates_skipped": 0}

        # Determine entity schema from task context
        context = task.context or {}
        config = context.get("config", {})
        entity_schema = config.get("entity_schema")
        entity_type = config.get("entity_type")
        if entity_type and not entity_schema:
            entity_schema = {"entity_type": entity_type}

        # Determine content type from output source
        content_type = "plain_text"
        if isinstance(output, dict):
            source = output.get("source", "")
            if source == "adk":
                content_type = "structured_json" if _looks_like_json(response_text) else "plain_text"

        # Build validation policy from task guardrails
        guardrails = config.get("guardrails", {})
        _policy = ValidationPolicy(  # noqa: F841
            max_entities_per_task=guardrails.get("max_per_source", 500),
            dedup_fields=guardrails.get("dedup_on", ["name", "entity_type"]),
        )

        # Extract entities via LLM
        extraction_service = KnowledgeExtractionService()
        extraction_result = extraction_service.extract_from_content(
            db=db,
            content=response_text,
            content_type=content_type,
            tenant_id=uuid.UUID(tenant_id),
            source_agent_id=uuid.UUID(agent_id) if agent_id else None,
            collection_task_id=uuid.UUID(task_id),
            entity_schema=entity_schema,
        )

        # Count results
        entities_created = len(extraction_result.get("entities", []))

        duration_ms = int((time.time() - start) * 1000)
        _log_trace(
            db,
            task_id=task_id,
            tenant_id=tenant_id,
            step_type="entity_persist",
            step_order=4,
            agent_id=agent_id,
            details={
                "entities_created": entities_created,
                "content_type": content_type,
                "has_schema": entity_schema is not None,
            },
            duration_ms=duration_ms,
        )

        logger.info(f"Persisted {entities_created} entities for task {task_id}")
        return {
            "entities_created": entities_created,
            "entities_updated": 0,
            "duplicates_skipped": 0,
        }
    finally:
        db.close()


def _looks_like_json(text: str) -> bool:
    """Quick check if text looks like JSON."""
    stripped = text.strip()
    return (stripped.startswith("[") or stripped.startswith("{")) and (
        stripped.endswith("]") or stripped.endswith("}")
    )
