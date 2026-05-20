"""One-shot backfill of metacognition substrate from chat_messages history.

Per Simon's request (2026-05-20): "backfill all of them i'm not patient".

What this does
--------------
For every assistant `chat_message` already in the database, write a paired
``ConfidencePrediction`` + ``OutcomeObservation`` so that the calibration
aggregator (M3 Prometheus ECE + ``/api/v1/metacog/calibration``) has
historical data points to chew on. Without this, ECE starts cold from the
moment M2's runtime hook (#619) shipped on 2026-05-19.

Mapping
-------
- ``decision_kind`` = ``rl_route_chat_response`` (same as M2's hook)
- ``decision_id``   = the message UUID (deterministic + idempotent)
- ``predicted_confidence`` = ``chat_messages.confidence`` if present, else
  ``0.5`` (Phase 1 baseline — what M2's hook itself currently writes)
- ``context_hash``  = ``sha256(session_id|created_at)[:16]`` — lets the
  aggregator group structurally-similar decisions without leaking content
- ``actual_reward`` = ``+1.0`` if the assistant response was non-empty,
  ``-1.0`` if empty (matching ``cli_session_manager._record_metacog_observation``)
- ``latency_ms``    = ``0`` (we don't have it for historical messages —
  the schema accepts >= 0)
- ``ts``/``completed_at`` = the message's ``created_at`` so calibration
  bucketing reflects when the decision actually happened, not the
  backfill time

Idempotency
-----------
Predictions and observations are written via the public
``metacog_io.write_prediction`` / ``write_observation`` paths so they
honor tenant boundaries. The script checks for an existing
``agent_memories`` row whose JSON content's ``decision_id`` matches the
message UUID and skips if one is found, so re-runs are safe.

Usage
-----
    docker compose exec api python -m app.scripts.backfill_metacog_history \
        --tenant-id <uuid>          # optional; omit to do ALL tenants
        --dry-run                   # log what would happen, don't write
        --limit N                   # cap rows processed (smoke test)

Why only ``rl_route_chat_response``? The other ``DECISION_KINDS``
(tool_call_outcome, affect_appraise, blackboard_contribute,
rl_route_coalition_role) don't have a clean historical surface in the
schema — we'd be inventing decisions. Chat-response is the one with a
1:1 mapping to existing rows.
"""
from __future__ import annotations

import argparse
import hashlib
import logging
import sys
import uuid
from datetime import datetime, timezone
from typing import Iterable, Optional

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.db.session import SessionLocal
from app.models.agent_memory import AgentMemory
from app.schemas.metacog import ConfidencePrediction, OutcomeObservation
from app.services import metacog_io
from app.services.metacog import (
    OBSERVATION_MEMORY_TYPE,
    PREDICTION_MEMORY_TYPE,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger("backfill_metacog")


def _iso(dt: datetime) -> str:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).isoformat()


def _context_hash(session_id: uuid.UUID, created_at: datetime) -> str:
    payload = f"{session_id}|{_iso(created_at)}".encode("utf-8")
    return hashlib.sha256(payload).hexdigest()[:16]


def _already_backfilled(db: Session, message_id: uuid.UUID) -> bool:
    """A prediction or observation row whose content carries this
    decision_id means the message was already processed (either by
    this backfill or by M2's live hook on later messages)."""
    row = db.execute(
        text(
            """
            SELECT 1
            FROM agent_memories
            WHERE memory_type IN (:pred_type, :obs_type)
              AND (content ->> 'decision_id')::uuid = :msg_id
            LIMIT 1
            """
        ),
        {
            "pred_type": PREDICTION_MEMORY_TYPE,
            "obs_type": OBSERVATION_MEMORY_TYPE,
            "msg_id": str(message_id),
        },
    ).first()
    return row is not None


