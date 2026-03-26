"""Temporal activities for the Auto-dream RL consolidation cycle.

Inspired by how REM sleep consolidates short-term memories into long-term storage,
the Auto-dream cycle runs nightly (triggered from AutonomousLearningWorkflow) and:

  1. scan_unconsolidated_experiences  — fetch rewarded RL experiences from last 24 h
  2. extract_decision_patterns        — group by (decision_point, action_key), compute reward stats
  3. generate_dream_insights          — persist AutoDreamInsight rows + optional AgentMemory entries
  4. consolidate_dream_policies       — update RLPolicyState weights from high-confidence patterns
  5. log_dream_results                — write audit summary

Queue: servicetsunami-orchestration (registered in orchestration_worker.py)
"""

import logging
import uuid
from collections import defaultdict
from datetime import datetime, timedelta
from typing import Any, Dict, List

from temporalio import activity

from app.db.session import SessionLocal
from app.models.agent import Agent
from app.models.agent_memory import AgentMemory
from app.models.auto_dream_insight import AutoDreamInsight
from app.models.rl_experience import RLExperience
from app.models.rl_policy_state import RLPolicyState

logger = logging.getLogger(__name__)

# Minimum experiences needed before a pattern is considered meaningful
_MIN_PATTERN_COUNT = 2

# Reward threshold above which a pattern is written to AgentMemory
_MEMORY_WRITE_THRESHOLD = 0.4

# Learning-rate alpha for blending dream insights into existing policy weights
_POLICY_ALPHA = 0.15


def _extract_action_key(action: dict) -> str:
    """Return a stable string key from an action JSONB dict."""
    for field in ("candidate", "type", "agent_id", "agent_name", "skill_id", "tool_name", "platform"):
        val = action.get(field)
        if val:
            return str(val)[:150]
    # Fallback: first key=value pair
    if action:
        k = next(iter(action))
        return f"{k}={str(action[k])[:100]}"
    return "unknown"


# ---------------------------------------------------------------------------
# Activity 1 — scan rewarded RL experiences from last 24 h
# ---------------------------------------------------------------------------

@activity.defn
async def scan_unconsolidated_experiences(tenant_id: str) -> Dict[str, Any]:
    """Query rewarded, non-archived RL experiences created in the last 24 hours.

    Returns a dict with 'grouped' (decision_point → list of experience dicts)
    and 'total' count.
    """
    db = SessionLocal()
    try:
        tid = uuid.UUID(tenant_id)
        since = datetime.utcnow() - timedelta(hours=24)

        experiences = (
            db.query(RLExperience)
            .filter(
                RLExperience.tenant_id == tid,
                RLExperience.created_at >= since,
                RLExperience.reward.isnot(None),
                RLExperience.archived_at.is_(None),
            )
            .limit(500)
            .all()
        )

        grouped: Dict[str, List[Dict]] = defaultdict(list)
        for exp in experiences:
            action = exp.action or {}
            grouped[exp.decision_point].append({
                "id": str(exp.id),
                "decision_point": exp.decision_point,
                "action_key": _extract_action_key(action),
                "action": action,
                "state": exp.state or {},
                "reward": exp.reward,
                "created_at": exp.created_at.isoformat() if exp.created_at else None,
            })

        logger.info(
            "[auto-dream] tenant=%s scanned %d experiences across %d decision points",
            tenant_id[:8],
            len(experiences),
            len(grouped),
        )
        return {"grouped": dict(grouped), "total": len(experiences)}

    except Exception:
        logger.exception("[auto-dream] scan_unconsolidated_experiences failed")
        return {"grouped": {}, "total": 0}
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Activity 2 — group by action_key, compute reward stats per decision_point
# ---------------------------------------------------------------------------

