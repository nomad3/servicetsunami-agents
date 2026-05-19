"""Benchmark aggregator — Phase 3 of the skill-creator framework port.

Equivalent of Claude Code's ``aggregate_benchmark.py``. Reads every
``skill_eval_runs`` row for a given ``(skill_id, iteration)`` and its
1:1 ``skill_eval_grading`` payload, partitions them by the ``with_skill``
boolean, and produces a ``benchmark.json``-shaped dict matching
``docs/skill-creator/schemas.md`` (the "benchmark.json" section).

The output layout::

    {
        "version": 1,
        "skill_slug": str,
        "iteration": int,
        "eval_count": int,
        "generated_at": "<RFC 3339 UTC>",
        "run_summary": {
            "with_skill":    Stats,
            "without_skill": Stats,
            "delta":         StatsDelta,   # stddev fields are null
        },
        "per_eval": [
            {
                "eval_id":       str,
                "with_skill":    LegSummary | None,
                "without_skill": LegSummary | None,
            },
            ...
        ],
    }

A ``Stats`` object is ``{pass_rate, timing_ms, tokens}`` each
``{mean, stddev}``. The delta object emits ``stddev: null`` per schema
rule — stddev of a paired difference isn't a meaningful single-sample
statistic, and the schemas.md doc is explicit that producers MUST emit
``null`` not ``0``.

Math notes
----------

* ``pass_rate`` per run is **1.0 iff the grading score is 1.0, else 0.0**.
  This matches Claude Code's reference shape (and the schemas.md text:
  "treats each eval as one Bernoulli sample"). We do NOT use the raw
  fractional score here — that's what ``per_eval[].with_skill.passed``
  carries for the eval-viewer's drill-down.

* ``stddev`` is the SAMPLE stddev (Bessel-corrected, n-1 denominator).
  For n=1 we emit ``0.0`` since the one-sample variance is undefined
  but the eval-viewer needs a number; we never call stddev on n=0
  (those legs surface as "no data" upstream).

* ``delta`` is computed only on the means: ``with_skill.mean
  - without_skill.mean`` for each of pass_rate / timing_ms / tokens.
  Schema-defined sign convention:
    - positive ``pass_rate`` delta means the skill helped
    - positive ``timing_ms`` / ``tokens`` delta means the skill cost
      more time / tokens

Missing-data handling
---------------------

Runs without a grading row are treated as "ungraded" → ``pass_rate``
contribution of 0.0, but ``timing_ms`` / ``tokens`` are still aggregated
from the run row itself. This matches Claude Code's behavior where a
crashed grader doesn't poison the timing/token stats.

A leg with zero usable runs (e.g. every with_skill run errored before
the grader could touch it) returns ``Stats`` with all fields ``0.0``
and ``stddev: 0.0``. The analyzer flags this as a high-variance /
low-signal iteration so the UI surfaces the problem.
"""

from __future__ import annotations

import logging
import math
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy import text
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)


# ──────────────────────────────────────────────────────────────────────────
# Errors
# ──────────────────────────────────────────────────────────────────────────


class AggregateNotFound(LookupError):
    """Raised when no ``skill_eval_runs`` rows exist for the (skill, iter).

    The endpoint turns this into a 404. We keep it as a distinct exception
    (rather than returning ``None``) so the call site is unambiguous —
    an empty benchmark shape with zero evals would be a valid response
    in other workflows.
    """


# ──────────────────────────────────────────────────────────────────────────
# Stats math
# ──────────────────────────────────────────────────────────────────────────


def _mean(values: List[float]) -> float:
    if not values:
        return 0.0
    return sum(values) / len(values)


def _stddev_sample(values: List[float]) -> float:
    """Sample (Bessel-corrected) stddev. Returns 0.0 for n<=1.

    Sample (n-1) chosen over population (n) because each eval is one
    Bernoulli observation from a wider population of latent user
    prompts; n-1 is the standard estimator for that. For n=1 the
    statistic is undefined but the viewer needs a number, so 0.0 it is.
    """
    n = len(values)
    if n <= 1:
        return 0.0
    m = _mean(values)
    var = sum((v - m) ** 2 for v in values) / (n - 1)
    return math.sqrt(var)


