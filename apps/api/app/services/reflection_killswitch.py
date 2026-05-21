"""Per-tenant kill-switch lookup for NightlyReflectionWorkflow (O2).

Locked decision #4 (canonical design §8): the workflow must NOT run
for a tenant unless an operator has explicitly enabled it via
``tenant_features.nightly_reflection_enabled``. Default OFF.

Same defensive pattern as ``cli_orchestrator_shadow.is_cli_stream_output``:
missing row → FALSE, query failure → FALSE. The synthesis path must
never run on accident.
"""
from __future__ import annotations

import logging
import uuid
from typing import Union

from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

log = logging.getLogger(__name__)


def is_nightly_reflection_enabled(
    db: Session,
    tenant_id: Union[str, uuid.UUID],
) -> bool:
    """Return whether the operator has opted this tenant in to
    overnight synthesis. Default FALSE for any tenant without a row.

    Defensive: any SQL failure returns FALSE so a flaky features
    lookup can never enable synthesis on accident.
    """
    try:
        from app.models.tenant_features import TenantFeatures
        row = (
            db.query(TenantFeatures)
            .filter(TenantFeatures.tenant_id == str(tenant_id))
            .first()
        )
        if row is None:
            return False
        return bool(getattr(row, "nightly_reflection_enabled", False))
    except SQLAlchemyError as exc:
        log.warning(
            "reflection_killswitch: lookup failed tenant=%s err=%s; "
            "treating as OFF",
            tenant_id, exc,
        )
        return False
    except Exception as exc:  # noqa: BLE001
        log.warning(
            "reflection_killswitch: unexpected error tenant=%s err=%s; "
            "treating as OFF",
            tenant_id, exc,
        )
        return False


__all__ = ["is_nightly_reflection_enabled"]
