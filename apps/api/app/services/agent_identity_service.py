"""Service layer for agent identity profiles with tenant isolation."""

from datetime import datetime
from typing import List, Optional
import uuid

from sqlalchemy.orm import Session

from app.models.agent_identity_profile import AgentIdentityProfile
from app.schemas.agent_identity_profile import (
    AgentIdentityProfileCreate,
    AgentIdentityProfileUpdate,
)


def get_profile(
    db: Session,
    tenant_id: uuid.UUID,
    agent_slug: str,
) -> Optional[AgentIdentityProfile]:
    return (
        db.query(AgentIdentityProfile)
        .filter(
            AgentIdentityProfile.tenant_id == tenant_id,
            AgentIdentityProfile.agent_slug == agent_slug,
        )
        .first()
    )


def get_or_create_profile(
    db: Session,
    tenant_id: uuid.UUID,
    agent_slug: str,
) -> AgentIdentityProfile:
    """Get existing profile or bootstrap a default one."""
    profile = get_profile(db, tenant_id, agent_slug)
    if profile:
        return profile
    profile = AgentIdentityProfile(
        tenant_id=tenant_id,
        agent_slug=agent_slug,
    )
    db.add(profile)
    db.commit()
    db.refresh(profile)
    return profile


def list_profiles(
    db: Session,
    tenant_id: uuid.UUID,
) -> List[AgentIdentityProfile]:
    return (
        db.query(AgentIdentityProfile)
        .filter(AgentIdentityProfile.tenant_id == tenant_id)
        .order_by(AgentIdentityProfile.agent_slug.asc())
        .all()
    )


def upsert_profile(
    db: Session,
    tenant_id: uuid.UUID,
    profile_in: AgentIdentityProfileCreate,
) -> AgentIdentityProfile:
    profile = get_profile(db, tenant_id, profile_in.agent_slug)
    if not profile:
        profile = AgentIdentityProfile(
            tenant_id=tenant_id,
            agent_slug=profile_in.agent_slug,
        )
        db.add(profile)

    data = profile_in.model_dump(exclude={"agent_slug"})
    for key, value in data.items():
        if hasattr(profile, key):
            v = value.value if hasattr(value, "value") else value
            setattr(profile, key, v)

    profile.updated_at = datetime.utcnow()
    db.commit()
    db.refresh(profile)
    return profile


def update_profile(
    db: Session,
    tenant_id: uuid.UUID,
    agent_slug: str,
    profile_in: AgentIdentityProfileUpdate,
) -> Optional[AgentIdentityProfile]:
    profile = get_profile(db, tenant_id, agent_slug)
    if not profile:
        return None

    data = profile_in.model_dump(exclude_unset=True)
    for key, value in data.items():
        if hasattr(profile, key):
            v = value.value if hasattr(value, "value") else value
            setattr(profile, key, v)

    profile.updated_at = datetime.utcnow()
    db.commit()
    db.refresh(profile)
    return profile


def delete_profile(
    db: Session,
    tenant_id: uuid.UUID,
    agent_slug: str,
) -> bool:
    profile = get_profile(db, tenant_id, agent_slug)
    if not profile:
        return False
    db.delete(profile)
    db.commit()
    return True


def build_runtime_identity_context(
    db: Session,
    tenant_id: uuid.UUID,
    agent_slug: str,
) -> Optional[str]:
    """Build a markdown context block for runtime injection into CLI prompts."""
    profile = get_profile(db, tenant_id, agent_slug)
    if not profile:
        return None

    parts = [f"## Agent Identity: {agent_slug}"]
    parts.append(f"**Role**: {profile.role}")
    if profile.mandate:
        parts.append(f"**Mandate**: {profile.mandate}")
    if profile.domain_boundaries:
        parts.append(f"**Domain boundaries**: {', '.join(profile.domain_boundaries)}")
    if profile.risk_posture:
        parts.append(f"**Risk posture**: {profile.risk_posture}")
    if profile.escalation_threshold:
        parts.append(f"**Escalation threshold**: {profile.escalation_threshold}")
    if profile.planning_style:
        parts.append(f"**Planning style**: {profile.planning_style}")
    if profile.communication_style:
        parts.append(f"**Communication style**: {profile.communication_style}")
    if profile.operating_principles:
        parts.append("**Operating principles**:")
        for p in profile.operating_principles:
            parts.append(f"- {p}")
    if profile.strengths:
        parts.append(f"**Strengths**: {', '.join(profile.strengths)}")
    if profile.weaknesses:
        parts.append(f"**Known weaknesses**: {', '.join(profile.weaknesses)}")
    if profile.preferred_strategies:
        parts.append(f"**Preferred strategies**: {', '.join(profile.preferred_strategies)}")
    if profile.avoided_strategies:
        parts.append(f"**Strategies to avoid**: {', '.join(profile.avoided_strategies)}")
    if profile.allowed_tool_classes:
        parts.append(f"**Allowed tool classes**: {', '.join(profile.allowed_tool_classes)}")
    if profile.denied_tool_classes:
        parts.append(f"**Denied tool classes**: {', '.join(profile.denied_tool_classes)}")
    if profile.success_criteria:
        parts.append("**Success criteria**:")
        for sc in profile.success_criteria:
            if isinstance(sc, dict):
                parts.append(f"- {sc.get('description', sc)}")
            else:
                parts.append(f"- {sc}")

    return "\n".join(parts)
