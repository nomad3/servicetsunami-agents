import os
import pytest

pytestmark = pytest.mark.skipif(
    not os.environ.get("MEMORY_CORE_URL"),
    reason="MEMORY_CORE_URL not set"
)

TENANT_ID = "0f134606-3906-44a5-9e88-6c2020f0f776"


def test_recall():
    """Recall returns metadata with timing info."""
    from app.generated import memory_pb2, memory_pb2_grpc
    import grpc
    channel = grpc.insecure_channel(os.environ["MEMORY_CORE_URL"])
    stub = memory_pb2_grpc.MemoryCoreStub(channel)
    response = stub.Recall(memory_pb2.RecallRequest(
        tenant_id=TENANT_ID, query="what is integral",
        top_k_per_type=5, total_token_budget=4000
    ))
    assert response.metadata.query_time_ms > 0
    assert response.metadata.query_time_ms < 5000


def test_record_commitment():
    """RecordCommitment writes without error."""
    from app.generated import memory_pb2, memory_pb2_grpc
    import grpc
    channel = grpc.insecure_channel(os.environ["MEMORY_CORE_URL"])
    stub = memory_pb2_grpc.MemoryCoreStub(channel)
    # Should not raise
    stub.RecordCommitment(memory_pb2.RecordCommitmentRequest(
        tenant_id=TENANT_ID, owner_agent_slug="test_agent",
        title="Test commitment from integration test", commitment_type="action"
    ))
