"""Local ML API — manage Ollama models and inference."""

import asyncio
from fastapi import APIRouter, Depends
from app.api import deps

router = APIRouter()


@router.get("/status")
async def get_status(current_user=Depends(deps.get_current_active_user)):
    """Check local ML inference availability."""
    from app.services.local_inference import is_available, list_models, OLLAMA_BASE_URL
    available = await is_available()
    models = await list_models() if available else []
    return {
        "available": available,
        "ollama_url": OLLAMA_BASE_URL,
        "models": models,
        "model_count": len(models),
    }


@router.post("/pull")
async def pull_model(
    model_name: str = "gemma4",
    current_user=Depends(deps.get_current_active_user),
):
    """Pull a model to Ollama."""
    from app.services.local_inference import pull_model
    success = await pull_model(model_name)
    return {"model": model_name, "success": success}


@router.post("/score-test")
async def test_quality_scoring(
    user_message: str = "What is 2+2?",
    agent_response: str = "4",
    current_user=Depends(deps.get_current_active_user),
):
    """Test the auto-quality scorer."""
    from app.services.local_inference import score_response_quality
    result = await score_response_quality(user_message, agent_response)
    return {"result": result}
