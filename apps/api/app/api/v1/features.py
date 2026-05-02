"""API routes for tenant features."""
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from typing import Dict

from app.api.deps import get_db, get_current_user, require_superuser
from app.models.user import User
from app.schemas.tenant_features import TenantFeatures, TenantFeaturesUpdate
from app.services import features as service

router = APIRouter()


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
    current_user: User = Depends(require_superuser),
):
    """Update current tenant's feature flags.

    Admin-only — these are tenant-global settings (default CLI platform,
    GitHub primary account, plan limits) that affect every member's
    experience. Was previously gated by ``get_current_user`` with a
    docstring claim of "admin only in production"; the gate is now real.
    """
    # Ensure features exist
    service.get_or_create_features(db, current_user.tenant_id)
    features = service.update_features(db, current_user.tenant_id, features_in)
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
