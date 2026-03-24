"""Service layer for world state assertions and snapshot projections."""

from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional
import uuid

from sqlalchemy.orm import Session

from app.models.world_state import WorldStateAssertion, WorldStateSnapshot
from app.schemas.world_state import WorldStateAssertionCreate


def _validate_entity_ref(db: Session, tenant_id: uuid.UUID, entity_id: Optional[uuid.UUID]) -> None:
    if not entity_id:
        return
    from app.models.knowledge_entity import KnowledgeEntity
    exists = db.query(KnowledgeEntity).filter(
        KnowledgeEntity.id == entity_id, KnowledgeEntity.tenant_id == tenant_id,
    ).first()
    if not exists:
        raise ValueError(f"Entity {entity_id} not found in this tenant")


def _validate_observation_ref(db: Session, tenant_id: uuid.UUID, obs_id: Optional[uuid.UUID]) -> None:
    if not obs_id:
        return
    from app.models.knowledge_observation import KnowledgeObservation
    exists = db.query(KnowledgeObservation).filter(
        KnowledgeObservation.id == obs_id, KnowledgeObservation.tenant_id == tenant_id,
    ).first()
    if not exists:
        raise ValueError(f"Observation {obs_id} not found in this tenant")


def _expire_stale_assertions(db: Session, tenant_id: uuid.UUID) -> List[str]:
    """Transition active assertions past their freshness TTL to expired.

    Returns the list of affected subject_slugs so callers can refresh their snapshots.
    """
    now = datetime.utcnow()
    stale_rows = (
        db.query(WorldStateAssertion.subject_slug)
        .filter(
            WorldStateAssertion.tenant_id == tenant_id,
            WorldStateAssertion.status == "active",
            WorldStateAssertion.valid_from + WorldStateAssertion.freshness_ttl_hours * timedelta(hours=1) < now,
        )
        .distinct()
        .all()
    )
    affected_slugs = [row.subject_slug for row in stale_rows]

    if affected_slugs:
        db.query(WorldStateAssertion).filter(
            WorldStateAssertion.tenant_id == tenant_id,
            WorldStateAssertion.status == "active",
            WorldStateAssertion.valid_from + WorldStateAssertion.freshness_ttl_hours * timedelta(hours=1) < now,
        ).update({"status": "expired", "valid_to": now}, synchronize_session="fetch")
        db.commit()

    return affected_slugs
    return expired_count


def assert_state(
    db: Session,
    tenant_id: uuid.UUID,
    assertion_in: WorldStateAssertionCreate,
) -> WorldStateAssertion:
    """Create or update an assertion. Supersedes prior active assertion for same attribute."""
    _validate_entity_ref(db, tenant_id, assertion_in.subject_entity_id)
    _validate_observation_ref(db, tenant_id, assertion_in.source_observation_id)

    # Expire stale assertions and refresh affected snapshots
    affected = _expire_stale_assertions(db, tenant_id)
    for slug in affected:
        if slug != assertion_in.subject_slug:  # current subject refreshed later
            _update_snapshot_no_expire(db, tenant_id, slug)

    # Find existing active assertion for same subject + attribute
    existing = (
        db.query(WorldStateAssertion)
        .filter(
            WorldStateAssertion.tenant_id == tenant_id,
            WorldStateAssertion.subject_slug == assertion_in.subject_slug,
            WorldStateAssertion.attribute_path == assertion_in.attribute_path,
            WorldStateAssertion.status == "active",
        )
        .first()
    )

    if existing:
        # Same value = corroboration
        if existing.value_json == assertion_in.value_json:
            existing.corroboration_count += 1
            existing.confidence = min(1.0, existing.confidence + 0.05)
            existing.updated_at = datetime.utcnow()
            db.commit()
            db.refresh(existing)
            _update_snapshot(db, tenant_id, assertion_in.subject_slug, assertion_in.subject_entity_id)
            return existing

        # Different value = supersede old assertion
        existing.status = "superseded"
        existing.valid_to = datetime.utcnow()

    assertion = WorldStateAssertion(
        tenant_id=tenant_id,
        subject_entity_id=assertion_in.subject_entity_id,
        subject_slug=assertion_in.subject_slug,
        attribute_path=assertion_in.attribute_path,
        value_json=assertion_in.value_json,
        previous_value_json=existing.value_json if existing else None,
        confidence=assertion_in.confidence,
        source_observation_id=assertion_in.source_observation_id,
        source_type=assertion_in.source_type.value,
        freshness_ttl_hours=assertion_in.freshness_ttl_hours,
        status="active",
        valid_from=datetime.utcnow(),
    )
    db.add(assertion)
    db.flush()  # Populate assertion.id before linking supersession chain
    if existing:
        existing.superseded_by_id = assertion.id
    db.commit()
    db.refresh(assertion)

    _update_snapshot(db, tenant_id, assertion_in.subject_slug, assertion_in.subject_entity_id)
    return assertion


def get_assertion(
    db: Session,
    tenant_id: uuid.UUID,
    assertion_id: uuid.UUID,
) -> Optional[WorldStateAssertion]:
    return (
        db.query(WorldStateAssertion)
        .filter(
            WorldStateAssertion.id == assertion_id,
            WorldStateAssertion.tenant_id == tenant_id,
        )
        .first()
    )


