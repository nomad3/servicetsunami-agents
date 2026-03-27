"""Auto-quality scorer — rates every agent response using local Ollama model.

Uses the agent_response_quality rubric from scoring_rubrics.py for
multi-dimensional scoring (accuracy, helpfulness, tool_usage, memory_usage,
efficiency, context_awareness) plus cost efficiency tracking per platform.

Also runs a 3-agent consensus review council (Accuracy, Helpfulness, Persona
reviewers) that mirrors the code-worker review pattern — extended to ALL agents.
Requires 2/3 approval to pass. Consensus results are merged into the RL experience.

Runs asynchronously after each chat response is returned to the user.
"""

import asyncio
import logging
import os
import threading
import uuid
from datetime import timedelta
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
    rollout_experiment_id: Optional[str] = None,
    rollout_arm: Optional[str] = None,
    routing_trajectory_id: Optional[str] = None,
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
            rollout_experiment_id, rollout_arm,
            routing_trajectory_id,
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
    rollout_experiment_id: Optional[str] = None,
    rollout_arm: Optional[str] = None,
    routing_trajectory_id: Optional[str] = None,
):
    """Score the response with multi-dimensional rubric + consensus council, log as RL reward."""
    from app.services.local_inference import is_available, generate

    logger.info("Auto-quality scorer: starting for tenant %s (platform=%s)", str(tenant_id)[:8], platform)

    if not await is_available():
        logger.info("Auto-quality scorer: Ollama not available — skipping")
        return

    # ── Run single-agent rubric scoring AND 3-agent consensus in parallel ──
    from app.services.scoring_rubrics import get_rubric
    from app.services.consensus_reviewer import run_consensus_review

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

    # Run rubric scorer + consensus council in parallel
    rubric_raw, consensus = await asyncio.gather(
        generate(
            prompt=prompt,
            model=os.environ.get("QUALITY_MODEL", "qwen2.5-coder:1.5b"),
            system=rubric["system_prompt"],
            temperature=0.1,
            max_tokens=300,
        ),
        run_consensus_review(
            user_message=user_message,
            agent_response=agent_response,
            agent_slug=agent_slug,
            platform=platform,
            channel=channel,
            tools_called=tools_called,
            entities_recalled=entities_recalled,
        ),
        return_exceptions=True,
    )

    # ── Parse rubric score ──
    import json, re
    score = 50
    breakdown = {}
    cost_efficiency = {}
    reasoning = ""

    if isinstance(rubric_raw, Exception):
        logger.warning("Rubric scorer raised exception: %s — using default score", rubric_raw)
        rubric_raw = None
    if rubric_raw and isinstance(rubric_raw, str):
        try:
            clean = re.sub(r"<think>.*?</think>", "", rubric_raw, flags=re.DOTALL).strip()
            json_str = clean[clean.index('{'):clean.rindex('}') + 1]
            data = json.loads(json_str)
            score = max(0, min(100, int(data.get("score", 50))))
            breakdown = data.get("breakdown", {})
            cost_efficiency = data.get("cost_efficiency", {})
            reasoning = str(data.get("reasoning", ""))[:300]
        except (json.JSONDecodeError, ValueError):
            logger.debug("Failed to parse quality score from: %s", rubric_raw[:100])

    # Handle gather exceptions — consensus may be an Exception if return_exceptions=True
    if isinstance(consensus, Exception):
        logger.warning("Consensus review raised exception: %s — skipping penalty", consensus)
        from app.services.consensus_reviewer import ConsensusResult
        consensus = ConsensusResult(passed=True, approved_count=3, total_reviewers=3, reviews=[], report="skipped")

    # ── Blend consensus signal into reward ──
    # Consensus failure: reduce reward by up to 15 points (proportional to disapprovals)
    disapproval_ratio = 1.0 - (consensus.approved_count / consensus.total_reviewers)
    consensus_penalty = disapproval_ratio * 15  # Max 15-pt penalty for 0/3 approval
    adjusted_score = max(0, score - int(consensus_penalty))

    # Map adjusted 0-100 score to RL reward: 0→-1.0, 50→0.0, 100→+1.0
    reward = (adjusted_score - 50) / 50.0

    logger.info(
        "Auto-quality: %d/100 → adjusted %d/100 (reward=%.2f) consensus=%s (%d/%d) platform=%s",
        score, adjusted_score, reward,
        "PASSED" if consensus.passed else "FAILED",
        consensus.approved_count, consensus.total_reviewers,
        platform,
    )

    if not consensus.passed:
        logger.info("Consensus FAILED for agent=%s — issues: %s", agent_slug, "; ".join(consensus.all_issues[:3]))

    # ── Log as RL experience with rubric + consensus breakdown ──
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
                    # Rubric scoring
                    "score": score,
                    "adjusted_score": adjusted_score,
                    "breakdown": breakdown,
                    "cost_efficiency": cost_efficiency,
                    "reasoning": reasoning,
                    "model": os.environ.get("QUALITY_MODEL", "qwen2.5-coder:1.5b"),
                    "platform": platform,
                    "tokens_used": tokens_used,
                    "cost_usd": cost_usd,
                    # Consensus council
                    "consensus_passed": consensus.passed,
                    "consensus_approved": consensus.approved_count,
                    "consensus_total": consensus.total_reviewers,
                    "consensus_reviews": consensus.reviews,
                    "consensus_issues": consensus.all_issues[:6],
                    "consensus_suggestions": consensus.all_suggestions[:6],
                    "consensus_penalty": consensus_penalty,
                    "consensus_fragile": consensus.fragile,
                },
                reward_source="auto_quality_consensus",
            )
            logger.info(
                "Auto-quality RL saved: id=%s score=%d→%d consensus=%s/%s platform=%s",
                str(exp.id)[:8], score, adjusted_score,
                consensus.approved_count, consensus.total_reviewers, platform,
            )
            # ── Rollout reward: feed scored reward back into the live experiment ──
            if rollout_experiment_id:
                try:
                    from app.services import policy_rollout_service
                    policy_rollout_service.record_rollout_observation(
                        db, tenant_id,
                        experiment_id=uuid.UUID(rollout_experiment_id),
                        is_treatment=(rollout_arm == "treatment"),
                        reward=reward,
                    )
                except Exception as e:
                    logger.debug("Rollout reward update failed: %s", e)

            # ── Backfill agent_routing experience with the same reward ──
            # The routing decision that selected this platform should share credit
            # for the response quality outcome.
            if routing_trajectory_id:
                try:
                    from sqlalchemy import text as sa_text
                    db.execute(sa_text("""
                        UPDATE rl_experiences
                        SET reward = :reward,
                            reward_source = 'response_quality_backfill',
                            rewarded_at = NOW()
                        WHERE tenant_id = CAST(:tid AS uuid)
                          AND trajectory_id = CAST(:traj AS uuid)
                          AND decision_point = 'agent_routing'
                          AND reward IS NULL
                    """), {
                        "reward": reward,
                        "tid": str(tenant_id),
                        "traj": routing_trajectory_id,
                    })
                    db.commit()
                    logger.debug("Backfilled agent_routing reward=%.3f for trajectory %s", reward, routing_trajectory_id[:8])
                except Exception as e:
                    logger.debug("agent_routing reward backfill failed: %s", e)

            # ── Decision gate: trigger provider council for high-value cases ──
            _maybe_trigger_provider_council(
                tenant_id=tenant_id,
                experience_id=str(exp.id),
                user_message=user_message,
                agent_response=agent_response,
                agent_slug=agent_slug,
                platform=platform,
                channel=channel,
                tools_called=tools_called,
                entities_recalled=entities_recalled,
                adjusted_score=adjusted_score,
                consensus_fragile=consensus.fragile,
            )
        finally:
            db.close()
    except Exception as e:
        logger.warning("Failed to log auto-quality RL: %s", e)


