"""RL-driven routing — learns which platform and agent perform best.

Queries accumulated RL experience data to make routing decisions.
All queries are fast DB lookups (<10ms), never model calls.
Falls back to defaults when insufficient data.
"""

import logging
import uuid
from dataclasses import dataclass, field
from typing import Optional, List, Dict, Any

from sqlalchemy import text
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

MIN_EXPERIENCES_PLATFORM = 10   # Min experiences before recommending a platform
MIN_EXPERIENCES_AGENT = 5       # Min similar experiences before recommending an agent
MIN_REWARD_THRESHOLD = 0.1      # Platform must have avg reward > this to be recommended
SIMILARITY_TOP_K = 10           # Number of similar past messages to consider


@dataclass
class RoutingRecommendation:
    """Result of RL-based routing lookup."""
    platform: Optional[str] = None          # Recommended platform or None
    agent_slug: Optional[str] = None        # Recommended agent or None
    confidence: float = 0.0                 # 0.0 (no data) to 1.0 (strong signal)
    source: str = "default"                 # "rl_platform", "rl_agent", "default"
    reasoning: str = ""                     # Why this recommendation
    alternatives: List[Dict[str, Any]] = field(default_factory=list)


def get_best_platform(
    db: Session,
    tenant_id: uuid.UUID,
    task_type: str = "general",
) -> RoutingRecommendation:
    """Query RL experiences to find the best-performing platform for this task type.

    Returns a recommendation with the platform that has the highest avg reward
    for the given task_type. Requires MIN_EXPERIENCES_PLATFORM experiences.
    """
    sql = text("""
        SELECT
            action->>'platform' AS platform,
            COUNT(*) AS total,
            AVG(reward) AS avg_reward,
            STDDEV(reward) AS stddev_reward
        FROM rl_experiences
        WHERE tenant_id = CAST(:tid AS uuid)
          AND reward IS NOT NULL
          AND archived_at IS NULL
          AND action->>'platform' IS NOT NULL
          AND state->>'task_type' = :task_type
        GROUP BY action->>'platform'
        HAVING COUNT(*) >= :min_exp
        ORDER BY AVG(reward) DESC
    """)

    try:
        rows = db.execute(sql, {
            "tid": str(tenant_id),
            "task_type": task_type,
            "min_exp": MIN_EXPERIENCES_PLATFORM,
        }).fetchall()
    except Exception as e:
        logger.debug("Platform RL query failed: %s", e)
        return RoutingRecommendation()

    if not rows:
        return RoutingRecommendation(reasoning="Insufficient RL data for platform selection")

    alternatives = []
    for r in rows:
        alternatives.append({
            "platform": r.platform,
            "avg_reward": round(float(r.avg_reward or 0), 3),
            "total": r.total,
            "stddev": round(float(r.stddev_reward or 0), 3),
        })

    best = rows[0]
    avg_reward = float(best.avg_reward or 0)

    if avg_reward < MIN_REWARD_THRESHOLD:
        return RoutingRecommendation(
            alternatives=alternatives,
            reasoning=f"Best platform {best.platform} has low avg reward {avg_reward:.3f}",
        )

    # Confidence based on data volume and reward clarity
    confidence = min(1.0, best.total / 50)  # Scales up to 50 experiences
    if len(rows) > 1:
        second = float(rows[1].avg_reward or 0)
        gap = avg_reward - second
        if gap < 0.05:
            confidence *= 0.5  # Low confidence when platforms are close

    return RoutingRecommendation(
        platform=best.platform,
        confidence=round(confidence, 2),
        source="rl_platform",
        reasoning=f"{best.platform} avg_reward={avg_reward:.3f} over {best.total} experiences (task_type={task_type})",
        alternatives=alternatives,
    )


