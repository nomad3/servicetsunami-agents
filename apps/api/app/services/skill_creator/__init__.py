"""Skill-creator framework service layer.

Phase 1 shipped the data shapes (`docs/skill-creator/schemas.md`) and
the grader (`grader.py`). Phase 2 (this PR) adds the eval runner —
``eval_runner.dispatch_iteration`` + ``eval_runner.get_iteration_status``
— which kicks off paired (with_skill + baseline) eval runs and persists
their on-disk artifacts under the workspaces volume. Subsequent phases
add the aggregator, analyzer, comparator, description optimizer, and
packaging.

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
]
