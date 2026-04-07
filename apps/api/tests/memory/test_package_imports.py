"""Smoke test: the memory package and its public API are importable."""

def test_memory_package_imports():
    from app.memory import recall, record_observation, record_commitment
    from app.memory import ingest_events
    from app.memory.types import (
        MemoryEvent, RecallRequest, RecallResponse,
        EntitySummary, CommitmentSummary, EpisodeSummary,
    )
    assert callable(recall)
    assert callable(record_observation)
    assert callable(record_commitment)
    assert callable(ingest_events)

def test_recall_request_construction():
    from uuid import uuid4
    from app.memory.types import RecallRequest
    req = RecallRequest(
        tenant_id=uuid4(),
        agent_slug="luna",
        query="who is Ray Aristy",
    )
    assert req.top_k_per_type == 5  # default
    assert req.total_token_budget == 8000  # default

def test_memory_event_construction():
    from datetime import datetime, timezone
    from uuid import uuid4
    from app.memory.types import MemoryEvent
    ev = MemoryEvent(
        tenant_id=uuid4(),
        source_type="chat",
        source_id="msg-123",
        actor_slug="luna",
        occurred_at=datetime.now(timezone.utc),
        ingested_at=datetime.now(timezone.utc),
        kind="text",
        text="hello",
    )
    assert ev.source_type == "chat"
    assert ev.confidence == 1.0  # default

def test_recall_response_summarises_token_estimate():
    from app.memory.types import RecallResponse
    resp = RecallResponse()
    assert resp.total_tokens_estimate == 0
    assert resp.entities == []