# ---------------------------------------------------------------------------
# Provider council decision gate
# ---------------------------------------------------------------------------

_SIDE_EFFECT_TOOLS = {"send_email", "create_jira_issue", "deploy_changes", "execute_shell"}


def _maybe_trigger_provider_council(
    tenant_id,
    experience_id: str,
    user_message: str,
    agent_response: str,
    agent_slug: str,
    platform: str,
    channel: str,
    tools_called: list,
    entities_recalled: list,
    adjusted_score: int,
    consensus_fragile: bool,
):
    """Decide whether to trigger the multi-provider review council.

    Triggers on: side-effect tools, fragile consensus, low scores, or 5% sample.
    """
    import random

    trigger_reason = None

    # 1. Side-effect tools used
    if tools_called and any(t in _SIDE_EFFECT_TOOLS for t in tools_called):
        trigger_reason = "side_effect_tools"

    # 2. Fragile consensus (2/3 exactly)
    elif consensus_fragile:
        trigger_reason = "fragile_consensus"

    # 3. Low score
    elif adjusted_score < 40:
        trigger_reason = "low_score"

    # 4. Random 5% sample
    elif random.random() < float(os.environ.get("PROVIDER_COUNCIL_SAMPLE_RATE", "0.05")):
        trigger_reason = "sampled"

    if not trigger_reason:
        return

    logger.info(
        "Provider council triggered: reason=%s score=%d platform=%s agent=%s",
        trigger_reason, adjusted_score, platform, agent_slug,
    )

    # Dispatch Temporal workflow in a separate thread (this function is called
    # from a background thread that may or may not have an event loop)
    def _dispatch_in_thread():
        try:
            import asyncio
            from temporalio.client import Client as TemporalClient
            from app.core.config import settings

            async def _do_dispatch():
                client = await TemporalClient.connect(settings.TEMPORAL_ADDRESS)
                await client.start_workflow(
                    "ProviderReviewWorkflow",
                    {
                        "user_message": user_message[:500],
                        "agent_response": agent_response[:1000],
                        "agent_slug": agent_slug,
                        "platform_used": platform,
                        "tools_called": ", ".join(str(t) for t in (tools_called or [])[:8]),
                        "entities_recalled": ", ".join(str(e) for e in (entities_recalled or [])[:5]),
                        "channel": channel,
                        "tenant_id": str(tenant_id),
                        "original_experience_id": experience_id,
                    },
                    id=f"provider-review-{experience_id[:8]}-{uuid.uuid4().hex[:6]}",
                    task_queue="servicetsunami-code",
                    execution_timeout=timedelta(minutes=15),
                )
                logger.info("Provider council workflow dispatched for experience %s", experience_id[:8])

            loop = asyncio.new_event_loop()
            try:
                loop.run_until_complete(_do_dispatch())
            finally:
                loop.close()
        except Exception as e:
            logger.warning("Failed to dispatch provider council: %s", e)

    t = threading.Thread(target=_dispatch_in_thread, daemon=True)
    t.start()