def list_assertions(
    db: Session,
    tenant_id: uuid.UUID,
    subject_slug: Optional[str] = None,
    status: Optional[str] = None,
    limit: int = 100,
) -> List[WorldStateAssertion]:
    q = db.query(WorldStateAssertion).filter(WorldStateAssertion.tenant_id == tenant_id)
    if subject_slug:
        q = q.filter(WorldStateAssertion.subject_slug == subject_slug)
    if status:
        q = q.filter(WorldStateAssertion.status == status)
    else:
        q = q.filter(WorldStateAssertion.status == "active")
    return q.order_by(WorldStateAssertion.updated_at.desc()).limit(limit).all()


def get_snapshot(
    db: Session,
    tenant_id: uuid.UUID,
    subject_slug: str,
) -> Optional[WorldStateSnapshot]:
    return (
        db.query(WorldStateSnapshot)
        .filter(
            WorldStateSnapshot.tenant_id == tenant_id,
            WorldStateSnapshot.subject_slug == subject_slug,
        )
        .first()
    )


def list_snapshots(
    db: Session,
    tenant_id: uuid.UUID,
    limit: int = 100,
) -> List[WorldStateSnapshot]:
    return (
        db.query(WorldStateSnapshot)
        .filter(WorldStateSnapshot.tenant_id == tenant_id)
        .order_by(WorldStateSnapshot.last_projected_at.desc())
        .limit(limit)
        .all()
    )


def get_unstable_assertions(
    db: Session,
    tenant_id: uuid.UUID,
    confidence_threshold: float = 0.5,
    limit: int = 20,
) -> List[WorldStateAssertion]:
    """Find active assertions with low confidence or nearing expiry."""
    now = datetime.utcnow()
    return (
        db.query(WorldStateAssertion)
        .filter(
            WorldStateAssertion.tenant_id == tenant_id,
            WorldStateAssertion.status == "active",
        )
        .filter(
            (WorldStateAssertion.confidence < confidence_threshold)
            | (
                WorldStateAssertion.valid_from
                + WorldStateAssertion.freshness_ttl_hours * timedelta(hours=1)
                < now + timedelta(hours=24)
            )
        )
        .order_by(WorldStateAssertion.confidence.asc())
        .limit(limit)
        .all()
    )


def build_world_state_context(
    db: Session,
    tenant_id: uuid.UUID,
    subject_slugs: List[str],
) -> str:
    """Build markdown context for runtime injection from snapshots."""
    parts = []
    for slug in subject_slugs[:10]:
        snapshot = get_snapshot(db, tenant_id, slug)
        if not snapshot:
            continue
        parts.append(f"### {slug}")
        state = snapshot.projected_state or {}
        for key, val in state.items():
            parts.append(f"- **{key}**: {val}")
        if snapshot.unstable_attributes:
            parts.append(f"- _Unstable_: {', '.join(snapshot.unstable_attributes)}")
        parts.append(f"- _Confidence_: avg={snapshot.avg_confidence:.2f}, min={snapshot.min_confidence:.2f}")
        parts.append("")
    return "\n".join(parts) if parts else ""


def _update_snapshot(
    db: Session,
    tenant_id: uuid.UUID,
    subject_slug: str,
    subject_entity_id: Optional[uuid.UUID] = None,
) -> WorldStateSnapshot:
    """Recompute the snapshot for a subject from its active, non-expired assertions."""
    # Expire stale assertions and refresh any other affected subjects
    affected = _expire_stale_assertions(db, tenant_id)
    for slug in affected:
        if slug != subject_slug:
            _update_snapshot_no_expire(db, tenant_id, slug)

    return _update_snapshot_no_expire(db, tenant_id, subject_slug, subject_entity_id)


def _update_snapshot_no_expire(
    db: Session,
    tenant_id: uuid.UUID,
    subject_slug: str,
    subject_entity_id: Optional[uuid.UUID] = None,
) -> WorldStateSnapshot:
    """Recompute snapshot without triggering expiry (avoids recursion)."""
    active = (
        db.query(WorldStateAssertion)
        .filter(
            WorldStateAssertion.tenant_id == tenant_id,
            WorldStateAssertion.subject_slug == subject_slug,
            WorldStateAssertion.status == "active",
        )
        .all()
    )

    now = datetime.utcnow()
    projected: Dict[str, Any] = {}
    confidences: List[float] = []
    unstable: List[str] = []

    for a in active:
        projected[a.attribute_path] = a.value_json
        confidences.append(a.confidence)
        # Check freshness
        expiry = a.valid_from + timedelta(hours=a.freshness_ttl_hours)
        if a.confidence < 0.5 or expiry < now + timedelta(hours=24):
            unstable.append(a.attribute_path)

    snapshot = (
        db.query(WorldStateSnapshot)
        .filter(
            WorldStateSnapshot.tenant_id == tenant_id,
            WorldStateSnapshot.subject_slug == subject_slug,
        )
        .first()
    )
    if not snapshot:
        snapshot = WorldStateSnapshot(
            tenant_id=tenant_id,
            subject_slug=subject_slug,
            subject_entity_id=subject_entity_id,
        )
        db.add(snapshot)

    snapshot.projected_state = projected
    snapshot.assertion_count = len(active)
    snapshot.min_confidence = min(confidences) if confidences else 1.0
    snapshot.avg_confidence = sum(confidences) / len(confidences) if confidences else 1.0
    snapshot.unstable_attributes = unstable
    snapshot.last_projected_at = now
    snapshot.updated_at = now

    db.commit()
    db.refresh(snapshot)
    return snapshot