@activity.defn
async def extract_decision_patterns(
    tenant_id: str, grouped_json: Dict[str, List[Dict]]
) -> Dict[str, Any]:
    """For each decision_point, aggregate experiences by action_key.

    Returns a dict:
      {
        decision_point: [
          {action_key, avg_reward, count, min_reward, max_reward, insight_type},
          ...
        ]
      }
    Only patterns with >= _MIN_PATTERN_COUNT experiences are returned.
    """
    patterns: Dict[str, List[Dict]] = {}

    for dp, exps in grouped_json.items():
        # Bucket by action_key
        buckets: Dict[str, List[float]] = defaultdict(list)
        for exp in exps:
            key = exp.get("action_key") or _extract_action_key(exp.get("action", {}))
            reward = exp.get("reward")
            if reward is not None:
                buckets[key].append(float(reward))

        dp_patterns = []
        for action_key, rewards in buckets.items():
            if len(rewards) < _MIN_PATTERN_COUNT:
                continue
            avg = sum(rewards) / len(rewards)
            # Classify insight type
            if avg >= 0.5:
                insight_type = "opportunity"
            elif avg <= -0.3:
                insight_type = "anomaly"
            else:
                insight_type = "pattern"

            dp_patterns.append({
                "action_key": action_key,
                "avg_reward": round(avg, 4),
                "count": len(rewards),
                "min_reward": round(min(rewards), 4),
                "max_reward": round(max(rewards), 4),
                "insight_type": insight_type,
            })

        if dp_patterns:
            patterns[dp] = dp_patterns

    total_patterns = sum(len(v) for v in patterns.values())
    logger.info(
        "[auto-dream] tenant=%s extracted %d patterns from %d decision points",
        tenant_id[:8],
        total_patterns,
        len(patterns),
    )
    return patterns


# ---------------------------------------------------------------------------
# Activity 3 — persist AutoDreamInsight rows + high-value AgentMemory entries
# ---------------------------------------------------------------------------

@activity.defn
async def generate_dream_insights(
    tenant_id: str, patterns: Dict[str, List[Dict]]
) -> Dict[str, Any]:
    """Write AutoDreamInsight rows to DB.

    For patterns with avg_reward >= _MEMORY_WRITE_THRESHOLD, also create an
    AgentMemory entry so Luna can recall the learned rule during live chats.

    Returns {dream_cycle_id, insights_created, memories_created}.
    """
    db = SessionLocal()
    try:
        tid = uuid.UUID(tenant_id)
        dream_cycle_id = uuid.uuid4()
        insights_created = 0
        memories_created = 0

        # Resolve an agent_id for AgentMemory rows (requires non-null FK)
        luna_agent = (
            db.query(Agent)
            .filter(Agent.tenant_id == tid)
            .order_by(Agent.created_at)
            .first()
        )
        agent_id = luna_agent.id if luna_agent else None

        for dp, dp_patterns in patterns.items():
            for p in dp_patterns:
                action_key = p["action_key"]
                avg_reward = p["avg_reward"]
                count = p["count"]
                insight_type = p["insight_type"]

                # Confidence: scales with sample size, capped at 0.95
                confidence = min(0.95, 0.5 + (count / 20) * 0.45)

                context_summary = (
                    f"Decision: {dp} | Action: {action_key} | "
                    f"Avg reward: {avg_reward:.3f} over {count} experiences"
                )

                insight = AutoDreamInsight(
                    tenant_id=tid,
                    dream_cycle_id=dream_cycle_id,
                    decision_point=dp,
                    insight_type=insight_type,
                    action_key=action_key,
                    context_summary=context_summary,
                    avg_reward=avg_reward,
                    experience_count=count,
                    confidence=confidence,
                    properties={
                        "min_reward": p["min_reward"],
                        "max_reward": p["max_reward"],
                    },
                )
                db.add(insight)
                db.flush()  # get insight.id before creating memory reference
                insights_created += 1

                # Write to AgentMemory for high-value patterns (requires an agent)
                if avg_reward >= _MEMORY_WRITE_THRESHOLD and agent_id is not None:
                    direction = "works well" if avg_reward >= 0.5 else "is acceptable"
                    memory_content = (
                        f"[Auto-dream] For {dp} decisions, action '{action_key}' {direction} "
                        f"(avg reward={avg_reward:.3f}, n={count}). "
                        f"Confidence: {confidence:.2f}."
                    )
                    mem = AgentMemory(
                        agent_id=agent_id,
                        tenant_id=tid,
                        content=memory_content,
                        memory_type="rl_insight",
                        importance=min(1.0, 0.4 + abs(avg_reward) * 0.5),
                        source="auto_dream",
                        tags=["auto_dream", dp],
                    )
                    db.add(mem)
                    db.flush()
                    insight.synthetic_memory_id = mem.id
                    memories_created += 1

        db.commit()
        logger.info(
            "[auto-dream] tenant=%s cycle=%s created %d insights, %d memories",
            tenant_id[:8],
            str(dream_cycle_id)[:8],
            insights_created,
            memories_created,
        )
        return {
            "dream_cycle_id": str(dream_cycle_id),
            "insights_created": insights_created,
            "memories_created": memories_created,
        }

    except Exception:
        db.rollback()
        logger.exception("[auto-dream] generate_dream_insights failed")
        return {"dream_cycle_id": "", "insights_created": 0, "memories_created": 0}
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Activity 4 — blend dream patterns into RLPolicyState weights
# ---------------------------------------------------------------------------

