"""Agent Identity service — resolve tenant-specific agent names and branding."""
import logging
import uuid
from sqlalchemy.orm import Session
from app.db.safe_ops import safe_rollback
from app.models.tenant_branding import TenantBranding

logger = logging.getLogger(__name__)

def resolve_primary_agent_slug(db: Session, tenant_id: uuid.UUID) -> str:
    """Resolve the default agent slug for this tenant from branding or defaults."""
    try:
        branding = db.query(TenantBranding).filter(TenantBranding.tenant_id == tenant_id).first()
        if branding and branding.ai_assistant_name and branding.ai_assistant_name != "AI Assistant":
            return branding.ai_assistant_name.lower().replace(" ", "-")
    except Exception:
        safe_rollback(db)
    return "luna"
