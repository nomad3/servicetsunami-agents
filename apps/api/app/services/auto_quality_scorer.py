"""Auto-quality scorer — rates every agent response using local Ollama model.

Runs asynchronously after each chat response is returned to the user.
Feeds scores back into the RL system as implicit rewards.

This replaces manual thumbs up/down as the primary training signal,
increasing RL data from ~43 manual ratings to hundreds of auto-scored
experiences per day.
"""

import asyncio
import logging
import uuid
from typing import Optional

from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)


def score_and_log_async(
    tenant_id: uuid.UUID,
    user_message: str,
    agent_response: str,
    trajectory_id: Optional[uuid.UUID] = None,
):
    """Fire-and-forget: score response quality and log RL reward.

    Call this AFTER returning the response to the user.
    Runs in background — never blocks the response.
    """
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            loop.create_task(_score_and_log(tenant_id, user_message, agent_response, trajectory_id))
        else:
            asyncio.run(_score_and_log(tenant_id, user_message, agent_response, trajectory_id))
    except RuntimeError:
        # No event loop — run synchronously in a new thread
        import threading
        threading.Thread(
            target=lambda: asyncio.run(_score_and_log(tenant_id, user_message, agent_response, trajectory_id)),
            daemon=True,
        ).start()


async def _score_and_log(
    tenant_id: uuid.UUID,
    user_message: str,
    agent_response: str,
    trajectory_id: Optional[uuid.UUID] = None,
):
    """Score the response and log as RL reward."""
    from app.services.local_inference import score_response_quality, is_available

    # Check if Ollama is available
    if not await is_available():
        logger.debug("Ollama not available — skipping auto-quality scoring")
        return

    # Score the response
    result = await score_response_quality(user_message, agent_response)
    if not result:
        logger.debug("Auto-quality scoring returned no result")
        return

    score = result["score"]
    reasoning = result.get("reasoning", "")

    # Map 1-5 score to RL reward: 1→-1.0, 2→-0.5, 3→0.0, 4→+0.5, 5→+1.0
    reward = (score - 3) / 2.0

    logger.info(
        "Auto-quality score: %d/5 (reward=%.2f) — %s",
        score, reward, reasoning[:80],
    )

    # Log as RL experience
    try:
        from app.db.session import SessionLocal
        from app.services import rl_experience_service

        db = SessionLocal()
        try:
            rl_experience_service.log_experience(
                db,
                tenant_id=tenant_id,
                trajectory_id=trajectory_id or uuid.uuid4(),
                step_index=0,
                decision_point="response_generation",
                state={
                    "user_message": user_message[:200],
                    "response_length": len(agent_response),
                },
                action={
                    "response_preview": agent_response[:100],
                },
                state_text=f"User: {user_message[:100]} → Response: {agent_response[:100]}",
                reward=reward,
                reward_source="auto_quality",
                reward_components={
                    "score": score,
                    "reasoning": reasoning,
                    "model": result.get("model", ""),
                },
            )
        finally:
            db.close()
    except Exception as e:
        logger.debug("Failed to log auto-quality RL experience: %s", e)
