"""One-shot backfill of agent affect_baseline from user-turn history.

Treated as a data migration (Simon's framing 2026-05-21), not just a
model run. Per-pair stats, model versioning, latency tracking, and
a guard against silently superseding a higher-confidence prior
baseline live in this script.

What this does
--------------
For every (tenant_id, agent_id) pair with at least one user-role
``chat_message`` in history:

  1. Optional Ollama warm-up call so the first per-pair burst of
     classifications doesn't burn through 6-7 cold-load timeouts.
  2. Pull the user messages (LIMIT cap per pair to keep the batch
     bounded — historical median is well under the cap).
  3. Run each through ``classify_user_signal`` (Ollama or heuristic).
     Track per-call latency + ollama-vs-heuristic-fallback counts so
     the operator can tell at the end how much was real Ollama signal
     vs. heuristic safety-net.
  4. Average the resulting PAD vectors → an aggregate baseline.
  5. Pre-check: if a prior agent_memory row for this pair carries a
     non-null affect_baseline AND its confidence exceeds the
     incoming row's confidence, log + skip rather than supersede.
     Operators can pass --force-overwrite-curated to override.
  6. Write a single agent_memory row with ``affect_baseline`` set to
     that aggregate, model_name + run_ts + sample_size in the row
     content (provenance).

Idempotency
-----------
Each (tenant_id, agent_id) gets at most one new backfill row per
run. Pre-flight check looks for an existing agent_memory with
memory_type='affect_baseline_backfill'. ``--force`` writes a new
row anyway — ``get_affect_baseline`` picks most-recent so the new
row wins regardless. ``--force-overwrite-curated`` is REQUIRED to
supersede a row with higher confidence than the new run.

Usage
-----
    docker compose exec -w /app api python -m scripts.backfill_user_signal_affect \\
        --tenant-id <uuid>             # optional; omit for ALL tenants
        --backend ollama               # default; or 'heuristic'
        --model gemma4                 # pinned for provenance (default)
        --ollama-timeout 60            # seconds per classifier call
        --max-msgs-per-agent 500       # safety cap
        --dry-run                      # log what would happen, don't write
        --force                        # write even if backfill row exists
        --force-overwrite-curated      # write even over higher-confidence prior
        --skip-warmup                  # skip Ollama warm-up call

When ``--backend ollama`` expect ~25-30s first-call cold-load then
1-3s per subsequent call. Plan ~15-25 minutes for a 500-message
tenant.
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import sys
import time
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

# Confidence we attach to a backfill row. Aggregated over many user
# turns; not as load-bearing as a real-time appraisal. The
# high-confidence guard compares against this value when deciding
# whether to supersede an existing baseline.
BACKFILL_CONFIDENCE = 0.6


def _iter_pair_messages(
    db: Session,
    tenant_id: Optional[uuid.UUID],
    max_msgs_per_agent: int,
) -> Dict[Tuple[uuid.UUID, uuid.UUID], List[str]]:
    """Yield {(tenant_id, agent_id): [user_message_text, ...]} dict."""
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


def _highest_prior_baseline_confidence(
    db: Session,
    tenant_id: uuid.UUID,
    agent_id: uuid.UUID,
) -> Optional[float]:
    """Return the max ``confidence`` value across all prior agent_memory
    rows with a non-null ``affect_baseline`` for this pair. None if no
    such row exists. The high-confidence guard compares against this."""
    row = (
        db.query(AgentMemory.confidence)
        .filter(
            AgentMemory.tenant_id == str(tenant_id),
            AgentMemory.agent_id == str(agent_id),
            AgentMemory.affect_baseline.isnot(None),
        )
        .order_by(AgentMemory.confidence.desc().nullslast())
        .first()
    )
    if row is None:
        return None
    return float(row[0]) if row[0] is not None else None


def _average(results: List[PADClassifierResult]) -> Tuple[float, float, float]:
    n = max(1, len(results))
    p = sum(r.pleasure for r in results) / n
    a = sum(r.arousal for r in results) / n
    d = sum(r.dominance for r in results) / n
    return p, a, d


async def _classify_one(
    text_: str,
    *,
    backend: str,
    model: Optional[str],
    timeout: float,
) -> Tuple[PADClassifierResult, float]:
    """Returns (result, latency_seconds)."""
    start = time.monotonic()
    if backend == "heuristic":
        result = classify_heuristic(text_)
    else:
        result = await classify_ollama(text_, timeout=timeout, model=model)
    return result, time.monotonic() - start


async def _ollama_warmup(*, model: Optional[str], timeout: float) -> None:
    """Hit the model with a trivial prompt so the GPU memory load is
    paid once before the real per-pair loop. The 2026-05-21 cold-load
    on gemma4 took ~27s on this M4 — without warm-up the first ~6
    requests of every tenant burned through 15s+ timeouts each.

    Best-effort: any failure here is logged and the migration
    proceeds; the in-loop calls will absorb the cold-load anyway
    (just with the worse 60s timeout)."""
    try:
        log.info("warming up Ollama model=%s (one-shot trivial call)...", model)
        t0 = time.monotonic()
        await classify_ollama("ok", timeout=timeout, model=model)
        log.info("warm-up done in %.1fs", time.monotonic() - t0)
    except Exception as exc:  # noqa: BLE001
        log.warning(
            "warm-up failed (continuing anyway): %s", exc
        )


async def _run(
    *,
    tenant_id: Optional[uuid.UUID],
    backend: str,
    model: Optional[str],
    ollama_timeout: float,
    max_msgs_per_agent: int,
    dry_run: bool,
    force: bool,
    force_overwrite_curated: bool,
    skip_warmup: bool,
) -> dict:
    # Sync SQLAlchemy Session held across async awaits. SAFE only
    # because no DB I/O happens during an await — the awaits are HTTP
    # calls to Ollama. Future refactors that introduce DB I/O inside
    # an await MUST switch to per-pair sessions.
    db: Session = SessionLocal()
    stats = {
        "pairs_seen": 0,
        "pairs_skipped_existing": 0,
        "pairs_skipped_higher_confidence": 0,
        "pairs_wrote": 0,
        "messages_classified": 0,
        "messages_classified_via_ollama": 0,
        "messages_classified_via_heuristic_fallback": 0,
        "errors": 0,
        "avg_latency_ms": 0.0,
        "model": model or "(auto)",
        "backend": backend,
    }
    latencies: List[float] = []
    try:
        grouped = _iter_pair_messages(db, tenant_id, max_msgs_per_agent)
        # Close the read transaction explicitly so Postgres
        # idle_in_transaction_session_timeout doesn't kill us mid-loop.
        db.commit()
        log.info(
            "found %s (tenant, agent) pairs; backend=%s model=%s "
            "ollama_timeout=%ss cap=%s/pair",
            len(grouped), backend, model or "(auto)",
            ollama_timeout, max_msgs_per_agent,
        )

        if backend == "ollama" and not skip_warmup and grouped:
            await _ollama_warmup(model=model, timeout=ollama_timeout)

        for (tid, aid), messages in grouped.items():
            stats["pairs_seen"] += 1

            if not force and _already_backfilled(db, tid, aid):
                stats["pairs_skipped_existing"] += 1
                continue

            # High-confidence guard.
            prior_conf = _highest_prior_baseline_confidence(db, tid, aid)
            if (
                prior_conf is not None
                and prior_conf > BACKFILL_CONFIDENCE
                and not force_overwrite_curated
            ):
                log.warning(
                    "skipping tenant=%s agent=%s: existing baseline has "
                    "confidence=%.2f > backfill confidence=%.2f. Pass "
                    "--force-overwrite-curated to supersede.",
                    tid, aid, prior_conf, BACKFILL_CONFIDENCE,
                )
                stats["pairs_skipped_higher_confidence"] += 1
                continue

            log.info(
                "classifying %s messages tenant=%s agent=%s",
                len(messages), tid, aid,
            )

            results: List[PADClassifierResult] = []
            n_ollama = 0
            n_fallback = 0
            pair_latencies: List[float] = []
            for msg in messages:
                try:
                    pad, latency = await _classify_one(
                        msg,
                        backend=backend,
                        model=model,
                        timeout=ollama_timeout,
                    )
                    results.append(pad)
                    pair_latencies.append(latency)
                    stats["messages_classified"] += 1
                    # Heuristic falls back fast — a classification under
                    # ~50ms is almost certainly the heuristic, not a
                    # real LLM round-trip. Use this as a cheap proxy.
                    # (Exact: a hook into the classifier would be
                    # cleaner; this is good enough for the migration
                    # report.)
                    if backend == "ollama" and latency < 0.05:
                        n_fallback += 1
                    elif backend == "ollama":
                        n_ollama += 1
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

            stats["messages_classified_via_ollama"] += n_ollama
            stats["messages_classified_via_heuristic_fallback"] += n_fallback
            latencies.extend(pair_latencies)

            p, a, d = _average(results)
            mean_ms = (
                sum(pair_latencies) * 1000.0 / max(1, len(pair_latencies))
            )
            log.info(
                "aggregate baseline tenant=%s agent=%s pleasure=%.3f "
                "arousal=%.3f dominance=%.3f n=%s ollama_hits=%s "
                "heuristic_fallbacks=%s mean_latency_ms=%.0f",
                tid, aid, p, a, d, len(results),
                n_ollama, n_fallback, mean_ms,
            )

            if dry_run:
                stats["pairs_wrote"] += 1
                continue

            run_ts = datetime.now(timezone.utc).isoformat()
            row = AgentMemory(
                tenant_id=tid,
                agent_id=aid,
                memory_type=BACKFILL_MEMORY_TYPE,
                content=(
                    f"Affect baseline backfill. samples={len(results)} "
                    f"backend={backend} model={model or 'auto'} "
                    f"ollama_hits={n_ollama} "
                    f"heuristic_fallbacks={n_fallback} "
                    f"mean_latency_ms={mean_ms:.0f} ts={run_ts}"
                ),
                affect_baseline={
                    "pleasure": p,
                    "arousal": a,
                    "dominance": d,
                },
                visibility="agent_only",
                importance=0.5,
                confidence=BACKFILL_CONFIDENCE,
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

    if latencies:
        stats["avg_latency_ms"] = sum(latencies) * 1000.0 / len(latencies)
    return stats


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--tenant-id", type=uuid.UUID, default=None)
    ap.add_argument(
        "--backend", choices=("ollama", "heuristic"), default="ollama",
    )
    ap.add_argument(
        "--model", default="gemma4",
        help="Ollama model tag; pinned for provenance + reproducibility. "
        "Default 'gemma4' resolves to whichever variant Ollama has loaded "
        "(typically the warm one).",
    )
    ap.add_argument("--ollama-timeout", type=float, default=60.0)
    ap.add_argument("--max-msgs-per-agent", type=int, default=500)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--force-overwrite-curated", action="store_true")
    ap.add_argument("--skip-warmup", action="store_true")
    args = ap.parse_args()

    log.info(
        "starting affect backfill tenant=%s backend=%s model=%s "
        "ollama_timeout=%ss cap=%s dry_run=%s force=%s "
        "force_overwrite_curated=%s skip_warmup=%s",
        args.tenant_id, args.backend, args.model, args.ollama_timeout,
        args.max_msgs_per_agent, args.dry_run, args.force,
        args.force_overwrite_curated, args.skip_warmup,
    )
    stats = asyncio.run(_run(
        tenant_id=args.tenant_id,
        backend=args.backend,
        model=args.model if args.backend == "ollama" else None,
        ollama_timeout=args.ollama_timeout,
        max_msgs_per_agent=args.max_msgs_per_agent,
        dry_run=args.dry_run,
        force=args.force,
        force_overwrite_curated=args.force_overwrite_curated,
        skip_warmup=args.skip_warmup,
    ))
    log.info("done: %s", stats)
    return 0


if __name__ == "__main__":
    sys.exit(main())
