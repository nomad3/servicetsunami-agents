"""API routes for knowledge graph"""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session
from typing import List, Optional
import uuid

from app.api.deps import get_db, get_current_user
from app.models.user import User
from app.schemas.knowledge_entity import (
    KnowledgeEntity, KnowledgeEntityCreate, KnowledgeEntityUpdate,
    KnowledgeEntityBulkCreate, KnowledgeEntityBulkResponse, CollectionSummary,
)
from app.schemas.knowledge_relation import KnowledgeRelation, KnowledgeRelationCreate, KnowledgeRelationWithEntities
from app.services import knowledge as service

router = APIRouter()


@router.get("/quality-stats")
def get_quality_stats(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Entity quality statistics: total entities, embedding coverage, top/bottom by usefulness, per-platform extraction stats."""
    return service.get_quality_stats(db, current_user.tenant_id)


@router.get("/scoring-rubrics")
def list_scoring_rubrics(current_user: User = Depends(get_current_user)):
    """List all available scoring rubrics."""
    from app.services.scoring_rubrics import list_rubrics
    return list_rubrics()


# Entity endpoints
@router.post("/entities", response_model=KnowledgeEntity, status_code=201)
def create_entity(
    entity_in: KnowledgeEntityCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """Create a new knowledge entity."""
    return service.create_entity(db, entity_in, current_user.tenant_id)


@router.get("/entities", response_model=List[KnowledgeEntity])
def list_entities(
    entity_type: Optional[str] = None,
    category: Optional[str] = None,
    status: Optional[str] = None,
    task_id: Optional[uuid.UUID] = None,
    skip: int = 0,
    limit: int = 100,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """List entities with optional filters."""
    return service.get_entities(
        db, current_user.tenant_id, entity_type, skip, limit,
        status=status, task_id=task_id, category=category,
    )


@router.get("/entities/search", response_model=List[KnowledgeEntity])
def search_entities(
    q: str,
    entity_type: Optional[str] = None,
    category: Optional[str] = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """Search entities by name."""
    return service.search_entities(db, current_user.tenant_id, q, entity_type, category=category)


@router.post("/entities/bulk", response_model=KnowledgeEntityBulkResponse, status_code=201)
def bulk_create_entities(
    bulk_in: KnowledgeEntityBulkCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """Bulk create entities with dedup."""
    return service.bulk_create_entities(db, bulk_in.entities, current_user.tenant_id)


@router.get("/entities/{entity_id}", response_model=KnowledgeEntity)
def get_entity(
    entity_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """Get entity by ID."""
    entity = service.get_entity(db, entity_id, current_user.tenant_id)
    if not entity:
        raise HTTPException(status_code=404, detail="Entity not found")
    return entity


@router.put("/entities/{entity_id}", response_model=KnowledgeEntity)
def update_entity(
    entity_id: uuid.UUID,
    entity_in: KnowledgeEntityUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """Update an entity."""
    entity = service.update_entity(db, entity_id, current_user.tenant_id, entity_in)
    if not entity:
        raise HTTPException(status_code=404, detail="Entity not found")
    return entity


@router.delete("/entities/{entity_id}", status_code=204)
def delete_entity(
    entity_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """Delete an entity and its relations."""
    if not service.delete_entity(db, entity_id, current_user.tenant_id):
        raise HTTPException(status_code=404, detail="Entity not found")


@router.post("/entities/{entity_id}/score")
def score_entity(
    entity_id: uuid.UUID,
    rubric_id: str = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """Compute and store a lead score for an entity using a configurable rubric."""
    result = service.score_entity(db, entity_id, current_user.tenant_id, rubric_id=rubric_id)
    if not result:
        raise HTTPException(status_code=404, detail="Entity not found or scoring failed")
    return result


@router.post("/entities/score/batch")
def score_entities_batch(
    limit: int = 50,
    rubric_id: str = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Score all unscored entities for the tenant."""
    from app.models.knowledge_entity import KnowledgeEntity as KE

    entities = (
        db.query(KE)
        .filter(
            KE.tenant_id == current_user.tenant_id,
            KE.score.is_(None),
            KE.status != "archived",
        )
        .limit(limit)
        .all()
    )

    results = []
    for entity in entities:
        result = service.score_entity(
            db, entity.id, current_user.tenant_id, rubric_id=rubric_id,
        )
        if result:
            results.append(result)

    return {"scored": len(results), "results": results}


@router.put("/entities/{entity_id}/status", response_model=KnowledgeEntity)
def update_entity_status(
    entity_id: uuid.UUID,
    status_update: dict,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """Update entity lifecycle status."""
    new_status = status_update.get("status")
    if not new_status:
        raise HTTPException(status_code=400, detail="'status' field required")
    entity = service.update_entity_status(db, entity_id, current_user.tenant_id, new_status)
    if not entity:
        raise HTTPException(status_code=404, detail="Entity not found or invalid status")
    return entity


@router.get("/collections/{task_id}/summary", response_model=CollectionSummary)
def get_collection_summary(
    task_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """Get collection summary for a task."""
    return service.get_collection_summary(db, task_id, current_user.tenant_id)


# Relation endpoints
@router.get("/relations", response_model=List[KnowledgeRelationWithEntities])
def list_relations(
    relation_type: Optional[str] = None,
    skip: int = 0,
    limit: int = 100,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """List all relations for the tenant with entity names."""
    relations = service.get_all_relations(
        db, current_user.tenant_id, relation_type, skip, limit
    )
    results = []
    for rel in relations:
        base = KnowledgeRelation.model_validate(rel)
        data = KnowledgeRelationWithEntities(
            **base.model_dump(),
            from_entity_name=rel.from_entity.name if rel.from_entity else None,
            from_entity_category=rel.from_entity.category if rel.from_entity else None,
            to_entity_name=rel.to_entity.name if rel.to_entity else None,
            to_entity_category=rel.to_entity.category if rel.to_entity else None,
        )
        results.append(data)
    return results


@router.post("/relations", response_model=KnowledgeRelation, status_code=201)
def create_relation(
    relation_in: KnowledgeRelationCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """Create a relation between entities."""
    try:
        return service.create_relation(db, relation_in, current_user.tenant_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/entities/{entity_id}/relations", response_model=List[KnowledgeRelation])
def get_entity_relations(
    entity_id: uuid.UUID,
    direction: str = "both",
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """Get all relations for an entity."""
    return service.get_entity_relations(db, entity_id, current_user.tenant_id, direction)


@router.delete("/relations/{relation_id}", status_code=204)
def delete_relation(
    relation_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """Delete a relation."""
    if not service.delete_relation(db, relation_id, current_user.tenant_id):
        raise HTTPException(status_code=404, detail="Relation not found")


# ---------------------------------------------------------------------------
# Git History / PR Outcome
# ---------------------------------------------------------------------------

class PROutcomeRequest(BaseModel):
    repo: str
    pr_number: int
    outcome: str  # merged, closed, reverted
    title: str = ""
    review_comments: List[str] = []


@router.post("/pr-outcome")
def report_pr_outcome(
    payload: PROutcomeRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Report a PR outcome for RL reward assignment and knowledge observation.

    Called by code-worker or nightly polling when a PR is merged/closed/reverted.
    Stores a git_pr observation and returns the suggested RL reward.
    """
    if payload.outcome not in ("merged", "closed", "reverted"):
        raise HTTPException(status_code=400, detail="outcome must be merged, closed, or reverted")

    result = service.store_pr_outcome(
        db,
        tenant_id=current_user.tenant_id,
        repo=payload.repo,
        pr_number=payload.pr_number,
        outcome=payload.outcome,
        title=payload.title,
        review_comments=payload.review_comments,
    )

    # Try to assign RL reward to the code_task experience
    try:
        from app.services import rl_experience_service
        from sqlalchemy import text as sql_text

        exp = db.execute(
            sql_text("""
                SELECT id FROM rl_experiences
                WHERE tenant_id = CAST(:tid AS uuid)
                AND decision_point = 'code_task'
                AND (state->>'pr_number')::int = :pr_num
                AND reward IS NULL
                ORDER BY created_at DESC LIMIT 1
            """),
            {
                "tid": str(current_user.tenant_id),
                "pr_num": payload.pr_number,
            },
        ).fetchone()

        if exp:
            rl_experience_service.assign_reward(
                db,
                experience_id=exp.id,
                reward=result["rl_reward"],
                reward_components={
                    "pr_outcome": payload.outcome,
                    "pr_number": payload.pr_number,
                    "review_count": len(payload.review_comments),
                },
                reward_source="git_pr_outcome",
            )
            result["rl_experience_rewarded"] = True
    except Exception:
        pass

    return result


@router.get("/git-context")
def get_git_context(
    q: str = "",
    limit: int = 10,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Get recent git context (commits, PRs, hotspots) relevant to a query."""
    from app.services.memory_recall import get_recent_git_context
    return get_recent_git_context(db, current_user.tenant_id, q, limit=limit)


# ---------------------------------------------------------------------------
# Embedding Backfill
# ---------------------------------------------------------------------------

@router.post("/backfill-embeddings")
async def trigger_backfill(
    current_user: User = Depends(get_current_user),
):
    """Start an embedding backfill workflow (admin only)."""
    from app.services.dynamic_workflow_launcher import start_dynamic_workflow_by_name

    temporal_wf_id = await start_dynamic_workflow_by_name(
        "Embedding Backfill", str(current_user.tenant_id),
    )
    return {"workflow_id": temporal_wf_id, "status": "started"}


# ---------------------------------------------------------------------------
# Memory Consolidation
# ---------------------------------------------------------------------------

@router.post("/consolidation/start")
async def start_consolidation(
    current_user: User = Depends(get_current_user),
):
    """Start the nightly memory consolidation workflow."""
    from app.services.dynamic_workflow_launcher import start_dynamic_workflow_by_name

    temporal_wf_id = await start_dynamic_workflow_by_name(
        "Memory Consolidation", str(current_user.tenant_id),
    )
    return {"workflow_id": temporal_wf_id, "status": "started"}


@router.post("/consolidation/stop")
async def stop_consolidation(
    current_user: User = Depends(get_current_user),
):
    """Stop the memory consolidation workflow."""
    from temporalio.client import Client
    from app.core.config import settings as app_settings

    client = await Client.connect(app_settings.TEMPORAL_ADDRESS)
    workflow_id = f"memory-consolidation-{current_user.tenant_id}"
    try:
        handle = client.get_workflow_handle(workflow_id)
        await handle.cancel()
        return {"status": "stopped"}
    except Exception as e:
        return {"status": "not_running", "error": str(e)}


@router.get("/consolidation/status")
async def get_consolidation_status(
    current_user: User = Depends(get_current_user),
):
    """Check if the consolidation workflow is running."""
    from temporalio.client import Client
    from app.core.config import settings as app_settings

    client = await Client.connect(app_settings.TEMPORAL_ADDRESS)
    workflow_id = f"memory-consolidation-{current_user.tenant_id}"
    try:
        handle = client.get_workflow_handle(workflow_id)
        desc = await handle.describe()
        return {"status": str(desc.status), "workflow_id": workflow_id}
    except Exception:
        return {"status": "not_running"}
