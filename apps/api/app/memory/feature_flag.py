from uuid import UUID
from app.core.config import settings

def is_v2_enabled(tenant_id: UUID) -> bool:
    """Return True if Memory-First V2 path is enabled for this tenant."""
    if not settings.USE_MEMORY_V2:
        return False
    if settings.USE_MEMORY_V2_TENANT_ALLOWLIST:
        return str(tenant_id) in settings.USE_MEMORY_V2_TENANT_ALLOWLIST
    return True
