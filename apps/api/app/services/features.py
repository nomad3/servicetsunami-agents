"""Feature flags service for tenant feature management."""
import logging
import uuid
from typing import Optional

from sqlalchemy.orm import Session

from app.models.tenant_features import TenantFeatures
from app.schemas.tenant_features import TenantFeaturesCreate, TenantFeaturesUpdate

logger = logging.getLogger(__name__)


# Default-deny allowlist of fields any active tenant member may set on
# their own tenant. Everything else (the ``*_enabled`` toggles,
# ``active_llm_provider``, plan/billing limits, removed branding) stays
# superuser-only and is silently dropped from a non-superuser update.
_MEMBER_WRITABLE_FIELDS = frozenset({
    "default_cli_platform",
    "github_primary_account",
    "cpa_export_format",
    "rl_enabled",
    "rl_settings",
    "cli_orchestrator_enabled",
})


def get_features(db: Session, tenant_id: uuid.UUID) -> Optional[TenantFeatures]:
    """Get tenant features by tenant_id."""
    return db.query(TenantFeatures).filter(
        TenantFeatures.tenant_id == tenant_id
    ).first()


def create_features(
    db: Session,
    tenant_id: uuid.UUID,
    features_in: TenantFeaturesCreate
) -> TenantFeatures:
    """Create tenant features."""
    features = TenantFeatures(
        tenant_id=tenant_id,
        **features_in.model_dump(exclude_unset=True)
    )
    db.add(features)
    db.commit()
    db.refresh(features)
    return features


def update_features(
    db: Session,
    tenant_id: uuid.UUID,
    features_in: TenantFeaturesUpdate,
    *,
    is_superuser: bool = False,
) -> Optional[TenantFeatures]:
    """Update tenant features.

    With ``is_superuser=False`` (default) only the
    ``_MEMBER_WRITABLE_FIELDS`` allowlist is honored — any other field
    in ``features_in`` is silently dropped. Silent so the PUT can still
    succeed for the member's intended preference change instead of a
    422 that blocks unrelated payloads. The dropped fields are logged
    at WARNING so audit / on-call can see elevation probes.
    """
    features = get_features(db, tenant_id)
    if not features:
        return None

    update_data = features_in.model_dump(exclude_unset=True)
    if not is_superuser:
        dropped = sorted(
            k for k in update_data if k not in _MEMBER_WRITABLE_FIELDS
        )
        if dropped:
            logger.warning(
                "update_features: dropped superuser-only fields %s for tenant=%s",
                dropped,
                tenant_id,
            )
            update_data = {
                k: v for k, v in update_data.items()
                if k in _MEMBER_WRITABLE_FIELDS
            }
    for field, value in update_data.items():
        setattr(features, field, value)

    db.add(features)
    db.commit()
    db.refresh(features)
    return features


def get_or_create_features(
    db: Session,
    tenant_id: uuid.UUID
) -> TenantFeatures:
    """Get existing features or create with defaults."""
    features = get_features(db, tenant_id)
    if not features:
        features = create_features(db, tenant_id, TenantFeaturesCreate())
    return features


def is_feature_enabled(
    db: Session,
    tenant_id: uuid.UUID,
    feature_name: str
) -> bool:
    """Check if a specific feature is enabled for tenant."""
    features = get_or_create_features(db, tenant_id)
    return getattr(features, feature_name, False)


def check_limit(
    db: Session,
    tenant_id: uuid.UUID,
    limit_name: str,
    current_usage: int
) -> bool:
    """Check if current usage is within limits."""
    features = get_or_create_features(db, tenant_id)
    limit_value = getattr(features, limit_name, None)
    if limit_value is None:
        return True
    return current_usage < limit_value