@activity.defn
async def consolidate_dream_policies(
    tenant_id: str, patterns: Dict[str, List[Dict]]
) -> Dict[str, Any]:
    """Update RLPolicyState weights using reward signals from this dream cycle.

    For each decision_point, fetch or create an RLPolicyState and apply:
        new_weight = (1 - alpha) * old_weight + alpha * avg_reward
    where alpha = _POLICY_ALPHA (conservative blend to avoid overfit).

    Returns {decision_points_updated, total_weight_delta}.
    """
    db = SessionLocal()
    try:
        tid = uuid.UUID(tenant_id)
        dp_updated = 0
        total_delta = 0.0

        for dp, dp_patterns in patterns.items():
            # Fetch or create policy state for this decision point
            policy = (
                db.query(RLPolicyState)
                .filter(
                    RLPolicyState.tenant_id == tid,
                    RLPolicyState.decision_point == dp,
                )
                .first()
            )
            if policy is None:
                policy = RLPolicyState(
                    tenant_id=tid,
                    decision_point=dp,
                    weights={},
                    version="dream-init",
                    experience_count=0,
                )
                db.add(policy)
                db.flush()

            weights: dict = dict(policy.weights or {})
            delta = 0.0

            for p in dp_patterns:
                key = p["action_key"]
                old_w = weights.get(key, 0.0)
                new_w = (1 - _POLICY_ALPHA) * old_w + _POLICY_ALPHA * p["avg_reward"]
                delta += abs(new_w - old_w)
                weights[key] = round(new_w, 6)

            policy.weights = weights
            policy.experience_count = (policy.experience_count or 0) + sum(
                p["count"] for p in dp_patterns
            )
            policy.last_updated_at = datetime.utcnow()
            policy.version = f"dream-{datetime.utcnow().strftime('%Y%m%d')}"

            dp_updated += 1
            total_delta += delta

        db.commit()
        logger.info(
            "[auto-dream] tenant=%s updated policies for %d decision points, total Δw=%.4f",
            tenant_id[:8],
            dp_updated,
            total_delta,
        )
        return {
            "decision_points_updated": dp_updated,
            "total_weight_delta": round(total_delta, 4),
        }

    except Exception:
        db.rollback()
        logger.exception("[auto-dream] consolidate_dream_policies failed")
        return {"decision_points_updated": 0, "total_weight_delta": 0.0}
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Activity 5 — log dream results for audit / morning report inclusion
# ---------------------------------------------------------------------------

@activity.defn
async def log_dream_results(tenant_id: str, results_json: str) -> Dict[str, Any]:
    """Write a structured audit log entry for this dream cycle and return summary."""
    import json

    try:
        results = json.loads(results_json)
    except Exception:
        results = {}

    summary = (
        f"[auto-dream] tenant={tenant_id[:8]} | "
        f"experiences={results.get('total_experiences', 0)} | "
        f"patterns={results.get('total_patterns', 0)} | "
        f"insights={results.get('insights_created', 0)} | "
        f"memories={results.get('memories_created', 0)} | "
        f"policies_updated={results.get('decision_points_updated', 0)} | "
        f"weight_delta={results.get('total_weight_delta', 0.0):.4f}"
    )
    logger.info(summary)
    return {"summary": summary, "ok": True}
