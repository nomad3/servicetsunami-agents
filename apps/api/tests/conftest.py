"""Shared pytest configuration for apps/api tests.

Some legacy test modules import the full FastAPI app at module scope, which in
turn binds SQLAlchemy models containing Postgres-only types (JSONB, pgvector).
Those modules can only run against a real Postgres + pgvector instance and are
marked `@pytest.mark.integration` (see `pytestmark = pytest.mark.integration`
at the top of each affected file).

When the default suite runs with `-m "not integration"`, we still need to keep
import-time errors from breaking *collection* — pytest evaluates the module
body before it consults the marker. We therefore short-circuit collection of
those files via `pytest_configure` (which has access to the resolved marker
expression) unless the user opts in via `-m integration` or runs the file by
path. This avoids parsing `sys.argv` ourselves, which is fragile under
pytest-xdist and any wrapper that mutates argv before pytest sees it.
"""
from __future__ import annotations

import os

# Files that will fail to import without a live Postgres + pgvector backend.
# Listed by basename; we use `collect_ignore` to skip them at the collection
# stage so they never even get imported in default runs.
_INTEGRATION_ONLY_FILES = {
    "test_api.py",
    "test_integrations.py",
    "test_internal_endpoints.py",
    "test_multi_provider.py",
    "test_oauth.py",
}


# Populated dynamically in pytest_configure based on the resolved -m marker
# expression. pytest reads collect_ignore after pytest_configure runs, so
# mutating it here is safe.
collect_ignore: list[str] = []


def _marker_expr_wants_integration(config) -> bool:
    """Return True iff the active -m expression *includes* integration tests.

    Uses pytest's resolved option value (`config.getoption("-m")`) instead of
    walking `sys.argv` ourselves, so it works under pytest-xdist, wrapper
    scripts, and pytest.ini-set markers.
    """
    expr = config.getoption("-m", default="") or ""
    if not expr:
        # No marker filter — collect everything.
        return True
    return "integration" in expr and "not integration" not in expr


def pytest_configure(config):
    """Make sure required env defaults exist for the unit suite.

    The fail-closed Settings() (see app/core/config.py) requires SECRET_KEY,
    API_INTERNAL_KEY, MCP_API_KEY at import time. CI sets these explicitly;
    locally we honour whatever the developer exported but fall back to the
    same fake values used in `.github/workflows/tests.yaml` so a fresh shell
    can run the suite without hand-exporting five variables.
    """
    defaults = {
        "SECRET_KEY": "test-secret-key-not-real-32bytes-12345",
        "API_INTERNAL_KEY": "test-internal-key-not-real-32bytes-12",
        "MCP_API_KEY": "test-mcp-key-not-real-24bytes-12345",
        "ENCRYPTION_KEY": "M0FsbHA0c3N3MHJkSXNUMHRhbGx5RmFrZUtleVgx",
        "DATABASE_URL": "sqlite:///./test.db",
    }
    for key, value in defaults.items():
        os.environ.setdefault(key, value)

    # Skip integration-only files unless the active marker filter wants them.
    if not _marker_expr_wants_integration(config):
        collect_ignore.extend(sorted(_INTEGRATION_ONLY_FILES))
