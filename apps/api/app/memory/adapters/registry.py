"""Runtime registry of source adapters.

Adapters register themselves at import time. Unknown source_type strings
in MemoryEvents fail-fast at ingest_events(). This is the open/closed
extension point — adding a source means writing one adapter file and
importing it from app.memory.adapters.__init__.
"""
from app.memory.adapters.protocol import SourceAdapter

_REGISTRY: dict[str, SourceAdapter] = {}


def register_adapter(adapter: SourceAdapter) -> None:
    if not adapter.source_type:
        raise ValueError("adapter.source_type must be a non-empty string")
    _REGISTRY[adapter.source_type] = adapter


def get_adapter(source_type: str) -> SourceAdapter:
    if source_type not in _REGISTRY:
        raise KeyError(f"No adapter registered for source_type={source_type!r}")
    return _REGISTRY[source_type]


def list_source_types() -> list[str]:
    return sorted(_REGISTRY.keys())


def unregister_adapter(source_type: str) -> None:
    """Remove an adapter from the registry. Used by tests for isolation;
    not called by production code."""
    _REGISTRY.pop(source_type, None)


def snapshot_registry() -> dict[str, SourceAdapter]:
    """Return a shallow copy of the registry. Used by tests to save+restore."""
    return dict(_REGISTRY)


def restore_registry(snapshot: dict[str, SourceAdapter]) -> None:
    """Replace the registry with a snapshot. Used by tests to restore state."""
    _REGISTRY.clear()
    _REGISTRY.update(snapshot)
