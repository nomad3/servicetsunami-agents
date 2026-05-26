"""T6.5 — Router-graph startup smoke for Luna Learn routes.

Per ``feedback_test_router_startup``: importing ``app.api.v1.routes``
walks the whole router graph at startup. A broken import in ANY of
its sub-routers crashes api boot — last seen 2026-05-19 when PR C of
the emotions engine landed with ``from app.api.dependencies import``
where the correct path was ``app.api.deps``; unit tests passed,
production crash-looped for ~55 minutes.

This smoke test exercises the same import surface for the three new
Luna Learn routes added in Phase 4:

  * ``POST /api/v1/learning/dispatch`` (T4.4c)
  * ``POST /api/v1/skills/execute-draft`` (T4.4d)
  * ``POST /api/v1/skills/install-learned`` (T4.4e)

If any of those modules drift (bad import, typo, schema reference
that doesn't exist), this test fails immediately in CI — same surface
as api startup, not later in a unit test that imports the underlying
service and bypasses the router glue.
"""
from __future__ import annotations


def test_routes_module_imports_cleanly():
    """The top-level v1 routes module imports every Luna Learn router
    via its ``from app.api.v1 import (...)`` block. If any one is
    broken at import time, this raises ImportError. The whole test
    is the assertion."""
    from app.api.v1 import routes  # noqa: F401

    # Sanity: the resolved router still has its core attributes so a
    # future refactor that accidentally aliases `routes.router` to a
    # new symbol surfaces here too.
    assert hasattr(routes, "router")


def test_learning_router_module_imports():
    """The standalone Luna Learn router module (T4.4b/c) imports
    cleanly. Surfaces a broken import even if the parent routes
    module is restructured to lazy-load."""
    from app.api.v1 import learning

    assert hasattr(learning, "router")


def test_skills_new_router_module_imports():
    """The skills_new module hosts both ``/execute-draft`` (T4.4d) and
    ``/install-learned`` (T4.4e). The plan's "no auto-runner" pattern
    + the install-learned 409 → suffix-with-vN retry loop both depend
    on this router being importable without side-effects."""
    from app.api.v1 import skills_new

    assert hasattr(skills_new, "router")


def test_learning_dispatch_route_registered():
    """``POST /api/v1/learning/dispatch`` (T4.4c) MUST be in the
    learning router's path set. The CLI's `alpha learn` + the
    WhatsApp inbound path both POST here; a silent unregistration
    breaks both surfaces."""
    from app.api.v1 import learning

    paths = {r.path for r in learning.router.routes}
    assert "/dispatch" in paths, (
        f"/dispatch missing from learning router; registered paths: {paths}"
    )


def test_skills_execute_draft_route_registered():
    """``POST /api/v1/skills/execute-draft`` (T4.4d) MUST be in the
    skills_new router's path set. ``run_synthetic_test`` (T2.5) in
    mcp-server POSTs here; a silent unregistration breaks every
    learn-from-media synthesis test path."""
    from app.api.v1 import skills_new

    paths = {r.path for r in skills_new.router.routes}
    assert "/execute-draft" in paths, (
        f"/execute-draft missing from skills_new router; "
        f"registered paths: {paths}"
    )


def test_skills_install_learned_route_registered():
    """``POST /api/v1/skills/install-learned`` (T4.4e) MUST be in the
    skills_new router's path set. ``install_skill`` (T2.6) in
    mcp-server POSTs here on every successful learn; without it the
    workflow can never persist a synthesized skill."""
    from app.api.v1 import skills_new

    paths = {r.path for r in skills_new.router.routes}
    assert "/install-learned" in paths, (
        f"/install-learned missing from skills_new router; "
        f"registered paths: {paths}"
    )


def test_top_level_router_mounts_luna_learn_prefixes():
    """Verifies the top-level v1 router actually mounts the learning
    and skills sub-routers at the spec'd prefixes. A future refactor
    that moves them under a different prefix (e.g. /v2/) breaks every
    leaf caller — better to fail here than at the first WhatsApp
    inbound after deploy."""
    from app.api.v1 import routes

    # Walk routes.router and collect the full paths it exposes —
    # FastAPI's APIRouter flattens nested includes so the top-level
    # routes always carry the full /learning/* + /skills/* prefixes.
    all_paths = {r.path for r in routes.router.routes}

    # The three T6.5-targeted routes — full prefixed paths.
    assert "/learning/dispatch" in all_paths, (
        "/learning/dispatch not mounted on top-level v1 router"
    )
    assert "/skills/execute-draft" in all_paths, (
        "/skills/execute-draft not mounted on top-level v1 router"
    )
    assert "/skills/install-learned" in all_paths, (
        "/skills/install-learned not mounted on top-level v1 router"
    )
