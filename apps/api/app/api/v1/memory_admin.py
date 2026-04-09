"""Internal admin endpoints for memory management."""
from fastapi import APIRouter, Header, HTTPException
from temporalio.client import Client
from app.core.config import settings

router = APIRouter(prefix="/internal/memory", tags=["internal"])


@router.post("/backfill/{tenant_id}")
async def backfill_embeddings(
    tenant_id: str,
    x_internal_key: str = Header(..., alias="X-Internal-Key"),
):
    """Trigger the BackfillEmbeddingsWorkflow for a specific tenant."""
    if x_internal_key != settings.API_INTERNAL_KEY:
        raise HTTPException(status_code=401, detail="Invalid internal key")
        
    try:
        client = await Client.connect(settings.TEMPORAL_ADDRESS)
        handle = await client.start_workflow(
            "BackfillEmbeddingsWorkflow",
            args=[tenant_id],
            id=f"backfill-embeddings-{tenant_id}",
            task_queue="servicetsunami-orchestration",
        )
        return {
            "status": "triggered",
            "workflow_id": handle.id,
            "tenant_id": tenant_id
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to start backfill workflow: {e}")
