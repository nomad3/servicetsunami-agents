"""Source adapter contract.

Each adapter is a pure transformer: raw source data → list[MemoryEvent].
No DB writes, no side effects. The ingestion workflow handles persistence.
"""
from typing import Any, Protocol
from uuid import UUID
from app.memory.types import MemoryEvent


class SourceAdapter(Protocol):
    source_type: str

    async def ingest(
        self,
        raw: Any,
        source_metadata: dict,
        tenant_id: UUID,
    ) -> list[MemoryEvent]: ...

    def deduplication_key(self, raw: Any) -> str: ...
