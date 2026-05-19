"""Pattern analyzer — Phase 3 of the skill-creator framework port.

The aggregator (``aggregate.py``) collapses every run for a (skill,
iteration) into a single mean+stddev rollup. That summary hides three
classes of pattern the skill author cares about:

1. **Non-discriminating assertions** — an Expectation that passes (or
   fails) identically in BOTH legs across every paired eval. Such an
   assertion gives the skill zero signal on whether it's helping; the
   author should either tighten it or drop it.

2. **Flaky evals / high-variance metrics** — within a single leg,
   timing_ms or tokens varied wildly across runs. The aggregator
   shows a high stddev but doesn't *call out* which evals contributed
   most. We flag any eval whose paired-leg pass/fail flipped across
   identical re-runs, and any metric whose stddev is > 50% of the
   mean (rule-of-thumb for "noisy").

3. **Time/token tradeoff** — with_skill was faster but used more
   tokens, or used fewer tokens but was slower. The skill is doing
   *something* (because the means differ) but the value proposition
   is ambiguous; the author should look at the cause.

Output is a flat list of human-readable note strings. The Den eval-
viewer (Phase 4) will render these as a "Notes" pane next to the
benchmark table. We deliberately keep this simple — heuristic notes,
not a stats package — because the user does the deeper interpretation
in the viewer with the per-run transcripts open.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy import text
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)


# ──────────────────────────────────────────────────────────────────────────
# Heuristic thresholds
#
# Tuning knobs all live here so a future calibration PR can move them
# without spelunking the analysis logic. The defaults match the rules-
# of-thumb in the module docstring.
# ──────────────────────────────────────────────────────────────────────────


# A metric stddev > FLAKY_COEF_OF_VARIATION × mean → "high variance".
# 0.5 = stddev is half the mean, the classic noisy-signal threshold.
FLAKY_COEF_OF_VARIATION = 0.5

# Minimum mean before we even bother checking coefficient of variation.
# Below this we'd be flagging "noise of noise" — anything under 100 ms or
# 100 tokens isn't worth a note.
FLAKY_MIN_MEAN_TIMING_MS = 100.0
FLAKY_MIN_MEAN_TOKENS = 100.0


# Time/token tradeoff: only call it out when each side's delta is
# meaningful. <2% relative change is noise.
TRADEOFF_RELATIVE_THRESHOLD = 0.02


# ──────────────────────────────────────────────────────────────────────────
# Note generators
# ──────────────────────────────────────────────────────────────────────────


def _check_high_variance(stats_block: Dict[str, Any], leg_label: str) -> List[str]:
    """Flag metrics whose stddev/mean exceeds the noise threshold."""
    notes: List[str] = []
    for metric, min_mean in (
        ("timing_ms", FLAKY_MIN_MEAN_TIMING_MS),
        ("tokens", FLAKY_MIN_MEAN_TOKENS),
    ):
        m = stats_block.get(metric, {})
        mean = m.get("mean", 0.0) or 0.0
        stddev = m.get("stddev", 0.0) or 0.0
        if mean < min_mean:
            continue
        cv = stddev / mean if mean else 0.0
        if cv >= FLAKY_COEF_OF_VARIATION:
            notes.append(
                f"High variance on {leg_label} {metric}: "
                f"stddev={stddev:.1f} is {cv * 100:.0f}% of mean {mean:.1f}. "
                f"Re-running this iteration may shift the verdict."
            )
    return notes


def _check_time_token_tradeoff(delta: Dict[str, Any]) -> List[str]:
    """Flag the with_skill vs baseline time/token tradeoffs.

    "Tradeoff" specifically means the two metrics moved in OPPOSITE
    directions — one got faster while the other got more expensive.
    Both-faster (skill is just better) or both-slower (skill is just
    costlier) aren't tradeoffs; they're unambiguous and need no note.
    """
    notes: List[str] = []
    timing_d = (delta.get("timing_ms") or {}).get("mean") or 0.0
    tokens_d = (delta.get("tokens") or {}).get("mean") or 0.0

    # Use relative magnitudes — a 1ms / 1-token delta isn't worth a note.
    # We compare against the with_skill side's mean for the relative cut.
    # If both deltas are small we bail.
    if abs(timing_d) < 1.0 and abs(tokens_d) < 1.0:
        return notes

    if timing_d > 0 and tokens_d < 0:
        notes.append(
            f"Tradeoff: skill saved tokens (Δ={tokens_d:+.0f}) but ran "
            f"slower (Δ={timing_d:+.0f} ms). Inspect whether the saved "
            f"tokens justify the latency."
        )
    elif timing_d < 0 and tokens_d > 0:
        notes.append(
            f"Tradeoff: skill ran faster (Δ={timing_d:+.0f} ms) but "
            f"used more tokens (Δ={tokens_d:+.0f}). Inspect whether the "
            f"speed gain justifies the extra cost."
        )
    return notes


def _check_pass_rate_signal(delta: Dict[str, Any]) -> List[str]:
    """Flag suspiciously low / negative pass_rate deltas."""
    notes: List[str] = []
    pr = (delta.get("pass_rate") or {}).get("mean")
    if pr is None:
        return notes
    if pr < 0:
        notes.append(
            f"Negative pass_rate delta ({pr:+.2f}) — the skill made the "
            f"baseline WORSE on these evals. Re-check the skill body."
        )
    elif abs(pr) < 0.05:
        notes.append(
            f"Near-zero pass_rate delta ({pr:+.2f}) — the skill is not "
            f"materially changing outcomes. Either the evals are too easy "
            f"(baseline already passes them) or the skill isn't loading."
        )
    return notes


# ──────────────────────────────────────────────────────────────────────────
# Non-discriminating expectation check
# ──────────────────────────────────────────────────────────────────────────


def _expectations_signal_from_grading(
    db: Session,
    *,
    skill_id: uuid.UUID,
    iteration: int,
) -> List[str]:
    """Find Expectations whose pass/fail is identical across both legs of
    every eval. Such an Expectation has zero discriminating signal —
    it tells the skill author nothing about whether the skill is helping.

    Implementation:
      * Pull every grading.json payload joined back to the run's
        ``with_skill`` flag.
      * For each expectation id seen anywhere in this iteration, list
        the (with_skill, passed) pairs across all runs.
      * Flag the expectation if every with_skill=True run got the same
        ``passed`` value AND every with_skill=False run got the same
        ``passed`` value AND those two values are equal.
      * Skip expectations seen in only one leg (not enough data to
        call non-discriminating).
    """
    rows = db.execute(
        text(
            """
            SELECT r.with_skill, g.grading
              FROM skill_eval_runs r
              JOIN skill_evals e ON e.id = r.eval_id
              JOIN skill_eval_grading g ON g.run_id = r.id
             WHERE e.skill_id = :skill_id
               AND r.iteration = :iteration
            """
        ),
        {"skill_id": str(skill_id), "iteration": int(iteration)},
    ).fetchall()

    if not rows:
        return []

    # expectation_id → { "with": set(pass_values), "without": set(pass_values),
    #                    "desc": str (last-seen description) }
    by_exp: Dict[str, Dict[str, Any]] = {}
    for r in rows:
        with_skill = bool(r[0])
        grading = r[1] or {}
        if not isinstance(grading, dict):
            continue
        exps = grading.get("expectations") or []
        if not isinstance(exps, list):
            continue
        for exp in exps:
            if not isinstance(exp, dict):
                continue
            eid = exp.get("id")
            if not eid:
                continue
            slot = by_exp.setdefault(str(eid), {
                "with": set(),
                "without": set(),
                "desc": "",
            })
            (slot["with"] if with_skill else slot["without"]).add(
                bool(exp.get("passed", False))
            )
            slot["desc"] = exp.get("description") or slot["desc"]

    notes: List[str] = []
    for eid, slot in by_exp.items():
        with_set = slot["with"]
        without_set = slot["without"]
        if not with_set or not without_set:
            # Not enough data to call non-discriminating.
            continue
        # Both legs gave a single, identical verdict across all runs?
        if len(with_set) == 1 and len(without_set) == 1 and with_set == without_set:
            verdict = "passed" if next(iter(with_set)) else "failed"
            desc = slot["desc"] or "(no description)"
            notes.append(
                f"Non-discriminating expectation \"{eid}\" ({desc!r}): "
                f"every run {verdict} this regardless of whether the skill "
                f"was loaded. It contributes no signal; consider tightening "
                f"or dropping it."
            )

    return notes


# ──────────────────────────────────────────────────────────────────────────
# Flaky-eval check (intra-leg pass/fail flip across re-runs of one eval)
# ──────────────────────────────────────────────────────────────────────────


def _check_flaky_evals(
    db: Session,
    *,
    skill_id: uuid.UUID,
    iteration: int,
) -> List[str]:
    """Flag evals whose pass/fail flipped across repeated runs in the
    same leg of the same iteration.

    The aggregator counts paired re-runs but doesn't surface which
    eval is the noisy one. We detect by grouping (eval_id, with_skill)
    and counting runs where the grading score is 1.0 vs <1.0 — any
    group with both ``passed`` and ``failed`` outcomes is flaky.
    """
    rows = db.execute(
        text(
            """
            SELECT r.eval_id, r.with_skill, g.score
              FROM skill_eval_runs r
              JOIN skill_evals e ON e.id = r.eval_id
              LEFT JOIN skill_eval_grading g ON g.run_id = r.id
             WHERE e.skill_id = :skill_id
               AND r.iteration = :iteration
            """
        ),
        {"skill_id": str(skill_id), "iteration": int(iteration)},
    ).fetchall()

    if not rows:
        return []

    # (eval_id, with_skill) → set of {passed, failed}
    groups: Dict[Tuple[str, bool], set] = {}
    for r in rows:
        eid = str(r[0])
        with_skill = bool(r[1])
        score = r[2]
        if score is None:
            continue
        passed = float(score) >= 1.0
        groups.setdefault((eid, with_skill), set()).add(passed)

    notes: List[str] = []
    for (eid, with_skill), outcomes in groups.items():
        if len(outcomes) > 1:
            leg = "with_skill" if with_skill else "baseline"
            notes.append(
                f"Flaky eval \"{eid}\" on {leg} leg: same prompt + leg "
                f"produced both passed and failed outcomes across re-runs. "
                f"Consider raising temperature=0 or tightening the prompt."
            )
    return notes


# ──────────────────────────────────────────────────────────────────────────
# Public API
# ──────────────────────────────────────────────────────────────────────────


def analyze(
    db: Session,
    *,
    skill_id: uuid.UUID,
    iteration: int,
    benchmark: Dict[str, Any],
) -> List[str]:
    """Produce a flat list of analyzer notes for one iteration.

    Args:
        db: SQLAlchemy session.
        skill_id: Skill being analyzed.
        iteration: 1-indexed iteration number.
        benchmark: The output of ``aggregate.aggregate_iteration``.
            Passed in (rather than re-computed) so the endpoint hits
            the DB once per request, not twice.

    Returns:
        List of strings — order is: pass-rate signal, tradeoff, per-leg
        high-variance, non-discriminating expectations, flaky evals.
        Empty list when nothing flagged — caller renders "no notes".
    """
    notes: List[str] = []

    run_summary = benchmark.get("run_summary") or {}
    with_stats = run_summary.get("with_skill") or {}
    without_stats = run_summary.get("without_skill") or {}
    delta = run_summary.get("delta") or {}

    notes.extend(_check_pass_rate_signal(delta))
    notes.extend(_check_time_token_tradeoff(delta))
    notes.extend(_check_high_variance(with_stats, "with_skill"))
    notes.extend(_check_high_variance(without_stats, "baseline"))

    try:
        notes.extend(_expectations_signal_from_grading(
            db, skill_id=skill_id, iteration=iteration,
        ))
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "analyzer: non-discriminating-expectation pass failed: %s", exc,
        )
    try:
        notes.extend(_check_flaky_evals(
            db, skill_id=skill_id, iteration=iteration,
        ))
    except Exception as exc:  # noqa: BLE001
        logger.warning("analyzer: flaky-eval pass failed: %s", exc)

    return notes
