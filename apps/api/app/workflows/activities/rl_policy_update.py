import uuid
from datetime import datetime, timedelta
from temporalio import activity
from sqlalchemy.orm import Session
from sqlalchemy import func, text

from app.db.session import SessionLocal
from app.models.rl_experience import RLExperience
from app.models.rl_policy_state import RLPolicyState
from app.models.tenant_features import TenantFeatures
from app.services import rl_experience_service, embedding_service


@activity.defn
async def collect_tenant_experiences(tenant_id: str) -> dict:
    """Collect experience statistics for a tenant."""
    db = SessionLocal()
    try:
        tid = uuid.UUID(tenant_id)
        stats = (
            db.query(
                RLExperience.decision_point,
                func.count(RLExperience.id).label("count"),
                func.avg(RLExperience.reward).label("avg_reward"),
            )
            .filter(
                RLExperience.tenant_id == tid,
                RLExperience.archived_at.is_(None),
                RLExperience.reward.isnot(None),
            )
            .group_by(RLExperience.decision_point)
            .all()
        )
        return {
            "tenant_id": tenant_id,
            "decision_points": {
                s.decision_point: {"count": s.count, "avg_reward": float(s.avg_reward or 0)}
                for s in stats
            },
        }
    finally:
        db.close()


@activity.defn
async def update_tenant_policy(tenant_id: str, decision_point: str) -> dict:
    """Recompute policy weights for a tenant+decision_point from all rewarded experiences."""
    db = SessionLocal()
    try:
        tid = uuid.UUID(tenant_id)
        experiences = (
            db.query(RLExperience)
            .filter(
                RLExperience.tenant_id == tid,
                RLExperience.decision_point == decision_point,
                RLExperience.reward.isnot(None),
                RLExperience.archived_at.is_(None),
            )
            .all()
        )

        if not experiences:
            return {"tenant_id": tenant_id, "decision_point": decision_point, "updated": False}

        # Compute action-level aggregate scores
        action_scores = {}
        for exp in experiences:
            action_key = str(exp.action.get("id", exp.action.get("name", "unknown")))
            if action_key not in action_scores:
                action_scores[action_key] = {"total_reward": 0, "count": 0}
            action_scores[action_key]["total_reward"] += exp.reward
            action_scores[action_key]["count"] += 1

        weights = {
            k: {"avg_reward": v["total_reward"] / v["count"], "count": v["count"]}
            for k, v in action_scores.items()
        }

        policy = (
            db.query(RLPolicyState)
            .filter(RLPolicyState.tenant_id == tid, RLPolicyState.decision_point == decision_point)
            .first()
        )

        if policy:
            old_version = int(policy.version.replace("v", "")) if policy.version.startswith("v") else 0
            policy.weights = weights
            policy.version = f"v{old_version + 1}"
            policy.experience_count = len(experiences)
            policy.last_updated_at = datetime.utcnow()
        else:
            policy = RLPolicyState(
                tenant_id=tid,
                decision_point=decision_point,
                weights=weights,
                version="v1",
                experience_count=len(experiences),
            )
            db.add(policy)

        db.commit()
        return {"tenant_id": tenant_id, "decision_point": decision_point, "updated": True, "version": policy.version}
    finally:
        db.close()


