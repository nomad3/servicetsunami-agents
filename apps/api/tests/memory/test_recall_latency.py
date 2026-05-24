# apps/api/tests/memory/test_recall_latency.py
"""Recall latency micro-benchmark.

Soft target: p50 < 500ms on tenant 0f134606 with current data volume.
Hard target: p95 < 1500ms (the timeout).

This test is opt-in via -m latency to avoid slowing the regular test
suite. Run before merging Phase 1 to validate against the §11 SLO.
"""
import os, time, pytest
from uuid import UUID
from app.memory import recall
from app.memory.types import RecallRequest

PROD_TENANT = UUID("0f134606-3906-44a5-9e88-6c2020f0f776")


@pytest.fixture(scope="module", autouse=True)
def _warm_embedding_model():
    """Pre-load embedding model to measure steady-state performance."""
    from app.services import embedding_service
    embedding_service.embed_text("warmup", task_type="RETRIEVAL_QUERY")


@pytest.mark.latency
def test_recall_latency_p50(db_session):
    queries = [
        "who is Ray Aristy",
        "open commitments",
        "memory-first design",
        "what's our deal pipeline status",
        "competitor monitoring updates",
        "luna's preferences",
        "today's calendar",
        "recent github prs",
        "wolfpoint rebrand",
        "integral on-prem",
    ]
    latencies = []
    for q in queries * 3:  # 30 samples
        req = RecallRequest(
            tenant_id=PROD_TENANT,
            agent_slug="luna",
            query=q,
        )
        t0 = time.perf_counter()
        recall(db_session, req)
        latencies.append((time.perf_counter() - t0) * 1000)
    latencies.sort()
    p50 = latencies[len(latencies)//2]
    p95 = latencies[int(len(latencies)*0.95)]
    print(f"\nrecall p50={p50:.0f}ms p95={p95:.0f}ms")
    assert p50 < 500, f"p50 regressed: {p50:.0f}ms (target <500ms)"
    assert p95 < 1500, f"p95 exceeded hard timeout: {p95:.0f}ms"
