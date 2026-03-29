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
    """Return a stable string key from an action JSONB dict.

    Uses a named field when available; falls back to a hash suffix
    to avoid collisions when values are truncated.
    """
    import hashlib
    for field in ("candidate", "type", "agent_id", "agent_name", "skill_id", "tool_name", "platform"):
        val = action.get(field)
        if val:
            s = str(val)
            if len(s) <= 150:
                return s
            # Append a short hash to disambiguate truncated keys
            h = hashlib.sha1(s.encode()).hexdigest()[:8]
            return f"{s[:140]}#{h}"
    # Fallback: first key=value pair with hash
    if action:
        k = next(iter(action))
        v = str(action[k])
        raw = f"{k}={v}"
        if len(raw) <= 150:
            return raw
        h = hashlib.sha1(raw.encode()).hexdigest()[:8]
        return f"{k}={v[:130]}#{h}"
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
            rc = exp.reward_components or {}
            grouped[exp.decision_point].append({
                "id": str(exp.id),
                "decision_point": exp.decision_point,
                "action_key": _extract_action_key(action),
                "action": action,
                "state": exp.state or {},
                "reward": exp.reward,
                "reward_source": exp.reward_source,
                "scorer_confidence": rc.get("scorer_confidence"),
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

    # Confidence weights by reward_source — used when scorer_confidence
    # is not explicitly stored in reward_components.
    _SOURCE_CONFIDENCE = {
        "admin_review": 1.0,
        "explicit_rating": 1.0,
        "auto_quality_consensus": 0.7,
        "auto_quality": 0.5,
        "auto_quality_backfill": 0.1,
        "response_quality_backfill": 0.1,
    }

    for dp, exps in grouped_json.items():
        # Bucket by action_key — store (reward, confidence) tuples
        buckets: Dict[str, List[tuple]] = defaultdict(list)
        for exp in exps:
            key = exp.get("action_key") or _extract_action_key(exp.get("action", {}))
            reward = exp.get("reward")
            if reward is not None:
                # Prefer explicit scorer_confidence from reward_components;
                # fall back to reward_source lookup, then default 0.5.
                confidence = exp.get("scorer_confidence")
                if confidence is None:
                    source = exp.get("reward_source") or ""
                    confidence = _SOURCE_CONFIDENCE.get(source, 0.5)
                buckets[key].append((float(reward), float(confidence)))

        dp_patterns = []
        for action_key, entries in buckets.items():
            if len(entries) < _MIN_PATTERN_COUNT:
                continue

            rewards = [r for r, _ in entries]
            # Weighted average: weight each reward by scorer_confidence.
            # Falls back to simple average if all weights are zero.
            total_weight = sum(w for _, w in entries)
            if total_weight > 0:
                avg = sum(r * w for r, w in entries) / total_weight
            else:
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
                "avg_confidence": round(total_weight / len(entries), 4) if entries else 0.5,
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
    tenant_id: str, patterns: Dict[str, List[Dict]], dream_cycle_id: str = ""
) -> Dict[str, Any]:
    """Write AutoDreamInsight rows to DB.

    For patterns with avg_reward >= _MEMORY_WRITE_THRESHOLD, also create an
    AgentMemory entry so Luna can recall the learned rule during live chats.

    Returns {dream_cycle_id, insights_created, memories_created}.
    """
    db = SessionLocal()
    try:
        tid = uuid.UUID(tenant_id)
        dream_cycle_id = uuid.UUID(dream_cycle_id) if dream_cycle_id else uuid.uuid4()
        insights_created = 0
        memories_created = 0

        # Resolve an agent_id for AgentMemory rows (requires non-null FK)
        luna_agent = (
            db.query(Agent)
            .filter(Agent.tenant_id == tid)
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

        # Mark insights as applied for the decision points we just updated
        if dp_updated > 0:
            updated_dps = list(patterns.keys())
            db.query(AutoDreamInsight).filter(
                AutoDreamInsight.tenant_id == tid,
                AutoDreamInsight.decision_point.in_(updated_dps),
                AutoDreamInsight.applied_to_policy == False,
            ).update(
                {"applied_to_policy": True},
                synchronize_session="fetch",
            )

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


# ---------------------------------------------------------------------------
# Activity 6 — prune stale knowledge (entity health scoring + archival)
# ---------------------------------------------------------------------------

@activity.defn
async def prune_stale_knowledge(tenant_id: str) -> Dict[str, Any]:
    """Archive stale entities, observations, and memories.

    Scores each entity on recall usage, observation count, and recency.
    Archives entities with health < 0.1 and age > 30 days.
    Also merges obvious duplicate entities (same name, different case).
    """
    db = SessionLocal()
    try:
        tid = uuid.UUID(tenant_id)
        archived_entities = 0
        archived_memories = 0
        merged_duplicates = 0

        # Score entities by health
        from sqlalchemy import text
        entities = db.execute(text("""
            SELECT ke.id, ke.name, ke.entity_type, ke.recall_count,
                   ke.last_recalled_at, ke.created_at,
                   (SELECT count(*) FROM knowledge_observations ko WHERE ko.entity_id = ke.id) as obs_count
            FROM knowledge_entities ke
            WHERE ke.tenant_id = CAST(:tid AS uuid)
              AND ke.status != 'archived'
            ORDER BY ke.created_at
        """), {"tid": tenant_id}).mappings().all()

        now = datetime.utcnow()
        for e in entities:
            age_days = (now - e["created_at"]).days if e["created_at"] else 0
            recall_count = e["recall_count"] or 0
            obs_count = e["obs_count"] or 0
            last_recalled = e["last_recalled_at"]

            # Recency factor: 1.0 if recalled in last 7 days, decays to 0.1 at 90 days
            if last_recalled:
                days_since_recall = (now - last_recalled).days
                recency = max(0.1, 1.0 - (days_since_recall / 90.0))
            else:
                recency = 0.1 if age_days > 30 else 0.5

            # Health score: recall usage (40%) + has observations (30%) + recency (30%)
            recall_score = min(1.0, recall_count / 5.0)  # 5+ recalls = max
            obs_score = 1.0 if obs_count > 0 else 0.0
            health = (recall_score * 0.4) + (obs_score * 0.3) + (recency * 0.3)

            # Archive if unhealthy and old enough
            if health < 0.1 and age_days > 30:
                db.execute(text("""
                    UPDATE knowledge_entities SET status = 'archived'
                    WHERE id = CAST(:eid AS uuid)
                """), {"eid": str(e["id"])})
                archived_entities += 1

        # Archive agent memories with 0 access and age > 60 days
        result = db.execute(text("""
            UPDATE agent_memories SET expires_at = NOW()
            WHERE tenant_id = CAST(:tid AS uuid)
              AND access_count = 0
              AND created_at < NOW() - INTERVAL '60 days'
              AND expires_at IS NULL
        """), {"tid": tenant_id})
        archived_memories = result.rowcount

        # Find and merge duplicate entities (same name, different case)
        # Group by (name, entity_type) to avoid merging homonyms
        # e.g. "Apple" (person) and "Apple" (company) are NOT duplicates
        dupes = db.execute(text("""
            SELECT lower(name) as lname, entity_type, count(*) as cnt,
                   array_agg(id ORDER BY recall_count DESC NULLS LAST) as ids
            FROM knowledge_entities
            WHERE tenant_id = CAST(:tid AS uuid) AND status != 'archived'
            GROUP BY lower(name), entity_type
            HAVING count(*) > 1
            LIMIT 20
        """), {"tid": tenant_id}).mappings().all()

        for dupe in dupes:
            ids = dupe["ids"]
            if len(ids) < 2:
                continue
            keep_id = ids[0]  # Keep the one with highest recall_count
            for merge_id in ids[1:]:
                params = {"keep": str(keep_id), "merge": str(merge_id)}
                # Move observations to the kept entity
                db.execute(text("""
                    UPDATE knowledge_observations SET entity_id = CAST(:keep AS uuid)
                    WHERE entity_id = CAST(:merge AS uuid)
                """), params)
                # Move relations (both directions)
                db.execute(text("""
                    UPDATE knowledge_relations SET from_entity_id = CAST(:keep AS uuid)
                    WHERE from_entity_id = CAST(:merge AS uuid)
                """), params)
                db.execute(text("""
                    UPDATE knowledge_relations SET to_entity_id = CAST(:keep AS uuid)
                    WHERE to_entity_id = CAST(:merge AS uuid)
                """), params)
                # Move world state assertions + snapshots
                db.execute(text("""
                    UPDATE world_state_assertions SET subject_entity_id = CAST(:keep AS uuid)
                    WHERE subject_entity_id = CAST(:merge AS uuid)
                """), params)
                db.execute(text("""
                    UPDATE world_state_snapshots SET subject_entity_id = CAST(:keep AS uuid)
                    WHERE subject_entity_id = CAST(:merge AS uuid)
                """), params)
                # Archive the duplicate
                db.execute(text("""
                    UPDATE knowledge_entities SET status = 'archived'
                    WHERE id = CAST(:merge AS uuid)
                """), params)
                merged_duplicates += 1

        db.commit()
        logger.info(
            "[prune] tenant=%s archived %d entities, %d memories, merged %d duplicates",
            tenant_id[:8], archived_entities, archived_memories, merged_duplicates,
        )
        return {
            "archived_entities": archived_entities,
            "archived_memories": archived_memories,
            "merged_duplicates": merged_duplicates,
        }
    except Exception:
        db.rollback()
        logger.exception("[prune] prune_stale_knowledge failed")
        return {"archived_entities": 0, "archived_memories": 0, "merged_duplicates": 0}
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Activity 7 — learn user preferences from RL experience patterns
# ---------------------------------------------------------------------------

@activity.defn
async def learn_user_preferences(tenant_id: str) -> Dict[str, Any]:
    """Infer user preferences from RL experience patterns.

    Analyzes response quality scores to detect patterns:
    - If short responses consistently score higher -> preference for brevity
    - If tool-heavy responses score higher -> preference for action
    - If detailed responses score higher -> preference for thoroughness
    """
    db = SessionLocal()
    try:
        tid = uuid.UUID(tenant_id)
        from sqlalchemy import text
        from app.models.user_preference import UserPreference

        # Analyze recent RL experiences for response length preference
        # The scorer logs response_length (integer) into state, not response_text
        length_data = db.execute(text("""
            SELECT
                CASE
                    WHEN (state->>'response_length')::int < 200 THEN 'short'
                    WHEN (state->>'response_length')::int < 800 THEN 'medium'
                    ELSE 'detailed'
                END as response_length,
                AVG(reward) as avg_reward,
                COUNT(*) as cnt
            FROM rl_experiences
            WHERE tenant_id = CAST(:tid AS uuid)
              AND decision_point = 'response_generation'
              AND reward IS NOT NULL
              AND archived_at IS NULL
              AND state->>'response_length' IS NOT NULL
              AND created_at > NOW() - INTERVAL '14 days'
            GROUP BY 1
            HAVING COUNT(*) >= 3
            ORDER BY avg_reward DESC
        """), {"tid": tenant_id}).mappings().all()

        preferences_set = 0
        if length_data:
            best = length_data[0]
            if best["avg_reward"] > 0.5 and best["cnt"] >= 5:
                # Upsert preference
                existing = db.query(UserPreference).filter(
                    UserPreference.tenant_id == tid,
                    UserPreference.preference_type == "response_length",
                ).first()
                if existing:
                    existing.value = best["response_length"]
                    existing.confidence = min(0.95, 0.3 + (best["cnt"] / 20) * 0.65)
                    existing.evidence_count = best["cnt"]
                    existing.updated_at = datetime.utcnow()
                else:
                    db.add(UserPreference(
                        tenant_id=tid,
                        preference_type="response_length",
                        value=best["response_length"],
                        confidence=min(0.95, 0.3 + (best["cnt"] / 20) * 0.65),
                        evidence_count=best["cnt"],
                    ))
                preferences_set += 1

        db.commit()
        logger.info("[preferences] tenant=%s set %d preferences", tenant_id[:8], preferences_set)
        return {"preferences_set": preferences_set}
    except Exception:
        db.rollback()
        logger.exception("[preferences] learn_user_preferences failed")
        return {"preferences_set": 0}
    finally:
        db.close()
