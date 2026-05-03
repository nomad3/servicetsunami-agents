"""Migration assertions in this directory inspect a live Postgres schema and
therefore only make sense against a real database.  Mark every test below this
package as `integration` so the default unit run skips them.
"""
import pytest

# Apply integration marker to all collected items in this subtree.
def pytest_collection_modifyitems(config, items):
    for item in items:
        if "tests/migrations/" in str(item.path).replace("\\", "/"):
            item.add_marker(pytest.mark.integration)