def _stats_block(values_pass: List[float], values_time: List[float], values_tokens: List[float]) -> Dict[str, Any]:
    """Build the per-leg ``Stats`` payload for run_summary.with_skill /
    .without_skill.

    Each metric is a ``{mean, stddev}`` pair. The three lists are NOT
    required to be the same length (a run can have timing but no
    grading), so we compute each metric independently.
    """
    return {
        "pass_rate": {
            "mean": round(_mean(values_pass), 4),
            "stddev": round(_stddev_sample(values_pass), 4),
        },
        "timing_ms": {
            "mean": round(_mean(values_time), 2),
            "stddev": round(_stddev_sample(values_time), 2),
        },
        "tokens": {
            "mean": round(_mean(values_tokens), 2),
            "stddev": round(_stddev_sample(values_tokens), 2),
        },
    }


def _delta_block(with_stats: Dict[str, Any], without_stats: Dict[str, Any]) -> Dict[str, Any]:
    """Build the ``delta`` block: with_skill.mean - without_skill.mean per
    metric, with stddev=null per schemas.md.
    """
    def _d(metric: str, ndigits: int) -> Dict[str, Any]:
        return {
            "mean": round(
                with_stats[metric]["mean"] - without_stats[metric]["mean"],
                ndigits,
            ),
            "stddev": None,
        }

    return {
        "pass_rate": _d("pass_rate", 4),
        "timing_ms": _d("timing_ms", 2),
        "tokens": _d("tokens", 2),
    }


# ──────────────────────────────────────────────────────────────────────────
# DB load — the only IO path
# ──────────────────────────────────────────────────────────────────────────


def _load_runs_and_grading(
    db: Session,
    *,
    skill_id: uuid.UUID,
    iteration: int,
) -> List[Dict[str, Any]]:
    """Return one row per ``skill_eval_runs`` entry for the (skill, iter).

    Each row carries:
      ``run_id, eval_id, with_skill, timing_ms, token_total,
       grading_score (or None), grading_passed (or None)``

    The grading join is a LEFT JOIN — a run without a grading row is
    still included with NULL grading fields. The aggregator decides
    how to treat ungraded runs (Bernoulli=0, but timing/tokens still
    counted) per the missing-data handling docstring.
    """
    rows = db.execute(
        text(
            """
            SELECT r.id, r.eval_id, r.with_skill, r.timing_ms,
                   r.token_usage, r.status,
                   g.score, g.grading
              FROM skill_eval_runs r
              JOIN skill_evals e ON e.id = r.eval_id
              LEFT JOIN skill_eval_grading g ON g.run_id = r.id
             WHERE e.skill_id = :skill_id
               AND r.iteration = :iteration
             ORDER BY r.created_at ASC
            """
        ),
        {"skill_id": str(skill_id), "iteration": int(iteration)},
    ).fetchall()

    out: List[Dict[str, Any]] = []
    for r in rows:
        token_usage = r[4] or {}
        # token_usage shape is {input, output, total} per migration 136
        # comments; default to 0 if missing.
        if isinstance(token_usage, dict):
            tokens = int(token_usage.get("total") or 0)
        else:
            tokens = 0
        score = r[6]
        out.append({
            "run_id": str(r[0]),
            "eval_id": str(r[1]),
            "with_skill": bool(r[2]),
            "timing_ms": int(r[3]) if r[3] is not None else None,
            "tokens": tokens,
            "status": r[5],
            "grading_score": float(score) if score is not None else None,
            "grading_payload": r[7],
        })
    return out


# ──────────────────────────────────────────────────────────────────────────
# Public API
# ──────────────────────────────────────────────────────────────────────────


