"""One-shot backfill of agent affect_baseline from user-turn history.

Per Simon's "backfill all of them" 2026-05-20, follow-up after the
metacog history backfill. Phase 1.5 of the emotion engine added
``appraise_user_signal`` (Luna-approved 2026-05-20), so we can now
replay historical user turns through the classifier and seed each
agent's affect baseline with their conversational reality.

What this does
--------------
For every (tenant_id, agent_id) pair with at least one user-role
``chat_message`` in history:

  1. Pull the user messages (LIMIT cap per pair to keep the batch
     bounded — historical median is well under the cap).
  2. Run each through ``classify_user_signal`` (Ollama or heuristic).
  3. Average the resulting PAD vectors → an aggregate baseline.
  4. Write a single agent_memory row with ``affect_baseline`` set to
     that aggregate (memory_type='affect_baseline_backfill').

The aggregate is intentionally simple — Phase 1.5 is about giving
the emotion engine a starting point that reflects actual history
instead of neutral-zero. Phase 2 may switch to a recency-weighted
or RL-calibrated baseline.

Idempotency
-----------
Each (tenant_id, agent_id) gets at most one backfill row. Pre-flight
check looks for an existing agent_memory with
memory_type='affect_baseline_backfill' for the pair. Re-runs are
safe — the script skips pairs already done unless --force is set.

Usage
-----
    docker compose exec -w /app api python -m scripts.backfill_user_signal_affect \\
        --tenant-id <uuid>        # optional; omit for ALL tenants
        --backend heuristic        # 'ollama' (default) or 'heuristic'
        --max-msgs-per-agent 200   # safety cap, defaults to 500
        --dry-run                  # log what would happen, don't write
        --force                    # overwrite existing backfill row

When ``--backend ollama`` you'll pay ~1s per user message via the
local Ollama generate endpoint. ``--backend heuristic`` is
zero-latency and consistent for repeated runs.
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import sys
import uuid
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.db.session import SessionLocal
from app.models.agent_memory import AgentMemory
from app.services.user_signal_classifier import (
    PADClassifierResult,
    classify_heuristic,
    classify_ollama,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger("backfill_user_signal_affect")

BACKFILL_MEMORY_TYPE = "affect_baseline_backfill"


def _iter_pair_messages(
    db: Session,
    tenant_id: Optional[uuid.UUID],
    max_msgs_per_agent: int,
) -> Dict[Tuple[uuid.UUID, uuid.UUID], List[str]]:
    """Yield {(tenant_id, agent_id): [user_message_text, ...]} dict.

    Joins chat_messages to chat_sessions for tenant + agent resolution
    and caps the per-pair count so a chatty agent doesn't dominate
    classifier latency. We sample the MOST RECENT messages because
    they best reflect the agent's current conversational reality.
    """
    base = """
        SELECT
            cs.tenant_id AS tenant_id,
            COALESCE(cm.agent_id, cs.agent_id) AS agent_id,
            cm.content AS content,
            cm.created_at AS created_at
        FROM chat_messages cm
        JOIN chat_sessions cs ON cm.session_id = cs.id
        WHERE cm.role = 'user'
          AND cs.tenant_id IS NOT NULL
          AND COALESCE(cm.agent_id, cs.agent_id) IS NOT NULL
          AND cm.content IS NOT NULL
          AND length(trim(cm.content)) > 0
    """
    params: dict = {}
    if tenant_id is not None:
        base += " AND cs.tenant_id = :tid"
        params["tid"] = str(tenant_id)
    base += " ORDER BY cm.created_at DESC"

    grouped: Dict[Tuple[uuid.UUID, uuid.UUID], List[str]] = {}
    for row in db.execute(text(base), params):
        key = (row.tenant_id, row.agent_id)
        bucket = grouped.setdefault(key, [])
        if len(bucket) < max_msgs_per_agent:
            bucket.append(row.content)
    return grouped


def _already_backfilled(
    db: Session,
    tenant_id: uuid.UUID,
    agent_id: uuid.UUID,
) -> bool:
    row = (
        db.query(AgentMemory.id)
        .filter(
            AgentMemory.tenant_id == str(tenant_id),
            AgentMemory.agent_id == str(agent_id),
            AgentMemory.memory_type == BACKFILL_MEMORY_TYPE,
        )
        .first()
    )
    return row is not None


def _average(results: List[PADClassifierResult]) -> Tuple[float, float, float]:
    n = max(1, len(results))
    p = sum(r.pleasure for r in results) / n
    a = sum(r.arousal for r in results) / n
    d = sum(r.dominance for r in results) / n
    return p, a, d


async def _classify_one(text_: str, *, backend: str) -> PADClassifierResult:
    if backend == "heuristic":
        return classify_heuristic(text_)
    return await classify_ollama(text_)


async def _run(
    *,
    tenant_id: Optional[uuid.UUID],
    backend: str,
    max_msgs_per_agent: int,
    dry_run: bool,
    force: bool,
) -> dict:
    # Sync SQLAlchemy Session held across async awaits. This is SAFE
    # here ONLY because no DB I/O happens during an await: the awaits
    # are HTTP calls to Ollama, and the Session is touched only
    # synchronously between them. Future refactors that introduce DB
    # I/O inside an await MUST switch to per-pair sessions or an
    # async session — otherwise the Postgres conn pool will deadlock
    # under load. (Superpowers review I2 — design constraint locked
    # here so the next maintainer knows what they're touching.)
    db: Session = SessionLocal()
    stats = {
        "pairs_seen": 0,
        "pairs_skipped_existing": 0,
        "pairs_wrote": 0,
        "messages_classified": 0,
        "errors": 0,
    }
    try:
        grouped = _iter_pair_messages(db, tenant_id, max_msgs_per_agent)
        # Close the read transaction explicitly. Without this, the txn
        # opened by the bulk SELECT stays open across the entire
        # ~15-minute Ollama loop and Postgres may kill the conn for
        # idle_in_transaction_session_timeout. Superpowers review I2.
        db.commit()
        log.info(
            "found %s (tenant, agent) pairs; backend=%s cap=%s/pair",
            len(grouped), backend, max_msgs_per_agent,
        )

        for (tid, aid), messages in grouped.items():
            stats["pairs_seen"] += 1
            if not force and _already_backfilled(db, tid, aid):
                stats["pairs_skipped_existing"] += 1
                continue

            log.info(
                "classifying %s messages tenant=%s agent=%s",
                len(messages), tid, aid,
            )

            results: List[PADClassifierResult] = []
            for msg in messages:
                try:
                    pad = await _classify_one(msg, backend=backend)
                    results.append(pad)
                    stats["messages_classified"] += 1
                except Exception as exc:  # noqa: BLE001
                    log.warning(
                        "classify failed (skipping message): %s", exc
                    )
                    stats["errors"] += 1

            if not results:
                log.warning(
                    "no classifier results for tenant=%s agent=%s; skipping write",
                    tid, aid,
                )
                continue

            p, a, d = _average(results)
            log.info(
                "aggregate baseline tenant=%s agent=%s pleasure=%.3f "
                "arousal=%.3f dominance=%.3f (n=%s)",
                tid, aid, p, a, d, len(results),
            )

            if dry_run:
                stats["pairs_wrote"] += 1
                continue

            row = AgentMemory(
                tenant_id=tid,
                agent_id=aid,
                memory_type=BACKFILL_MEMORY_TYPE,
                content=(
                    "Affect baseline seeded from "
                    f"{len(results)} historical user turns; "
                    f"backend={backend}; ts="
                    + datetime.now(timezone.utc).isoformat()
                ),
                # affect_baseline is the jsonb column the emotion
                # engine reads via get_affect_baseline.
                affect_baseline={
                    "pleasure": p,
                    "arousal": a,
                    "dominance": d,
                },
                visibility="agent_only",
                importance=0.5,
                confidence=1.0,
            )
            try:
                db.add(row)
                db.commit()
                stats["pairs_wrote"] += 1
            except Exception as exc:  # noqa: BLE001
                log.warning(
                    "write failed tenant=%s agent=%s: %s",
                    tid, aid, exc,
                )
                stats["errors"] += 1
                db.rollback()
    finally:
        db.close()
    return stats


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--tenant-id", type=uuid.UUID, default=None)
    ap.add_argument(
        "--backend", choices=("ollama", "heuristic"), default="ollama",
    )
    ap.add_argument("--max-msgs-per-agent", type=int, default=500)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--force", action="store_true")
    args = ap.parse_args()

    log.info(
        "starting affect backfill tenant=%s backend=%s cap=%s "
        "dry_run=%s force=%s",
        args.tenant_id, args.backend, args.max_msgs_per_agent,
        args.dry_run, args.force,
    )
    stats = asyncio.run(_run(
        tenant_id=args.tenant_id,
        backend=args.backend,
        max_msgs_per_agent=args.max_msgs_per_agent,
        dry_run=args.dry_run,
        force=args.force,
    ))
    log.info("done: %s", stats)
    return 0


if __name__ == "__main__":
    sys.exit(main())