def get_best_agent(
    db: Session,
    tenant_id: uuid.UUID,
    message: str,
    embedding: Optional[list] = None,
) -> RoutingRecommendation:
    """Find similar past messages via embedding and return the agent that scored best.

    Uses pgvector similarity search on state_embedding to find similar past
    routing decisions, then picks the agent_slug with highest avg reward.
    """
    if not embedding:
        # Generate embedding for the message
        try:
            from app.services.embedding_service import embed_text
            embedding = embed_text(message)
        except Exception as e:
            logger.debug("Embedding generation failed for agent routing: %s", e)
            return RoutingRecommendation(reasoning="Could not generate embedding")

    if not embedding:
        return RoutingRecommendation(reasoning="No embedding available")

    sql = text("""
        SELECT
            action->>'agent_slug' AS agent_slug,
            AVG(reward) AS avg_reward,
            COUNT(*) AS match_count,
            AVG(1 - (state_embedding <=> CAST(:emb AS vector))) AS avg_similarity
        FROM rl_experiences
        WHERE tenant_id = CAST(:tid AS uuid)
          AND reward IS NOT NULL
          AND state_embedding IS NOT NULL
          AND archived_at IS NULL
          AND (1 - (state_embedding <=> CAST(:emb AS vector))) > 0.5
        GROUP BY action->>'agent_slug'
        HAVING COUNT(*) >= :min_exp
        ORDER BY AVG(reward) DESC
    """)

    try:
        rows = db.execute(sql, {
            "tid": str(tenant_id),
            "emb": str(embedding),
            "min_exp": MIN_EXPERIENCES_AGENT,
        }).fetchall()
    except Exception as e:
        logger.debug("Agent RL query failed: %s", e)
        return RoutingRecommendation(reasoning=f"Agent RL query failed: {e}")

    if not rows:
        return RoutingRecommendation(reasoning="No similar past messages found")

    best = rows[0]
    avg_reward = float(best.avg_reward or 0)
    avg_sim = float(best.avg_similarity or 0)

    if avg_reward < MIN_REWARD_THRESHOLD:
        return RoutingRecommendation(
            reasoning=f"Best agent {best.agent_slug} has low reward {avg_reward:.3f}",
        )

    # Confidence based on similarity quality and data volume
    confidence = min(1.0, avg_sim * (best.match_count / 20))

    return RoutingRecommendation(
        agent_slug=best.agent_slug,
        confidence=round(confidence, 2),
        source="rl_agent",
        reasoning=f"{best.agent_slug} avg_reward={avg_reward:.3f} avg_similarity={avg_sim:.3f} over {best.match_count} similar messages",
        alternatives=[{
            "agent_slug": r.agent_slug,
            "avg_reward": round(float(r.avg_reward or 0), 3),
            "match_count": r.match_count,
        } for r in rows],
    )


@dataclass
class CombinedRoutingRecommendation:
    """Separate platform and agent recommendations — no confidence leakage."""
    platform: Optional[str] = None
    platform_confidence: float = 0.0
    platform_reasoning: str = ""
    agent_slug: Optional[str] = None
    agent_confidence: float = 0.0
    agent_reasoning: str = ""


def get_routing_recommendation(
    db: Session,
    tenant_id: uuid.UUID,
    message: str,
    task_type: str = "general",
    current_platform: str = "claude_code",
    current_agent: str = "luna",
) -> CombinedRoutingRecommendation:
    """Return separate platform and agent recommendations.

    Each has its own confidence — caller applies thresholds independently.
    Agent lookup skipped to avoid embedding call on hot path (see P2 note).
    """
    result = CombinedRoutingRecommendation()

    # Platform: fast DB query, no model call
    platform_rec = get_best_platform(db, tenant_id, task_type)
    if platform_rec.platform:
        result.platform = platform_rec.platform
        result.platform_confidence = platform_rec.confidence
        result.platform_reasoning = platform_rec.reasoning

    # Agent: SKIP by default — requires embedding which adds latency.
    # Only enabled when explicitly requested or when we have a pre-computed embedding.
    # Callers can use get_best_agent() directly when they have an embedding.

    return result