def aggregate_iteration(
    db: Session,
    *,
    skill_id: uuid.UUID,
    iteration: int,
    skill_slug: str,
) -> Dict[str, Any]:
    """Build the ``benchmark.json`` payload for one iteration.

    Args:
        db: SQLAlchemy session (request-scoped; we never commit).
        skill_id: Skill being aggregated.
        iteration: 1-indexed iteration number.
        skill_slug: Already-derived slug (caller uses
            ``skill_manager.derive_slug`` on the DB ``name`` column).
            Passed in rather than re-derived so the caller controls
            slug-source drift.

    Returns:
        Dict matching the ``benchmark.json`` schema.

    Raises:
        AggregateNotFound: no ``skill_eval_runs`` rows exist for the
            given (skill, iteration). The endpoint turns this into 404.
    """
    runs = _load_runs_and_grading(
        db, skill_id=skill_id, iteration=iteration,
    )
    if not runs:
        raise AggregateNotFound(
            f"no runs for skill={skill_id} iteration={iteration}"
        )

    # ── Partition by leg ─────────────────────────────────────────────────
    with_rows = [r for r in runs if r["with_skill"]]
    without_rows = [r for r in runs if not r["with_skill"]]

    def _bernoulli(r: Dict[str, Any]) -> float:
        s = r["grading_score"]
        # 1.0 iff the grader said every expectation passed (score==1.0);
        # 0.0 otherwise, including the "no grading row" case.
        return 1.0 if (s is not None and s >= 1.0) else 0.0

    def _collect(rows: List[Dict[str, Any]]) -> Tuple[List[float], List[float], List[float]]:
        pass_vals = [_bernoulli(r) for r in rows]
        time_vals = [float(r["timing_ms"]) for r in rows if r["timing_ms"] is not None]
        token_vals = [float(r["tokens"]) for r in rows if r["tokens"] is not None]
        return pass_vals, time_vals, token_vals

    w_pass, w_time, w_tokens = _collect(with_rows)
    b_pass, b_time, b_tokens = _collect(without_rows)

    with_stats = _stats_block(w_pass, w_time, w_tokens)
    without_stats = _stats_block(b_pass, b_time, b_tokens)
    delta_stats = _delta_block(with_stats, without_stats)

    # ── per_eval table ───────────────────────────────────────────────────
    # Build a lookup keyed by eval_id so the eval-viewer can render the
    # paired rows side-by-side. A leg without a run for some eval gets
    # ``None`` (the viewer renders "missing run" for it).
    per_eval_map: Dict[str, Dict[str, Any]] = {}
    for r in runs:
        slot = per_eval_map.setdefault(
            r["eval_id"], {"eval_id": r["eval_id"], "with_skill": None, "without_skill": None}
        )
        leg_key = "with_skill" if r["with_skill"] else "without_skill"
        if slot[leg_key] is not None:
            # Multiple runs of the same (eval, leg) — keep the most recent
            # by virtue of ORDER BY created_at ASC; latest overwrites.
            pass
        slot[leg_key] = {
            "passed": (r["grading_score"] is not None and r["grading_score"] >= 1.0),
            "timing_ms": r["timing_ms"],
            "tokens": r["tokens"],
        }
    per_eval = list(per_eval_map.values())

    return {
        "version": 1,
        "skill_slug": skill_slug,
        "iteration": int(iteration),
        "eval_count": len(per_eval_map),
        "generated_at": datetime.now(tz=timezone.utc)
            .isoformat(timespec="seconds")
            .replace("+00:00", "Z"),
        "run_summary": {
            "with_skill": with_stats,
            "without_skill": without_stats,
            "delta": delta_stats,
        },
        "per_eval": per_eval,
    }


def aggregate_for_skill(
    db: Session,
    *,
    skill_id: uuid.UUID,
    iteration: int,
) -> Dict[str, Any]:
    """Convenience wrapper that derives the skill slug from the DB.

    Looks up ``skills.name``, runs it through the canonical
    ``derive_slug`` helper, and dispatches to ``aggregate_iteration``.
    Raises ``AggregateNotFound`` when the skill doesn't exist OR when
    the (skill, iteration) coordinate has no rows; the endpoint maps
    both to 404 (same 404-not-403 pattern as PR #563 / #579).
    """
    row = db.execute(
        text("SELECT name FROM skills WHERE id = :id"),
        {"id": str(skill_id)},
    ).fetchone()
    if not row:
        raise AggregateNotFound(f"skill {skill_id} not found")

    from app.services.skill_manager import derive_slug
    skill_slug = derive_slug(row[0]) or ""

    return aggregate_iteration(
        db,
        skill_id=skill_id,
        iteration=iteration,
        skill_slug=skill_slug,
    )
