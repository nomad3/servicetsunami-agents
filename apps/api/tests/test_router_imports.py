"""Regression test for the router-import startup bug.

2026-05-19 incident: PR C of the emotions engine shipped
`apps/api/app/api/v1/emotion.py` with `from app.api.dependencies import ...`
when the correct path is `app.api.deps`. Unit tests passed because they
imported the underlying service. The error only fired at api startup
via `app.api.v1.routes` walking the router graph — and api exit(1)'d in
a crash loop for ~55 minutes before being hot-fixed in PR #591.

This test exercises the same import path that fires at startup. If any
router file has a broken import, this test fails in CI rather than at
boot.

Memory: feedback_test_router_startup.md.
"""
from __future__ import annotations


def test_routes_module_imports_cleanly():
    """Import the top-level v1 routes module. This in turn imports every
    router module via the `from app.api.v1 import (...)` block at the
    top of `routes.py`. If any of those router files has a broken
    import, this test fails immediately — same surface as api startup."""
    # Importing routes is the entire test — the assertion is "no
    # ImportError raised."
    from app.api.v1 import routes  # noqa: F401

    # Sanity: the router registry has at least the core routers.
    assert hasattr(routes, "router")
