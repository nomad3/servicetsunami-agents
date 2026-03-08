"""
Skill Router Service — Stub.

Skill execution backend has not been wired yet. This module retains the
SkillRouter class signature so that existing imports don't break.
"""

import uuid
import logging
from typing import Dict, Any, Optional

from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)


class SkillRouter:
    """Stub skill router — no execution backend configured."""

    def __init__(self, db: Session, tenant_id: uuid.UUID):
        self.db = db
        self.tenant_id = tenant_id

    def execute_skill(
        self,
        integration_name: str,
        payload: Dict[str, Any],
        task_id: Optional[uuid.UUID] = None,
        agent_id: Optional[uuid.UUID] = None,
    ) -> Dict[str, Any]:
        return {"status": "error", "error": "Skill execution backend not available"}

    def health_check(self) -> Dict[str, Any]:
        return {"status": "not_available", "healthy": False}

    def call_gateway_method(
        self,
        method: str,
        params: Dict[str, Any] = None,
        timeout_seconds: int = 30,
    ) -> Dict[str, Any]:
        return {"status": "error", "error": "Gateway not available"}
