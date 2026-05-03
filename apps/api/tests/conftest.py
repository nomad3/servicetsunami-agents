"""Shared pytest configuration for apps/api tests.

Some legacy test modules import the full FastAPI app at module scope, which in
turn binds SQLAlchemy models containing Postgres-only types (JSONB, pgvector).
Those modules can only run against a real Postgres + pgvector instance and are
marked `@pytest.mark.integration` (see `pytestmark = pytest.mark.integration`
at the top of each affected file).

When the default suite runs with `-m "not integration"`, we still need to keep
import-time errors from breaking *collection* — pytest evaluates the module
body before it consults the marker. We therefore short-circuit collection of
those files entirely unless the user explicitly opts in via the `integration`
marker expression or runs the file by path.
"""
from __future__ import annotations

import os
import sys

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


def _running_integration() -> bool:
    """Return True when the user's marker expression includes integration tests."""
    # `-m "integration"` or `-m "integration or X"` — anything that does NOT
    # exclude the marker. The most common default is `-m "not integration"`,
    # in which case we want to skip the heavy files entirely.
    argv = sys.argv
    for i, arg in enumerate(argv):
        if arg == "-m" and i + 1 < len(argv):
            expr = argv[i + 1]
            return "integration" in expr and "not integration" not in expr
        if arg.startswith("-m="):
            expr = arg.split("=", 1)[1]
            return "integration" in expr and "not integration" not in expr
    # No `-m` flag: pytest runs everything. Don't ignore.
    return True


# `collect_ignore` is consulted by pytest at collection time. Populating it
# only when the marker filter actively excludes integration tests means the
# integration job (which runs `-m integration`) still picks these files up.
collect_ignore: list[str] = []
if not _running_integration():
    collect_ignore.extend(sorted(_INTEGRATION_ONLY_FILES))


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
