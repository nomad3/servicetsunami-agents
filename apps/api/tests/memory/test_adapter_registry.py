"""Source adapters register at startup; recall + ingest validate via registry."""
import pytest


@pytest.fixture(autouse=True)
def isolate_registry():
    """Snapshot + restore the module-level registry around every test in this
    file so test order doesn't matter and real adapters registered elsewhere
    (when chat/email/etc adapters land) don't pollute these tests."""
    from app.memory.adapters.registry import snapshot_registry, restore_registry
    snap = snapshot_registry()
    yield
    restore_registry(snap)


def test_register_and_lookup_adapter():
    from app.memory.adapters.registry import register_adapter, get_adapter, list_source_types
    from app.memory.adapters.protocol import SourceAdapter

    class FakeAdapter:
        source_type = "test_fake"
        async def ingest(self, raw, source_metadata, tenant_id):
            return []
        def deduplication_key(self, raw):
            return f"fake:{raw}"

    register_adapter(FakeAdapter())
    assert "test_fake" in list_source_types()
    assert get_adapter("test_fake").source_type == "test_fake"


def test_unknown_adapter_raises():
    from app.memory.adapters.registry import get_adapter
    with pytest.raises(KeyError):
        get_adapter("nonexistent_source_type_xyz")


def test_isolate_registry_fixture_clears_state_between_tests():
    """Verify the autouse fixture actually restored state after the previous test.
    This test runs after test_register_and_lookup_adapter — if isolation works,
    'test_fake' should NOT be present at the start of this test."""
    from app.memory.adapters.registry import list_source_types
    assert "test_fake" not in list_source_types()
