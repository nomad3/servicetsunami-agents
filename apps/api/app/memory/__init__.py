"""Memory layer — single source of truth for recall, record, ingest.

This package is the Phase 1 Python implementation of the gRPC contract
defined in docs/plans/2026-04-07-memory-first-grpc-idl.proto. Phase 2
replaces this with a Rust gRPC client; consumers will not change.
"""
from app.memory.recall import recall
from app.memory.record import record_observation, record_commitment, record_goal
from app.memory.ingest import ingest_events

__all__ = [
    "recall",
    "record_observation",
    "record_commitment",
    "record_goal",
    "ingest_events",
]
