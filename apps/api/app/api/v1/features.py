"""API routes for tenant features."""
import uuid as _uuid
from typing import Dict, Optional

from fastapi import APIRouter, Depends, Header, HTTPException
from sqlalchemy.orm import Session

from app.api.deps import (
    get_db,
    get_current_user,
    get_current_active_user,
    require_superuser,
)
from app.core.config import settings
from app.models.tenant_features import TenantFeatures as TenantFeaturesModel
from app.models.user import User
from app.schemas.tenant_features import TenantFeatures, TenantFeaturesUpdate
from app.services import features as service

router = APIRouter()


def _verify_internal_key(
    x_internal_key: Optional[str] = Header(None, alias="X-Internal-Key"),
) -> None:
    if not x_internal_key or x_internal_key not in (
        settings.API_INTERNAL_KEY,
        settings.MCP_API_KEY,
    ):
        raise HTTPException(status_code=401, detail="Invalid internal key")


@router.get("", response_model=TenantFeatures)
def get_features(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """Get current tenant's feature flags and limits."""
    features = service.get_or_create_features(db, current_user.tenant_id)
    return features


@router.put("", response_model=TenantFeatures)
def update_features(
    features_in: TenantFeaturesUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """Update tenant features.

    Any active tenant member can change a small allowlist of
    user-preference fields (see ``_MEMBER_WRITABLE_FIELDS`` in the
    service). Every other field — including the tenant-wide ``*_enabled``
    toggles, ``active_llm_provider``, and plan/billing limits — remains
    superuser-only and is silently dropped from a non-superuser PUT
    payload rather than rejecting the whole request. The drop is logged
    at WARNING so audit and on-call can see elevation probes.
    """
    service.get_or_create_features(db, current_user.tenant_id)
    features = service.update_features(
        db,
        current_user.tenant_id,
        features_in,
        is_superuser=bool(current_user.is_superuser),
    )
    return features


@router.get("/check/{feature_name}", response_model=Dict[str, bool])
def check_feature(
    feature_name: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """Check if a specific feature is enabled."""
    enabled = service.is_feature_enabled(db, current_user.tenant_id, feature_name)
    return {"feature": feature_name, "enabled": enabled}


@router.get("/limits", response_model=Dict[str, dict])
def get_limits(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """Get current tenant's usage limits."""
    features = service.get_or_create_features(db, current_user.tenant_id)
    return {
        "max_agents": {"limit": features.max_agents},
        "max_agent_groups": {"limit": features.max_agent_groups},
        "monthly_token_limit": {"limit": features.monthly_token_limit},
        "storage_limit_gb": {"limit": features.storage_limit_gb},
    }


@router.get("/internal/tenant-features/{tenant_id}", response_model=Dict[str, bool])
def get_features_internal(
    tenant_id: _uuid.UUID,
    db: Session = Depends(get_db),
    _auth: None = Depends(_verify_internal_key),
):
    """Worker-side feature-flag lookup. Returns only the boolean columns
    (defaults baked in when no row exists).

    Used by ``apps/code-worker/tenant_feature_flags.py`` to gate the
    Claude stream-json rollout (plan §9). Auth via X-Internal-Key only;
    no user JWT involved.
    """
    row = db.query(TenantFeaturesModel).filter(
        TenantFeaturesModel.tenant_id == tenant_id
    ).first()
    if row is None:
        # No tenant_features row yet — return defaults (all OFF for
        # gated rollout flags). Cheap and stable.
        return {}
    # Project every boolean column onto the response. Includes
    # ``cli_stream_output`` once the migration adds it.
    out: Dict[str, bool] = {}
    for col in row.__table__.columns:  # type: ignore[attr-defined]
        if col.type.python_type is bool:
            val = getattr(row, col.name, None)
            if val is not None:
                out[col.name] = bool(val)
    return out
