"""Nightly reflection I/O layer — O1 substrate.

Bridges the pure-function reflection service to the database via the
`agent_memory` substrate. Same shape and discipline as
`metacog_io.py` (M1 of #616):

  - tenant boundary enforcement on write (current_tenant_id arg
    matches the JWT-derived tenant; mismatched writes are refused)
  - anchor on agent_id (real FK to agents.id), persist content as
    JSON in agent_memory with memory_type='nightly_reflection'
  - read paths return [] on SQLAlchemy error rather than raising
  - UUID filter values cast to str so the bind processor works under
    both Postgres and the SQLite test shim (lesson from PR #617)
  - NO db.refresh(row) — AgentMemory.id has a Python-side uuid4
    default so row.id is already populated; refresh() trips on the
    SQLite test engine
"""
from __future__ import annotations

import logging
import uuid
from typing import List, Optional

from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.models.agent_memory import AgentMemory
from app.schemas.reflection import NightlyReflection
from app.services.reflection import (
    REFLECTION_MEMORY_TYPE,
    deserialize_reflection,
    serialize_reflection,
)

logger = logging.getLogger(__name__)


# ── Write path ────────────────────────────────────────────────────────


def write_reflection(
    db: Session,
    *,
    reflection: NightlyReflection,
    current_tenant_id: Optional[uuid.UUID] = None,
) -> Optional[uuid.UUID]:
    """Persist a NightlyReflection as an agent_memory row.

    `current_tenant_id` enforces the tenant boundary — same discipline
    as `metacog_io.write_prediction`. When the caller passes a
    JWT-derived tenant, a reflection whose serialized tenant_id
    doesn't match is refused. The offline-synthesis runtime (O2) will
    construct reflections with the loop-local tenant_id and may omit
    the kwarg.

    Anchors on agent_id — the synthesising agent (Luna by default).
    Best-effort: returns None on bad UUID or commit failure (no
    raise, mirroring the metacog IO contract).
    """
    try:
        tenant_id = uuid.UUID(reflection.tenant_id)
        agent_id = uuid.UUID(reflection.agent_id)
    except (ValueError, AttributeError) as exc:
        logger.warning(
            "reflection_io.write_reflection: bad tenant/agent UUID — %s",
            exc,
        )
        return None

    if current_tenant_id is not None and tenant_id != current_tenant_id:
        logger.warning(
            "reflection_io.write_reflection: tenant boundary violation — "
            "reflection.tenant_id=%s != current_tenant_id=%s; "
            "refusing write",
            tenant_id, current_tenant_id,
        )
        return None

    row = AgentMemory(
        tenant_id=tenant_id,
        agent_id=agent_id,
        memory_type=REFLECTION_MEMORY_TYPE,
        content=serialize_reflection(reflection),
        importance=reflection.confidence,
        confidence=1.0,
        # Tags carry both the day and the kind so the Postgres
        # JSON-contains pushdown in list_reflections can filter
        # without scanning every reflection in the tenant.
        tags=[
            "reflection",
            reflection.kind,
            f"day:{reflection.day}",
        ],
    )
    try:
        db.add(row)
        db.commit()
        # AgentMemory.id has Python-side uuid4 default — row.id is
        # already populated at construction. db.refresh() trips on
        # SQLite test engine (lesson from M1 #617).
        return row.id
    except SQLAlchemyError as exc:
        logger.warning(
            "reflection_io.write_reflection: commit failed, rolling back. "
            "err=%s",
            exc,
        )
        db.rollback()
        return None


# ── Read paths ────────────────────────────────────────────────────────


def list_reflections(
    db: Session,
    *,
    tenant_id: uuid.UUID,
    day: Optional[str] = None,
    kind: Optional[str] = None,
    agent_id: Optional[uuid.UUID] = None,
) -> List[NightlyReflection]:
    """Return reflections in the tenant, optionally filtered.

    Filters:
      - day:    exact match on YYYY-MM-DD; SQLite-compat is handled by
                a post-filter on the deserialized object (the tags
                JSON column's contains() doesn't work under SQLite).
      - kind:   one of REFLECTION_KINDS; same SQLite-compat story.
      - agent_id: real FK column, pushed down to SQL.

    On Postgres both day and kind push down via the tags @> operator;
    on SQLite the queries return all reflection rows and the
    post-filter loop drops mismatches. The post-filter is the safety
    net — even on Postgres, malformed tag arrays would otherwise leak
    rows that don't match.

    UUID filters cast to str so the ORM's compiled bind processor
    works under both Postgres (native uuid column) and SQLite
    (TEXT-monkey-patched in tests). Without the cast,
    `Column == uuid.UUID(...)` silently returns zero rows under
    SQLite — PR #617 lesson.

    Ordered by created_at DESC so the morning-review surface sees
    freshest first.
    """
    tenant_id_param = str(tenant_id)
    agent_id_param = str(agent_id) if agent_id is not None else None
    try:
        q = db.query(AgentMemory).filter(
            AgentMemory.tenant_id == tenant_id_param,
            AgentMemory.memory_type == REFLECTION_MEMORY_TYPE,
        )
        if agent_id_param is not None:
            q = q.filter(AgentMemory.agent_id == agent_id_param)

        try:
            dialect_name = db.bind.dialect.name  # type: ignore[union-attr]
        except AttributeError:
            dialect_name = ""
        is_postgres = dialect_name.startswith("postgres")

        if is_postgres:
            # Push tag filters down on Postgres; SQLite's JSON contains
            # silently returns false so we keep the post-filter.
            if kind is not None:
                q = q.filter(AgentMemory.tags.contains([kind]))
            if day is not None:
                q = q.filter(AgentMemory.tags.contains([f"day:{day}"]))

        rows = q.order_by(AgentMemory.created_at.desc()).all()
    except SQLAlchemyError as exc:
        logger.warning(
            "reflection_io.list_reflections: query failed tenant=%s err=%s",
            tenant_id, exc,
        )
        return []

    out: List[NightlyReflection] = []
    for row in rows:
        r = deserialize_reflection(row.content)
        if r is None:
            continue
        # Post-filter safety net (covers SQLite + any malformed tag
        # arrays that slipped past Postgres' pushdown).
        if kind is not None and r.kind != kind:
            continue
        if day is not None and r.day != day:
            continue
        out.append(r)
    return out


def get_reflection_count(
    db: Session,
    *,
    tenant_id: uuid.UUID,
    day: Optional[str] = None,
) -> int:
    """Convenience for the morning-dashboard surface: how many
    reflections did we synthesise (overall, or for a specific day)?

    Goes through `list_reflections` so the filtering semantics stay
    in one place; this is a count of N small rows once a day, not a
    hot path.
    """
    return len(list_reflections(db, tenant_id=tenant_id, day=day))


__all__ = [
    "write_reflection",
    "list_reflections",
    "get_reflection_count",
]
