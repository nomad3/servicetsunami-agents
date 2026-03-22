"""Auto-quality scorer — rates every agent response using local Ollama model.

Uses the agent_response_quality rubric from scoring_rubrics.py for
multi-dimensional scoring (accuracy, helpfulness, tool_usage, memory_usage,
efficiency, context_awareness) plus cost efficiency tracking per platform.

Runs asynchronously after each chat response is returned to the user.
"""

import asyncio
import logging
import os
import threading
import uuid
from typing import Optional

logger = logging.getLogger(__name__)


def score_and_log_async(
    tenant_id: uuid.UUID,
    user_message: str,
    agent_response: str,
    trajectory_id: Optional[uuid.UUID] = None,
    platform: str = "claude_code",
    agent_slug: str = "luna",
    task_type: str = "general",
    channel: str = "web",
    tokens_used: int = 0,
    response_time_ms: int = 0,
    cost_usd: float = 0.0,
    tools_called: list = None,
    entities_recalled: list = None,
):
    """Fire-and-forget: score response quality and log RL reward.

    Call this AFTER returning the response to the user.
    Runs in background thread — never blocks the response.
    """
    threading.Thread(
        target=lambda: asyncio.run(_score_and_log(
            tenant_id, user_message, agent_response, trajectory_id,
            platform, agent_slug, task_type, channel,
            tokens_used, response_time_ms, cost_usd,
            tools_called or [], entities_recalled or [],
        )),
        daemon=True,
    ).start()


async def _score_and_log(
    tenant_id: uuid.UUID,
    user_message: str,
    agent_response: str,
    trajectory_id: Optional[uuid.UUID],
    platform: str,
    agent_slug: str,
    task_type: str,
    channel: str,
    tokens_used: int,
    response_time_ms: int,
    cost_usd: float,
    tools_called: list,
    entities_recalled: list,
):
    """Score the response with multi-dimensional rubric and log as RL reward."""
    from app.services.local_inference import is_available, generate

    logger.info("Auto-quality scorer: starting for tenant %s (platform=%s)", str(tenant_id)[:8], platform)

    if not await is_available():
        logger.info("Auto-quality scorer: Ollama not available — skipping")
        return

    # Build the rubric prompt with full context
    from app.services.scoring_rubrics import get_rubric
    rubric = get_rubric("agent_response_quality")
    if not rubric:
        logger.warning("agent_response_quality rubric not found")
        return

    prompt = rubric["prompt_template"].format(
        platform=platform,
        agent_slug=agent_slug,
        task_type=task_type,
        channel=channel,
        tokens_used=tokens_used,
        response_time_ms=response_time_ms,
        cost_usd=f"{cost_usd:.4f}" if cost_usd else "0.0000",
        user_message=user_message[:500],
        agent_response=agent_response[:1000],
        tools_called=", ".join(tools_called[:10]) if tools_called else "none",
        entities_recalled=", ".join(str(e) for e in entities_recalled[:5]) if entities_recalled else "none",
    )

    raw = await generate(
        prompt=prompt,
        model=os.environ.get("QUALITY_MODEL", "qwen2.5-coder:1.5b"),
        system=rubric["system_prompt"],
        temperature=0.1,
        max_tokens=300,
    )

    if not raw:
        logger.debug("Auto-quality scoring returned no result")
        return

    # Parse JSON response
    import json
    try:
        json_str = raw[raw.index('{'):raw.rindex('}') + 1]
        data = json.loads(json_str)
    except (json.JSONDecodeError, ValueError):
        logger.debug("Failed to parse quality score from: %s", raw[:100])
        return

    score = max(0, min(100, int(data.get("score", 50))))
    breakdown = data.get("breakdown", {})
    cost_efficiency = data.get("cost_efficiency", {})
    reasoning = str(data.get("reasoning", ""))[:300]

    # Map 0-100 score to RL reward: 0→-1.0, 50→0.0, 100→+1.0
    reward = (score - 50) / 50.0

    logger.info(
        "Auto-quality: %d/100 (reward=%.2f) platform=%s tokens=%d — %s",
        score, reward, platform, tokens_used, reasoning[:80],
    )

    # Log as RL experience with full breakdown
    try:
        from app.db.session import SessionLocal
        from app.services import rl_experience_service

        db = SessionLocal()
        try:
            exp = rl_experience_service.log_experience(
                db,
                tenant_id=tenant_id,
                trajectory_id=trajectory_id or uuid.uuid4(),
                step_index=0,
                decision_point="response_generation",
                state={
                    "user_message": user_message[:200],
                    "response_length": len(agent_response),
                    "platform": platform,
                    "agent_slug": agent_slug,
                    "task_type": task_type,
                    "channel": channel,
                    "tokens_used": tokens_used,
                    "response_time_ms": response_time_ms,
                    "cost_usd": cost_usd,
                    "tools_called": tools_called[:5],
                    "entities_recalled_count": len(entities_recalled),
                },
                action={
                    "response_preview": agent_response[:100],
                    "platform": platform,
                    "agent_slug": agent_slug,
                },
                state_text=(
                    f"platform={platform} agent={agent_slug} task={task_type} "
                    f"tokens={tokens_used} cost=${cost_usd:.4f} "
                    f"tools=[{','.join(tools_called[:3])}] "
                    f"User: {user_message[:80]}"
                ),
            )
            rl_experience_service.assign_reward(
                db,
                experience_id=exp.id,
                reward=reward,
                reward_components={
                    "score": score,
                    "breakdown": breakdown,
                    "cost_efficiency": cost_efficiency,
                    "reasoning": reasoning,
                    "model": os.environ.get("QUALITY_MODEL", "qwen2.5-coder:1.5b"),
                    "platform": platform,
                    "tokens_used": tokens_used,
                    "cost_usd": cost_usd,
                },
                reward_source="auto_quality",
            )
            logger.info("Auto-quality RL saved: id=%s score=%d platform=%s", str(exp.id)[:8], score, platform)
        finally:
            db.close()
    except Exception as e:
        logger.warning("Failed to log auto-quality RL: %s", e)
