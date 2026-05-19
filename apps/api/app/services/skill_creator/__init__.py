"""Skill-creator framework service layer.

Phase 1 shipped the data shapes (`docs/skill-creator/schemas.md`) and
the grader (`grader.py`). Phase 2 added the eval runner —
``eval_runner.dispatch_iteration`` + ``eval_runner.get_iteration_status``
— which kicks off paired (with_skill + baseline) eval runs and persists
their on-disk artifacts under the workspaces volume. Phase 3 (this PR)
adds the aggregator (``aggregate.aggregate_iteration``) and analyzer
(``analyzer.analyze``) that collapse those runs into a single
``benchmark.json`` plus a list of human-readable notes.

Subsequent phases add the eval-viewer Den tab, the comparator, the
description optimizer, and packaging.

See ``docs/plans/2026-05-18-skill-creator-framework-port.md`` for the full
delivery plan.
"""

from app.services.skill_creator.grader import (
    Expectation,
    GradedExpectation,
    GradingResult,
    grade,
)
from app.services.skill_creator.eval_runner import (
    dispatch_iteration,
    get_iteration_status,
    compute_iteration_dir,
    compute_eval_dir,
    TERMINAL_STATUSES,
)
from app.services.skill_creator.aggregate import (
    AggregateNotFound,
    aggregate_iteration,
    aggregate_for_skill,
)
from app.services.skill_creator.analyzer import analyze

__all__ = [
    "Expectation",
    "GradedExpectation",
    "GradingResult",
    "grade",
    "dispatch_iteration",
    "get_iteration_status",
    "compute_iteration_dir",
    "compute_eval_dir",
    "TERMINAL_STATUSES",
    "AggregateNotFound",
    "aggregate_iteration",
    "aggregate_for_skill",
    "analyze",
]