def _iter_messages(
    db: Session,
    tenant_id: Optional[uuid.UUID],
    limit: Optional[int],
) -> Iterable[dict]:
    """Yield assistant messages joined to their session for tenant
    + agent resolution. We deliberately go through raw SQL: the ORM
    eager loads would burn memory on hundreds of rows, and we only
    need a flat tuple per message."""
    base = """
        SELECT
            cm.id          AS msg_id,
            cm.session_id  AS session_id,
            cm.content     AS content,
            cm.confidence  AS confidence,
            cm.created_at  AS created_at,
            COALESCE(cm.agent_id, cs.agent_id) AS agent_id,
            cs.tenant_id   AS tenant_id
        FROM chat_messages cm
        JOIN chat_sessions cs ON cm.session_id = cs.id
        WHERE cm.role = 'assistant'
          AND COALESCE(cm.agent_id, cs.agent_id) IS NOT NULL
    """
    params: dict = {}
    if tenant_id is not None:
        base += " AND cs.tenant_id = :tid"
        params["tid"] = str(tenant_id)
    base += " ORDER BY cm.created_at ASC"
    if limit is not None:
        base += " LIMIT :lim"
        params["lim"] = limit

    for row in db.execute(text(base), params):
        yield {
            "msg_id": row.msg_id,
            "session_id": row.session_id,
            "content": row.content or "",
            "confidence": row.confidence,
            "created_at": row.created_at,
            "agent_id": row.agent_id,
            "tenant_id": row.tenant_id,
        }


def backfill(
    *,
    tenant_id: Optional[uuid.UUID],
    limit: Optional[int],
    dry_run: bool,
) -> dict:
    db: Session = SessionLocal()
    stats = {"seen": 0, "skipped_existing": 0, "wrote": 0, "errors": 0}
    try:
        for row in _iter_messages(db, tenant_id, limit):
            stats["seen"] += 1
            msg_id: uuid.UUID = row["msg_id"]
            if _already_backfilled(db, msg_id):
                stats["skipped_existing"] += 1
                continue

            ts = _iso(row["created_at"])
            predicted = (
                float(row["confidence"])
                if row["confidence"] is not None
                else 0.5
            )
            # Clamp — historical rows may have stored confidences
            # outside [0, 1] before Phase 1 normalization landed.
            predicted = min(1.0, max(0.0, predicted))
            content = row["content"]
            reward = 1.0 if (content and content.strip()) else -1.0

            prediction = ConfidencePrediction(
                tenant_id=str(row["tenant_id"]),
                agent_id=str(row["agent_id"]),
                decision_id=str(msg_id),
                decision_kind="rl_route_chat_response",
                predicted_confidence=predicted,
                context_hash=_context_hash(row["session_id"], row["created_at"]),
                ts=ts,
            )
            observation = OutcomeObservation(
                tenant_id=str(row["tenant_id"]),
                agent_id=str(row["agent_id"]),
                decision_id=str(msg_id),
                actual_reward=reward,
                latency_ms=0,  # not recorded historically
                completed_at=ts,
                error=None if reward > 0 else "empty assistant response",
            )

            if dry_run:
                stats["wrote"] += 1
                continue

            try:
                pred_id = metacog_io.write_prediction(
                    db,
                    prediction=prediction,
                    current_tenant_id=row["tenant_id"],
                )
                obs_id = metacog_io.write_observation(
                    db,
                    observation=observation,
                    current_tenant_id=row["tenant_id"],
                )
                if pred_id is None or obs_id is None:
                    # IO layer logs the reason; treat as a soft error
                    # so the run keeps going rather than tripping on
                    # one bad row.
                    stats["errors"] += 1
                    continue
                stats["wrote"] += 1
            except Exception as exc:  # noqa: BLE001
                log.warning(
                    "backfill failed for message %s: %s", msg_id, exc
                )
                stats["errors"] += 1
                db.rollback()

            if stats["seen"] % 100 == 0:
                log.info("progress: %s", stats)
    finally:
        db.close()
    return stats


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--tenant-id", type=uuid.UUID, default=None)
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    log.info(
        "starting backfill tenant=%s limit=%s dry_run=%s",
        args.tenant_id, args.limit, args.dry_run,
    )
    stats = backfill(
        tenant_id=args.tenant_id,
        limit=args.limit,
        dry_run=args.dry_run,
    )
    log.info("done: %s", stats)
    return 0


if __name__ == "__main__":
    sys.exit(main())