@activity.defn
async def anonymize_and_aggregate_global(decision_point: str) -> dict:
    """Aggregate anonymized experience data from opt-in tenants into global baseline."""
    db = SessionLocal()
    try:
        opt_in_tenants = (
            db.query(TenantFeatures)
            .filter(TenantFeatures.rl_enabled == True)
            .all()
        )
        opt_in_ids = [
            f.tenant_id for f in opt_in_tenants
            if f.rl_settings and f.rl_settings.get("opt_in_global_learning", True)
        ]

        if not opt_in_ids:
            return {"decision_point": decision_point, "updated": False}

        experiences = (
            db.query(RLExperience)
            .filter(
                RLExperience.tenant_id.in_(opt_in_ids),
                RLExperience.decision_point == decision_point,
                RLExperience.reward.isnot(None),
                RLExperience.archived_at.is_(None),
            )
            .all()
        )

        action_scores = {}
        for exp in experiences:
            action_key = str(exp.action.get("id", exp.action.get("name", "unknown")))
            if action_key not in action_scores:
                action_scores[action_key] = {"total_reward": 0, "count": 0}
            action_scores[action_key]["total_reward"] += exp.reward
            action_scores[action_key]["count"] += 1

        weights = {
            k: {"avg_reward": v["total_reward"] / v["count"], "count": v["count"]}
            for k, v in action_scores.items()
        }

        global_policy = (
            db.query(RLPolicyState)
            .filter(RLPolicyState.tenant_id.is_(None), RLPolicyState.decision_point == decision_point)
            .first()
        )
        if global_policy:
            old_ver = int(global_policy.version.replace("v", "")) if global_policy.version.startswith("v") else 0
            global_policy.weights = weights
            global_policy.version = f"v{old_ver + 1}"
            global_policy.experience_count = len(experiences)
            global_policy.last_updated_at = datetime.utcnow()
        else:
            global_policy = RLPolicyState(
                tenant_id=None,
                decision_point=decision_point,
                weights=weights,
                version="v1",
                experience_count=len(experiences),
            )
            db.add(global_policy)

        db.commit()
        return {"decision_point": decision_point, "updated": True, "tenants": len(opt_in_ids)}
    finally:
        db.close()


@activity.defn
async def archive_old_experiences(tenant_id: str, retention_days: int = 90) -> dict:
    """Archive experiences beyond retention window."""
    db = SessionLocal()
    try:
        count = rl_experience_service.archive_old_experiences(db, uuid.UUID(tenant_id), retention_days)
        return {"tenant_id": tenant_id, "archived": count}
    finally:
        db.close()


@activity.defn
async def experience_to_observation(tenant_id: str) -> dict:
    """Convert high-reward RL experiences into knowledge observations.

    Queries rl_experiences with |reward| > 0.5 from the last 24 hours.
    For each, creates a KnowledgeObservation with observation_type='decision_insight'
    and source_type='rl_experience', then auto-embeds the observation.

    Returns:
        Dict with tenant_id and observations_created count.
    """
    db = SessionLocal()
    try:
        tid = uuid.UUID(tenant_id)
        cutoff = datetime.utcnow() - timedelta(hours=24)

        # Find high-signal experiences from the last 24h
        experiences = (
            db.query(RLExperience)
            .filter(
                RLExperience.tenant_id == tid,
                RLExperience.created_at >= cutoff,
                RLExperience.reward.isnot(None),
                RLExperience.archived_at.is_(None),
            )
            .all()
        )

        # Filter to |reward| > 0.5
        significant = [e for e in experiences if abs(e.reward) > 0.5]

        created = 0
        for exp in significant:
            # Build observation text from experience
            decision_point = exp.decision_point or "unknown"
            action_desc = ""
            if exp.action:
                if "platform" in exp.action:
                    action_desc += f"platform={exp.action['platform']}"
                if "agent_slug" in exp.action:
                    action_desc += f" agent={exp.action['agent_slug']}"
                if "selected_agent" in exp.action:
                    action_desc += f" agent={exp.action['selected_agent']}"

            state_desc = ""
            if exp.state:
                if "task_type" in exp.state:
                    state_desc += f"task_type={exp.state['task_type']}"
                if "channel" in exp.state:
                    state_desc += f" channel={exp.state['channel']}"

            reward_label = "positive" if exp.reward > 0 else "negative"
            obs_text = (
                f"RL {reward_label} signal (reward={exp.reward:.2f}) "
                f"at decision_point={decision_point}: "
                f"{action_desc}. Context: {state_desc}. "
                f"Source: {exp.reward_source or 'unknown'}"
            ).strip()

            obs_id = uuid.uuid4()
            db.execute(
                text("""
                    INSERT INTO knowledge_observations
                    (id, tenant_id, observation_text, observation_type, source_type, source_platform)
                    VALUES (:id, :tid, :text, 'decision_insight', 'rl_experience', :platform)
                """),
                {
                    "id": str(obs_id),
                    "tid": str(tid),
                    "text": obs_text,
                    "platform": exp.action.get("platform", "unknown") if exp.action else "unknown",
                },
            )

            # Auto-embed the observation
            try:
                embedding_service.embed_and_store(
                    db, tid, "observation", str(obs_id), obs_text,
                )
            except Exception:
                pass  # Don't block on embedding failure

            created += 1

        db.commit()
        return {"tenant_id": tenant_id, "observations_created": created}
    finally:
        db.close()
