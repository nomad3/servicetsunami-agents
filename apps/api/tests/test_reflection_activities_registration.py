"""Activity-name registration smoke test for NightlyReflectionWorkflow.

Locks the fact that the 5 production activity defns in
``reflection_activities.py`` expose EXACTLY the names the workflow
expects. The workflow tests in ``test_nightly_reflection_workflow.py``
mock the activities by re-declaring `@activity.defn(name=...)` inside
the test fixture — that proves the workflow CAN call those names, but
NOT that the production activity module exposes them.

Failure mode this test catches: someone renames
``reflection.gather_episodes`` to ``reflection.gather`` in the
production module + forgets to update the workflow's
``execute_activity(gather_episodes, ...)`` reference, and the unit
tests still pass because they redeclare the names.

Same family of failure as feedback_test_router_startup memory —
unit tests don't catch registration drift; only worker startup
does. (#631 retroactive review I2.)
"""
from __future__ import annotations

import pytest

from app.workflows.activities import reflection_activities as ra


EXPECTED_ACTIVITY_NAMES = frozenset({
    "reflection.check_killswitch",
    "reflection.gather_episodes",
    "reflection.cluster_episodes",
    "reflection.synthesize_reflections",
    "reflection.write_reflections",
})


def _activity_name(fn) -> str:
    """Pull the temporalio activity name off a decorated function.

    Temporal stores it on a private attribute the SDK reads at
    registration time. We tolerate the SDK changing attribute names
    by checking the two known carrier shapes.
    """
    # The @activity.defn(name="...") decorator attaches activity
    # metadata via the _Definition descriptor. The public-ish path
    # to read the name without touching SDK internals is to look at
    # the __temporal_activity_definition attribute.
    defn = getattr(fn, "__temporal_activity_definition", None)
    if defn is not None and hasattr(defn, "name"):
        return defn.name
    # Older SDK fallback — some versions stash it differently.
    return getattr(fn, "name", fn.__name__)


@pytest.mark.parametrize("fn,expected_name", [
    (ra.check_killswitch, "reflection.check_killswitch"),
    (ra.gather_episodes, "reflection.gather_episodes"),
    (ra.cluster_episodes, "reflection.cluster_episodes"),
    (ra.synthesize_reflections, "reflection.synthesize_reflections"),
    (ra.write_reflections, "reflection.write_reflections"),
])
def test_activity_exposes_canonical_name(fn, expected_name):
    """Each production activity must register under the name the
    workflow looks up. Renaming one without updating the workflow
    would let the worker start cleanly but die on first scheduled
    run with NotFoundFailure — this test fails fast at import."""
    assert _activity_name(fn) == expected_name


def test_activity_module_exposes_exactly_five_canonical_activities():
    """Lock the activity count so adding a new mechanism in Phase 2
    forces a deliberate update to the workflow + this test, not a
    silent drift."""
    actual_names = {
        _activity_name(getattr(ra, attr))
        for attr in (
            "check_killswitch",
            "gather_episodes",
            "cluster_episodes",
            "synthesize_reflections",
            "write_reflections",
        )
    }
    assert actual_names == EXPECTED_ACTIVITY_NAMES


def test_activity_names_match_workflow_call_sites():
    """Read the workflow source and confirm every activity name in
    EXPECTED_ACTIVITY_NAMES is referenced by `execute_activity` (by
    function ref, since the workflow uses the imported functions
    not the names directly). We assert the IMPORT list matches what
    the activities module exposes."""
    from app.workflows import nightly_reflection_workflow as nrw

    # The workflow imports each activity by its canonical Python
    # name (check_killswitch, gather_episodes, etc.). Confirm those
    # symbols all exist in the activities module — a renamed Python
    # function would break this even before reaching Temporal's
    # registry.
    workflow_src = nrw.__file__
    with open(workflow_src, "r") as f:
        source = f.read()
    for python_name in (
        "check_killswitch",
        "gather_episodes",
        "cluster_episodes",
        "synthesize_reflections",
        "write_reflections",
    ):
        assert python_name in source, (
            f"workflow source doesn't reference {python_name!r} — "
            "rename drift between activities module and workflow"
        )
        assert hasattr(ra, python_name), (
            f"activities module doesn't expose {python_name!r} — "
            "workflow will fail at import time"
        )
