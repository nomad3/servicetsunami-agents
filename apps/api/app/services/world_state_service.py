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
        db.flush()

    return affected_slugs


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

    is_dispute = False
    dispute_reason = None

    if existing:
        # Same value = corroboration
        if existing.value_json == assertion_in.value_json:
            existing.corroboration_count += 1
            existing.confidence = min(1.0, existing.confidence + 0.05)
            existing.updated_at = datetime.utcnow()
            db.flush()
            _update_snapshot_no_expire(db, tenant_id, assertion_in.subject_slug, assertion_in.subject_entity_id)
            db.commit()
            db.refresh(existing)
            return existing

        # Different value from same source type = supersede (source updated its own claim)
        # Different value from different source type = dispute (neither side wins)
        is_dispute = existing.source_type != assertion_in.source_type.value
        if is_dispute:
            dispute_reason = (
                f"Conflicting value from {assertion_in.source_type.value} "
                f"(was {existing.source_type}): "
                f"{existing.value_json} vs {assertion_in.value_json}"
            )
            existing.status = "disputed"
            existing.dispute_reason = dispute_reason
        else:
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
        # Disputes: new assertion also disputed — neither side wins until resolved
        status="disputed" if (existing and is_dispute) else "active",
        dispute_reason=dispute_reason if (existing and is_dispute) else None,
        valid_from=datetime.utcnow(),
    )
    db.add(assertion)
    db.flush()  # Populate assertion.id before linking supersession chain
    if existing:
        existing.superseded_by_id = assertion.id
    _update_snapshot_no_expire(db, tenant_id, assertion_in.subject_slug, assertion_in.subject_entity_id)
    db.commit()
    db.refresh(assertion)
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


def _compute_decayed_confidence(assertion: WorldStateAssertion) -> float:
    """Apply time-based confidence decay relative to TTL.

    Confidence decays linearly once the assertion is past 50% of its TTL.
    At 100% of TTL it would hit 0, but expiry catches it first.
    """
    now = datetime.utcnow()
    age_hours = (now - assertion.valid_from).total_seconds() / 3600
    ttl = assertion.freshness_ttl_hours
    if age_hours < ttl * 0.5:
        return assertion.confidence
    decay_fraction = (age_hours - ttl * 0.5) / (ttl * 0.5)
    decay_fraction = min(1.0, max(0.0, decay_fraction))
    return max(0.0, assertion.confidence * (1.0 - decay_fraction * 0.5))


def list_disputed_assertions(
    db: Session,
    tenant_id: uuid.UUID,
    subject_slug: Optional[str] = None,
    limit: int = 50,
) -> List[WorldStateAssertion]:
    """Find assertions marked as disputed (conflicting claims from different sources)."""
    q = db.query(WorldStateAssertion).filter(
        WorldStateAssertion.tenant_id == tenant_id,
        WorldStateAssertion.status == "disputed",
    )
    if subject_slug:
        q = q.filter(WorldStateAssertion.subject_slug == subject_slug)
    return q.order_by(WorldStateAssertion.updated_at.desc()).limit(limit).all()


def resolve_dispute(
    db: Session,
    tenant_id: uuid.UUID,
    assertion_id: uuid.UUID,
    resolution: str = "superseded",
) -> Optional[WorldStateAssertion]:
    """Resolve a disputed assertion by marking it superseded or reactivating it.

    When reactivating: supersedes any currently active assertion for the same
    attribute so only one active claim exists per attribute.
    """
    assertion = get_assertion(db, tenant_id, assertion_id)
    if not assertion or assertion.status != "disputed":
        return None

    now = datetime.utcnow()

    if resolution == "active":
        # Supersede any other active assertion for this attribute first
        db.query(WorldStateAssertion).filter(
            WorldStateAssertion.tenant_id == tenant_id,
            WorldStateAssertion.subject_slug == assertion.subject_slug,
            WorldStateAssertion.attribute_path == assertion.attribute_path,
            WorldStateAssertion.status == "active",
            WorldStateAssertion.id != assertion_id,
        ).update({"status": "superseded", "valid_to": now}, synchronize_session="fetch")
        assertion.status = "active"
        assertion.dispute_reason = None
        assertion.valid_to = None
    else:
        assertion.status = "superseded"
        assertion.valid_to = now

    assertion.updated_at = now
    db.flush()
    _update_snapshot_no_expire(db, tenant_id, assertion.subject_slug, assertion.subject_entity_id)
    db.commit()
    db.refresh(assertion)
    return assertion


def build_world_state_context(
    db: Session,
    tenant_id: uuid.UUID,
    subject_slugs: List[str],
) -> str:
    """Build markdown context for runtime injection from snapshots.

    Includes freshness metadata and dispute warnings so agents can
    distinguish reliable state from assumptions needing verification.
    """
    parts = []
    for slug in subject_slugs[:10]:
        snapshot = get_snapshot(db, tenant_id, slug)
        if not snapshot:
            continue
        parts.append(f"### {slug}")
        state = snapshot.projected_state or {}
        for key, val in state.items():
            parts.append(f"- **{key}**: {val}")
        if snapshot.disputed_attributes:
            parts.append(f"- _DISPUTED (conflicting sources)_: {', '.join(snapshot.disputed_attributes)}")
        if snapshot.unstable_attributes:
            parts.append(f"- _Needs verification_: {', '.join(snapshot.unstable_attributes)}")
        freshness = "fresh" if snapshot.min_confidence > 0.7 else "aging" if snapshot.min_confidence > 0.4 else "stale"
        parts.append(f"- _Confidence_: avg={snapshot.avg_confidence:.2f}, min={snapshot.min_confidence:.2f} ({freshness})")
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
    """Recompute snapshot without triggering expiry (avoids recursion).

    Uses decayed confidence and tracks disputed attributes.
    """
    active = (
        db.query(WorldStateAssertion)
        .filter(
            WorldStateAssertion.tenant_id == tenant_id,
            WorldStateAssertion.subject_slug == subject_slug,
            WorldStateAssertion.status == "active",
        )
        .all()
    )

    # Also count disputed assertions for this subject
    disputed = (
        db.query(WorldStateAssertion)
        .filter(
            WorldStateAssertion.tenant_id == tenant_id,
            WorldStateAssertion.subject_slug == subject_slug,
            WorldStateAssertion.status == "disputed",
        )
        .all()
    )

    now = datetime.utcnow()
    projected: Dict[str, Any] = {}
    confidences: List[float] = []
    unstable: List[str] = []
    disputed_attrs: List[str] = []

    for a in active:
        decayed = _compute_decayed_confidence(a)
        projected[a.attribute_path] = a.value_json
        confidences.append(decayed)
        expiry = a.valid_from + timedelta(hours=a.freshness_ttl_hours)
        if decayed < 0.5 or expiry < now + timedelta(hours=24):
            unstable.append(a.attribute_path)

    for d in disputed:
        if d.attribute_path not in disputed_attrs:
            disputed_attrs.append(d.attribute_path)

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
    snapshot.disputed_attributes = disputed_attrs
    snapshot.disputed_count = len(disputed)
    snapshot.last_projected_at = now
    snapshot.updated_at = now

    db.flush()
    return snapshot
