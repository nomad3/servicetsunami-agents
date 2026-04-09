# Memory-First Agent Platform — Phase 0 + Phase 1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:subagent-driven-development` (recommended) or `superpowers:executing-plans` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the scattered chat hot path memory operations with a single `apps/api/app/memory/` package that pre-loads recall context, runs post-chat consolidation via Temporal, and ships a Gemma4-based commitment classifier — without touching Rust, K8s, or Claude CLI subprocess management (those are Phase 2/3).

**Architecture:** New `memory/` package exposes `recall()`, `record_*()`, `ingest_events()` as Python functions designed around the eventual gRPC contract (Phase 2 reimplements the same API in Rust). All post-chat side effects (knowledge extraction, episode generation, commitment detection, behavioral signals) move from in-process daemon threads into a `PostChatMemoryWorkflow` Temporal workflow on the existing `servicetsunami-orchestration` queue. The `conversation_episodes` table is reused (not replaced); `session_journals` stays as the weekly rollup table. Embeddings stay in the existing generic `embeddings` table — no new per-table columns.

**Tech Stack:** Python 3.11, FastAPI, SQLAlchemy, Temporal Python SDK, pgvector, nomic-embed-text-v1.5 (existing local embedding service), Gemma4 via Ollama (existing local inference), pytest.

**Source documents:**
- Design: `docs/plans/2026-04-07-memory-first-agent-platform-design.md` (1241 lines, approved post-review)
- Current state: exploration report from 2026-04-07 (chat hot path call graph, file:line citations throughout this plan)

**Branch:** `feat/memory-first-phase-1` (already pushed). All work commits to this branch; no direct-to-main commits. Final acceptance gate is a draft PR that becomes ready-for-review only after §11 acceptance gates pass.

**Out of scope (Phase 2+):**
- Rust embedding-service / memory-core extraction
- gRPC wire transport (Phase 1 uses Python function calls; gRPC IDL is frozen as a forward-compat contract only)
- K8s migration, helm charts, warm chat-runtime pods
- Multi-source ingesters (email, calendar, jira, github, ads)
- Federation, voice, devices, scrapers

---

## Table of Contents

- [§1 Phase 0 — Prerequisites (Tasks 1-3)](#phase-0--prerequisites)
- [§2 Phase 1.1 — Memory package scaffolding (Tasks 4-6)](#phase-11--memory-package-scaffolding)
- [§3 Phase 1.2 — Schema migrations (Tasks 7-9)](#phase-12--schema-migrations)
- [§4 Phase 1.3 — `memory.recall()` (Tasks 10-13)](#phase-13--memoryrecall)
- [§5 Phase 1.4 — `memory.record_*()` (Tasks 14-15)](#phase-14--memoryrecord_)
- [§6 Phase 1.5 — Commitment classifier (Tasks 16-19)](#phase-15--commitment-classifier)
- [§7 Phase 1.6 — Memory workflows (Tasks 20-28)](#phase-16--memory-workflows)
- [§8 Phase 1.7 — Hot path cutover (Tasks 29-32)](#phase-17--hot-path-cutover)
- [§9 Phase 1.8 — Backfill (Task 33)](#phase-18--backfill)
- [§10 Phase 1.9 — Acceptance gates (Tasks 34-38)](#phase-19--acceptance-gates)
- [§11 Phase 1.10 — Rollout (Tasks 39-41)](#phase-110--rollout)

---

## Phase 0 — Prerequisites

**Goal:** Three small deliverables that must exist before Phase 1 can start. Fast (1 week max) but blocking.

### Task 1: Capture latency baseline of current chat hot path

**Why first:** §11 acceptance criteria require us to prove we didn't regress. We need numbers from BEFORE we touch the code.

**Files:**
- Create: `apps/api/scripts/baseline_chat_latency.py`
- Create: `docs/plans/baselines/2026-04-07-chat-latency-baseline.md`

- [ ] **Step 1: Write the script**

```python
# apps/api/scripts/baseline_chat_latency.py
"""Measure end-to-end chat latency on the current code path.

Sends N synthetic messages through POST /api/v1/chat/sessions/{id}/messages,
records p50/p95/p99 wall-clock latency. Run against local docker-compose stack.
"""
import asyncio, time, statistics, httpx, sys, os, json

API = os.environ.get("API_BASE_URL", "http://localhost:8001")
TOKEN = os.environ["BASELINE_TOKEN"]  # JWT for tenant 0f134606
SESSION_ID = os.environ["BASELINE_SESSION_ID"]  # pre-created chat session
N = int(os.environ.get("BASELINE_N", "20"))

PROMPTS = [
    "hey luna",
    "what are my open commitments",
    "remind me what we discussed yesterday",
    "who is Ray Aristy",
    "what is my next meeting",
    "what's the status of integral",
    "summarize the memory-first design doc",
    "thanks",
    "ok",
    "what platforms are we tracking competitors on",
]

async def main():
    latencies = []
    async with httpx.AsyncClient(timeout=60) as c:
        for i in range(N):
            prompt = PROMPTS[i % len(PROMPTS)]
            t0 = time.perf_counter()
            r = await c.post(
                f"{API}/api/v1/chat/sessions/{SESSION_ID}/messages",
                headers={"Authorization": f"Bearer {TOKEN}"},
                json={"content": prompt},
            )
            latencies.append(time.perf_counter() - t0)
            r.raise_for_status()
    latencies.sort()
    print(json.dumps({
        "n": N,
        "p50": latencies[len(latencies)//2],
        "p95": latencies[int(len(latencies)*0.95)],
        "p99": latencies[int(len(latencies)*0.99)],
        "mean": statistics.mean(latencies),
        "max": max(latencies),
    }, indent=2))

asyncio.run(main())
```

- [ ] **Step 2: Run it against local docker-compose stack**

```bash
cd apps/api
BASELINE_TOKEN=<jwt> BASELINE_SESSION_ID=<session_uuid> BASELINE_N=30 \
  python scripts/baseline_chat_latency.py | tee /tmp/baseline.json
```

Expected: prints JSON with p50/p95/p99/mean/max in seconds. No assertion — just capture.

- [ ] **Step 3: Save the result + commit**

Create `docs/plans/baselines/2026-04-07-chat-latency-baseline.md` with:
- Date, branch, commit hash (`git rev-parse HEAD`)
- Hardware (M4, RAM)
- The JSON output
- Notes: which tenant, session, what models were active (Sonnet vs Haiku), exploration_mode (should be `off` post-`07d77cb4`)
- A line saying "Phase 1 must not regress p95 by more than 30%"

```bash
git add apps/api/scripts/baseline_chat_latency.py docs/plans/baselines/2026-04-07-chat-latency-baseline.md
git commit -m "feat(memory-first): capture pre-Phase-1 chat latency baseline"
```

---

### Task 2: Build commitment-classifier gold set (200 examples, hybrid corpus)

**Why:** Design doc §14 open question #2 makes "Gemma4 commitment F1 ≥ 0.7" a Phase 1 prerequisite. We need a labeled gold set to measure against. **Hybrid: 100 real + 100 synthetic.**

**Files:**
- Create: `apps/api/scripts/sample_chat_corpus.py` (pulls 100 real chat messages)
- Create: `apps/api/scripts/generate_synthetic_commitments.py` (Gemma4 generates 100 edge cases)
- Create: `apps/api/tests/fixtures/commitment_gold_set.jsonl` (final labeled corpus)
- Create: `docs/plans/baselines/commitment-gold-set-protocol.md` (labeling rubric)

- [ ] **Step 1: Write the labeling rubric first**

`docs/plans/baselines/commitment-gold-set-protocol.md`:

```markdown
# Commitment Classifier Gold Set — Labeling Protocol

A "commitment" is a statement where THE SPEAKER (user or assistant) commits
themselves or someone else to a future action with a specific or implicit deadline.

## True commitment (label: 1)
- "I'll send you the report by Friday" — explicit, dated, first-person
- "Luna, follow up with Ray tomorrow" — directive to assistant
- "We need to ship this before the merge freeze" — first-person plural, dated
- "I promise I'll review the PR tonight" — explicit promise
- "Voy a llamar al cliente mañana" — Spanish, explicit, dated

## NOT a commitment (label: 0)
- "Ray usually sends reports on Fridays" — third-person description
- "It would be nice to ship before the freeze" — wish, not commitment
- "I sent the report yesterday" — past tense
- "What if we shipped on Friday?" — hypothetical / question
- "Gap 3 is about commitment tracking" — meta-discussion of the feature
- "The commitment record table has 47 rows" — describing data
- "I'm thinking about reviewing the PR" — intent without commitment

## Edge cases (label carefully)
- "I'll try to get to it" — soft. Label as 0 unless paired with deadline.
- "Maybe tomorrow" — hedged. Label 0.
- "Confirmed for 3pm Thursday" — meeting confirmation. Label 1.
- "I owe you that doc" — obligation acknowledgment. Label 1.

## JSONL format
Each line:
{"text": "...", "role": "user|assistant", "label": 0|1, "title": "<if 1>", "due_at": "<ISO or null>", "type": "action|response|delivery|meeting", "source": "real|synthetic", "labeled_by": "simon"}
```

- [ ] **Step 2: Sample 100 real messages from tenant `0f134606`**

`apps/api/scripts/sample_chat_corpus.py`:

```python
"""Sample 100 chat_messages from tenant 0f134606 for gold-set labeling."""
import json
from sqlalchemy import create_engine, text
import os

engine = create_engine(os.environ["DATABASE_URL"])
TENANT = "0f134606-3906-44a5-9e88-6c2020f0f776"

with engine.connect() as c:
    rows = c.execute(text("""
        SELECT id, role, content, created_at
        FROM chat_messages
        WHERE session_id IN (SELECT id FROM chat_sessions WHERE tenant_id = CAST(:t AS uuid))
          AND char_length(content) BETWEEN 20 AND 600
        ORDER BY random()
        LIMIT 100
    """), {"t": TENANT}).fetchall()

with open("apps/api/tests/fixtures/commitment_gold_set_unlabeled.jsonl", "w") as f:
    for r in rows:
        f.write(json.dumps({
            "text": r.content,
            "role": r.role,
            "label": None,
            "source": "real",
            "message_id": str(r.id),
            "created_at": r.created_at.isoformat(),
        }) + "\n")
print(f"Wrote 100 unlabeled rows")
```

Run:
```bash
DATABASE_URL=postgresql://postgres:postgres@localhost:8003/servicetsunami \
  python apps/api/scripts/sample_chat_corpus.py
```

- [ ] **Step 3: Generate 100 synthetic edge cases via Gemma4**

`apps/api/scripts/generate_synthetic_commitments.py`:

```python
"""Generate 50 commitments + 50 non-commitments via Gemma4 for the gold set."""
import json, requests, sys

OLLAMA = "http://localhost:11434/api/generate"

PROMPT_COMMITMENT = """Generate 50 short messages (20-200 chars) where someone makes a clear commitment to a future action.
Mix: first-person ("I'll..."), directives to assistants, plural ("we need to..."),
explicit deadlines, implicit deadlines. Mix English and Spanish. One per line. No numbering, no quotes."""

PROMPT_NEGATIVE = """Generate 50 short messages (20-200 chars) that look like they MIGHT be commitments
but are NOT: third-person descriptions, past tense, hypotheticals, questions, meta-discussion of features,
descriptions of data, soft intent without deadline. Mix English and Spanish. One per line."""

def gen(prompt):
    r = requests.post(OLLAMA, json={"model": "gemma4", "prompt": prompt, "stream": False})
    return [l.strip() for l in r.json()["response"].splitlines() if l.strip()][:50]

with open("apps/api/tests/fixtures/commitment_gold_set_synthetic.jsonl", "w") as f:
    for txt in gen(PROMPT_COMMITMENT):
        f.write(json.dumps({"text": txt, "role": "user", "label": 1, "source": "synthetic", "title": txt[:80], "due_at": None, "type": "action"}) + "\n")
    for txt in gen(PROMPT_NEGATIVE):
        f.write(json.dumps({"text": txt, "role": "user", "label": 0, "source": "synthetic"}) + "\n")
```

- [ ] **Step 4: Hand-label the 100 real messages**

Open `commitment_gold_set_unlabeled.jsonl` in your editor. For each row, set `label` to 0 or 1 per the rubric. For label=1 rows, also fill `title`, `due_at` (or null), `type`. **Budget: 90-120 minutes of focused work.** Don't shortcut this — the F1 ≥ 0.7 gate depends on label quality.

- [ ] **Step 5: Merge into final fixture**

```bash
cat apps/api/tests/fixtures/commitment_gold_set_unlabeled.jsonl \
    apps/api/tests/fixtures/commitment_gold_set_synthetic.jsonl \
    > apps/api/tests/fixtures/commitment_gold_set.jsonl
wc -l apps/api/tests/fixtures/commitment_gold_set.jsonl  # expect 200
```

- [ ] **Step 6: Commit**

```bash
git add apps/api/scripts/sample_chat_corpus.py \
        apps/api/scripts/generate_synthetic_commitments.py \
        apps/api/tests/fixtures/commitment_gold_set.jsonl \
        docs/plans/baselines/commitment-gold-set-protocol.md
git commit -m "feat(memory-first): commitment classifier gold set (200 hybrid examples)"
```

---

### Task 3: Freeze the gRPC IDL as a forward-compat contract

**Why:** Design doc §14 open question #3. The Python `memory/` package signatures must match the eventual Rust gRPC service. Writing the .proto first prevents Phase 2 from being a re-architecture.

**Files:**
- Create: `docs/plans/2026-04-07-memory-first-grpc-idl.proto`

- [ ] **Step 1: Write the full IDL**

Use design doc §5.2 as the starting point but write the **complete** IDL — not abbreviated. Include all message types referenced in `RecallResponse` (`EntitySummary`, `ObservationSummary`, `RelationSummary`, `CommitmentSummary`, `GoalSummary`, `ConversationSummary`, `EpisodeSummary`, `WorldStateAssertion`, `RecallMetadata`, `MemoryEvent`).

Constraints to encode:
- `RecallRequest.agent_slug` is required (visibility filter)
- `RecallRequest.total_token_budget` defaults to 8000
- `IngestEventsRequest.workflow_id` is OPTIONAL (sync writes from API have no workflow)
- `MemoryEvent.source_type` is `string` (not enum) — registry pattern, not a Literal
- All UUIDs as `string` (gRPC convention)
- All timestamps as `google.protobuf.Timestamp`

- [ ] **Step 2: Lint with `protoc`**

```bash
brew install protobuf  # if not installed
protoc --proto_path=docs/plans --python_out=/tmp 2026-04-07-memory-first-grpc-idl.proto
```

Expected: zero errors. The generated .py is throwaway — we just need protoc to validate the syntax.

- [ ] **Step 3: Commit**

```bash
git add docs/plans/2026-04-07-memory-first-grpc-idl.proto
git commit -m "feat(memory-first): freeze gRPC IDL for Phase 2 forward-compat"
```

---

## Phase 1.1 — Memory package scaffolding

**Goal:** Create the empty `apps/api/app/memory/` package with type definitions and the public function signatures, no implementation. Locks in the API contract before any logic moves.

### Task 4: Create `apps/api/app/memory/` package skeleton

**Files:**
- Create: `apps/api/app/memory/__init__.py`
- Create: `apps/api/app/memory/types.py`
- Create: `apps/api/app/memory/recall.py`
- Create: `apps/api/app/memory/record.py`
- Create: `apps/api/app/memory/ingest.py`
- Create: `apps/api/app/memory/visibility.py`
- Create: `apps/api/app/memory/adapters/__init__.py`
- Create: `apps/api/tests/memory/__init__.py`
- Create: `apps/api/tests/memory/test_package_imports.py`

- [ ] **Step 1: Write the failing import test**

```python
# apps/api/tests/memory/test_package_imports.py
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
```

- [ ] **Step 2: Run it to verify it fails**

```bash
cd apps/api && pytest tests/memory/test_package_imports.py -v
```

Expected: `ModuleNotFoundError: No module named 'app.memory'`

- [ ] **Step 3: Create the package files (stubs only)**

`apps/api/app/memory/__init__.py`:

```python
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
```

`apps/api/app/memory/types.py`: (see Task 5)

`apps/api/app/memory/recall.py`:

```python
"""Memory recall — pre-loads context for the chat hot path.

This module is the entry point for all recall operations. The hot path
calls `recall()` ONCE per chat turn before dispatching to the CLI;
no in-prompt "recall tool" exists in this design.
"""
from typing import Optional
from sqlalchemy.orm import Session
from app.memory.types import RecallRequest, RecallResponse


def recall(db: Session, request: RecallRequest) -> RecallResponse:
    """Pre-load memory context for a chat turn.

    Takes a RecallRequest dataclass (mirrors the gRPC IDL exactly) so
    Phase 2 cutover to Rust gRPC client is a no-op for callers.
    Implementation in Task 10.
    """
    raise NotImplementedError("Task 10 implements this")
```

`apps/api/app/memory/record.py`:

```python
"""Synchronous memory write operations.

These are the SMALL, FAST writes that happen on the request thread:
single observation, single commitment, single goal. Bulk writes go
through `ingest_events()` which is async via Temporal.
"""
from uuid import UUID
from sqlalchemy.orm import Session


def record_observation(db: Session, tenant_id: UUID, **kwargs):
    """Implementation in Task 14."""
    raise NotImplementedError("Task 14 implements this")


def record_commitment(db: Session, tenant_id: UUID, **kwargs):
    """Implementation in Task 14."""
    raise NotImplementedError("Task 14 implements this")


def record_goal(db: Session, tenant_id: UUID, **kwargs):
    """Implementation in Task 14."""
    raise NotImplementedError("Task 14 implements this")
```

`apps/api/app/memory/ingest.py`:

```python
"""Bulk ingestion entry point — receives MemoryEvents from source adapters."""
from uuid import UUID
from sqlalchemy.orm import Session
from app.memory.types import MemoryEvent


def ingest_events(
    db: Session,
    tenant_id: UUID,
    events: list[MemoryEvent],
    workflow_id: str | None = None,
):
    """Implementation in Task 15."""
    raise NotImplementedError("Task 15 implements this")
```

`apps/api/app/memory/visibility.py`:

```python
"""Visibility filter for multi-agent scoping. Used by recall queries.

Implementation in Task 11.
"""
```

`apps/api/app/memory/adapters/__init__.py`:

```python
"""Source adapters: chat (Phase 1), email/calendar/jira/github/ads (Phase 3b)."""
```

- [ ] **Step 4: Run the test to verify it passes**

```bash
cd apps/api && pytest tests/memory/test_package_imports.py -v
```

Expected: PASS (the stubs raise NotImplementedError but the imports succeed).

- [ ] **Step 5: Commit**

```bash
git add apps/api/app/memory apps/api/tests/memory
git commit -m "feat(memory-first): scaffold apps/api/app/memory/ package"
```

---

### Task 5: Define type contracts in `memory/types.py`

**Files:**
- Modify: `apps/api/app/memory/types.py`
- Modify: `apps/api/tests/memory/test_package_imports.py` (extend)

- [ ] **Step 1: Write the failing test for type construction**

Append to `test_package_imports.py`:

```python
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
```

- [ ] **Step 2: Run it to verify it fails**

```bash
pytest tests/memory/test_package_imports.py::test_recall_request_construction -v
```

Expected: ImportError or AttributeError.

- [ ] **Step 3: Implement `types.py`**

```python
# apps/api/app/memory/types.py
"""Type contracts for the memory package.

These dataclasses mirror the gRPC IDL at
docs/plans/2026-04-07-memory-first-grpc-idl.proto. Phase 2 generates
equivalent Python bindings from the .proto and replaces these — but
the field names and defaults must match exactly.
"""
from __future__ import annotations
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Literal, Optional
from uuid import UUID


@dataclass
class RecallRequest:
    tenant_id: UUID
    agent_slug: str
    query: str
    chat_session_id: Optional[UUID] = None
    top_k_per_type: int = 5
    total_token_budget: int = 8000
    source_filter: Optional[list[str]] = None


@dataclass
class EntitySummary:
    id: UUID
    name: str
    category: Optional[str]
    description: Optional[str]
    confidence: float
    similarity: float
    source_type: Optional[str] = None


@dataclass
class ObservationSummary:
    id: UUID
    entity_id: UUID
    content: str
    confidence: float
    similarity: float
    created_at: datetime


@dataclass
class RelationSummary:
    id: UUID
    from_entity: str
    to_entity: str
    relation_type: str
    confidence: float


@dataclass
class CommitmentSummary:
    id: UUID
    title: str
    state: str
    due_at: Optional[datetime]
    priority: str
    similarity: float


@dataclass
class GoalSummary:
    id: UUID
    title: str
    state: str
    progress_pct: int
    priority: str
    similarity: float


@dataclass
class ConversationSummary:
    id: UUID
    role: str
    content: str
    created_at: datetime
    similarity: float


@dataclass
class EpisodeSummary:
    """Summary of a `conversation_episodes` row (the existing table)."""
    id: UUID
    session_id: Optional[UUID]
    summary: str
    key_topics: list[str]
    key_entities: list[str]
    created_at: datetime
    similarity: float


@dataclass
class ContradictionSummary:
    assertion_id: UUID
    subject: str
    predicate: str
    winning_value: str
    losing_value: str
    losing_source: str


@dataclass
class RecallMetadata:
    elapsed_ms: float
    used_keyword_fallback: bool = False
    degraded: bool = False
    truncated_for_budget: bool = False


@dataclass
class RecallResponse:
    entities: list[EntitySummary] = field(default_factory=list)
    observations: list[ObservationSummary] = field(default_factory=list)
    relations: list[RelationSummary] = field(default_factory=list)
    commitments: list[CommitmentSummary] = field(default_factory=list)
    goals: list[GoalSummary] = field(default_factory=list)
    past_conversations: list[ConversationSummary] = field(default_factory=list)
    episodes: list[EpisodeSummary] = field(default_factory=list)
    contradictions: list[ContradictionSummary] = field(default_factory=list)
    total_tokens_estimate: int = 0
    metadata: Optional[RecallMetadata] = None


@dataclass
class MemoryEvent:
    tenant_id: UUID
    source_type: str  # registry-validated, NOT a Literal — see adapters/registry.py
    source_id: str
    occurred_at: datetime
    ingested_at: datetime
    kind: Literal["text", "structured", "media"]
    actor_slug: Optional[str] = None
    source_metadata: dict[str, Any] = field(default_factory=dict)
    text: Optional[str] = None
    structured: Optional[dict[str, Any]] = None
    media_ref: Optional[str] = None
    proposed_entities: list[dict[str, Any]] = field(default_factory=list)
    proposed_observations: list[dict[str, Any]] = field(default_factory=list)
    proposed_relations: list[dict[str, Any]] = field(default_factory=list)
    proposed_commitments: list[dict[str, Any]] = field(default_factory=list)
    confidence: float = 1.0
    visibility: str = "tenant_wide"
```

- [ ] **Step 4: Run tests**

```bash
pytest tests/memory/test_package_imports.py -v
```

Expected: 4 passing.

- [ ] **Step 5: Commit**

```bash
git add apps/api/app/memory/types.py apps/api/tests/memory/test_package_imports.py
git commit -m "feat(memory-first): type contracts for memory package"
```

---

### Task 6: Source adapter Protocol + registry

**Files:**
- Create: `apps/api/app/memory/adapters/registry.py`
- Create: `apps/api/app/memory/adapters/protocol.py`
- Create: `apps/api/tests/memory/test_adapter_registry.py`

- [ ] **Step 1: Write the failing test**

```python
# apps/api/tests/memory/test_adapter_registry.py
"""Source adapters register at startup; recall + ingest validate via registry."""
import pytest

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
```

- [ ] **Step 2: Run to verify failure**

```bash
pytest tests/memory/test_adapter_registry.py -v
```

Expected: ImportError.

- [ ] **Step 3: Implement protocol + registry**

`apps/api/app/memory/adapters/protocol.py`:

```python
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
```

`apps/api/app/memory/adapters/registry.py`:

```python
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
```

- [ ] **Step 4: Run tests**

```bash
pytest tests/memory/test_adapter_registry.py -v
```

Expected: 2 passing.

- [ ] **Step 5: Commit**

```bash
git add apps/api/app/memory/adapters apps/api/tests/memory/test_adapter_registry.py
git commit -m "feat(memory-first): source adapter protocol + runtime registry"
```

---

## Phase 1.2 — Schema migrations

**Goal:** Three additive migrations. No destructive changes. Each is independently reversible.

### Task 7: Migration 086 — extend `conversation_episodes` for window-based triggers

**Why:** Design doc §4 (per resolved C1+C2): reuse the existing `conversation_episodes` table instead of creating a new `session_journals` collision. Add the columns the EpisodeWorkflow needs.

**Files:**
- Create: `apps/api/migrations/086_extend_conversation_episodes.sql`
- Create: `apps/api/tests/migrations/test_086_extend_conversation_episodes.py`

- [ ] **Step 1: Write the failing test**

```python
# apps/api/tests/migrations/test_086_extend_conversation_episodes.py
import os, pytest
from sqlalchemy import create_engine, inspect

@pytest.fixture
def engine():
    return create_engine(os.environ["DATABASE_URL"])

def test_conversation_episodes_has_window_columns(engine):
    cols = {c["name"] for c in inspect(engine).get_columns("conversation_episodes")}
    assert "window_start" in cols
    assert "window_end" in cols
    assert "trigger_reason" in cols
    assert "agent_slug" in cols
    assert "generated_by" in cols

def test_conversation_episodes_unique_window_constraint(engine):
    with engine.connect() as c:
        from sqlalchemy import text
        result = c.execute(text("""
            SELECT conname FROM pg_constraint
            WHERE conrelid = 'conversation_episodes'::regclass
              AND contype = 'u'
              AND conname = 'uk_conv_episodes_session_window'
        """)).first()
        assert result is not None
```

- [ ] **Step 2: Run to verify it fails**

```bash
DATABASE_URL=postgresql://postgres:postgres@localhost:8003/servicetsunami \
  pytest tests/migrations/test_086_extend_conversation_episodes.py -v
```

Expected: AssertionError on missing columns.

- [ ] **Step 3: Write the migration**

```sql
-- apps/api/migrations/086_extend_conversation_episodes.sql
-- Memory-First Phase 1: extend conversation_episodes for window-based EpisodeWorkflow.
-- The existing table (migration 075) is reused; this adds the columns the
-- new workflow needs. All additive — no destructive changes.

ALTER TABLE conversation_episodes
    ADD COLUMN IF NOT EXISTS window_start TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS window_end TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS trigger_reason VARCHAR(30),
    ADD COLUMN IF NOT EXISTS agent_slug VARCHAR(100),
    ADD COLUMN IF NOT EXISTS generated_by VARCHAR(50);

-- Unique constraint prevents duplicate episodes from concurrent triggers.
-- Backfill: existing rows have window_start = window_end = created_at.
UPDATE conversation_episodes
   SET window_start = created_at,
       window_end = created_at,
       trigger_reason = 'legacy',
       generated_by = 'unknown'
 WHERE window_start IS NULL;

ALTER TABLE conversation_episodes
    ADD CONSTRAINT uk_conv_episodes_session_window
    UNIQUE (session_id, window_start, window_end);

CREATE INDEX IF NOT EXISTS idx_conv_episodes_window
    ON conversation_episodes (session_id, window_end DESC);
```

- [ ] **Step 4: Update the `ConversationEpisode` ORM model**

Migration adds columns at the SQL level — the ORM must mirror them or `ConversationEpisode(window_start=..., trigger_reason=..., ...)` raises `TypeError`.

Edit `apps/api/app/models/conversation_episode.py` and add:

```python
# Inside ConversationEpisode class
window_start = Column(DateTime(timezone=True), nullable=True)
window_end = Column(DateTime(timezone=True), nullable=True)
trigger_reason = Column(String(30), nullable=True)
agent_slug = Column(String(100), nullable=True)
generated_by = Column(String(50), nullable=True)
```

Add a tiny ORM round-trip test in the same migration test file:

```python
def test_conversation_episode_orm_accepts_new_columns(db_session_fixture, test_tenant_fixture):
    from datetime import datetime, timezone
    from app.models.conversation_episode import ConversationEpisode
    ep = ConversationEpisode(
        tenant_id=test_tenant_fixture.id,
        summary="test",
        window_start=datetime.now(timezone.utc),
        window_end=datetime.now(timezone.utc),
        trigger_reason="test",
        generated_by="test",
    )
    db_session_fixture.add(ep)
    db_session_fixture.flush()
    assert ep.id is not None
```

- [ ] **Step 5: Apply via the auto-migrations runner**

```bash
docker-compose exec -T api python -c "from app.db.migrations import apply_pending; apply_pending()"
```

(Or restart api container — `_migrations` table tracks applied migrations.)

- [ ] **Step 6: Run the test to verify pass**

```bash
pytest tests/migrations/test_086_extend_conversation_episodes.py -v
```

Expected: 3 passing (2 schema checks + 1 ORM round-trip).

- [ ] **Step 7: Commit**

```bash
git add apps/api/migrations/086_extend_conversation_episodes.sql \
        apps/api/app/models/conversation_episode.py \
        apps/api/tests/migrations
git commit -m "feat(memory-first): migration 086 — extend conversation_episodes (SQL + ORM)"
```

---

### Task 8: Migration 087 — visibility columns + indexes

**Why:** Design doc §7. Multi-agent scoping needs `visibility` + `visible_to` on memory tables. Add composite indexes for the recall filter.

**Files:**
- Create: `apps/api/migrations/087_add_visibility_scoping.sql`
- Create: `apps/api/tests/migrations/test_087_visibility_scoping.py`

- [ ] **Step 1: Write the failing test**

```python
# apps/api/tests/migrations/test_087_visibility_scoping.py
import os, pytest
from sqlalchemy import create_engine, inspect, text

@pytest.fixture
def engine():
    return create_engine(os.environ["DATABASE_URL"])

@pytest.mark.parametrize("table", [
    "knowledge_entities",
    "commitment_records",
    "goal_records",
    "agent_memories",
    "behavioral_signals",
])
def test_table_has_visibility_columns(engine, table):
    cols = {c["name"] for c in inspect(engine).get_columns(table)}
    assert "visibility" in cols, f"{table} missing visibility column"
    assert "visible_to" in cols, f"{table} missing visible_to column"

def test_visibility_index_exists(engine):
    with engine.connect() as c:
        idx = c.execute(text("""
            SELECT indexname FROM pg_indexes
            WHERE tablename = 'knowledge_entities'
              AND indexname = 'idx_knowledge_entities_tenant_visibility_owner'
        """)).first()
        assert idx is not None
```

- [ ] **Step 2: Run to verify it fails**

```bash
pytest tests/migrations/test_087_visibility_scoping.py -v
```

- [ ] **Step 3: Write the migration**

```sql
-- apps/api/migrations/087_add_visibility_scoping.sql
-- Memory-First Phase 1: multi-agent visibility scoping (design doc §7).
-- Adds visibility + visible_to to memory tables. Default 'tenant_wide'
-- preserves current behavior (all agents in a tenant see everything).

DO $$
DECLARE
    t TEXT;
BEGIN
    FOR t IN SELECT unnest(ARRAY[
        'knowledge_entities',
        'commitment_records',
        'goal_records',
        'agent_memories',
        'behavioral_signals'
    ]) LOOP
        EXECUTE format(
            'ALTER TABLE %I
                 ADD COLUMN IF NOT EXISTS visibility VARCHAR(20) NOT NULL DEFAULT ''tenant_wide'',
                 ADD COLUMN IF NOT EXISTS visible_to TEXT[]', t);
    END LOOP;
END $$;

-- Composite indexes for the recall visibility filter (design review M9).
-- knowledge_entities is the hottest read path.
CREATE INDEX IF NOT EXISTS idx_knowledge_entities_tenant_visibility_owner
    ON knowledge_entities (tenant_id, visibility, owner_agent_slug)
    WHERE visibility != 'tenant_wide';

CREATE INDEX IF NOT EXISTS idx_knowledge_entities_visible_to_gin
    ON knowledge_entities USING GIN (visible_to)
    WHERE visibility = 'agent_group';

CREATE INDEX IF NOT EXISTS idx_commitments_tenant_visibility_owner
    ON commitment_records (tenant_id, visibility, owner_agent_slug)
    WHERE visibility != 'tenant_wide';

CREATE INDEX IF NOT EXISTS idx_agent_memories_tenant_visibility_owner
    ON agent_memories (tenant_id, visibility, owner_agent_slug)
    WHERE visibility != 'tenant_wide';
```

- [ ] **Step 4: Apply + verify + commit**

```bash
docker-compose exec -T api python -c "from app.db.migrations import apply_pending; apply_pending()"
pytest tests/migrations/test_087_visibility_scoping.py -v
git add apps/api/migrations/087_add_visibility_scoping.sql apps/api/tests/migrations/test_087_visibility_scoping.py
git commit -m "feat(memory-first): migration 087 — visibility scoping + indexes"
```

---

### Task 9: Migration 088 — add `workflow_id` to `memory_activities` (only)

**Why:** Design doc §3 (resolved fix #5): drop `memory_events`, reuse `memory_activities` for audit. The existing model already has most columns we need: `event_type`, `description`, `source`, `event_metadata` (JSON, mapped to `metadata`), `entity_id`, `memory_id`, `workflow_run_id`, `agent_id`, `user_id`. The ONLY thing missing for Temporal traceability is `workflow_id` (the workflow type ID, not the run ID). Source-specific identifiers (source_id, source_type details, target_table, target_id, actor_slug) all live in `event_metadata` JSON — no schema change needed.

**Files:**
- Read first: `apps/api/app/models/memory_activity.py` (verify current columns)
- Create: `apps/api/migrations/088_memory_activities_workflow_id.sql`
- Create: `apps/api/tests/migrations/test_088_memory_activities_audit.py`

- [ ] **Step 1: Failing test**

```python
import os, pytest
from sqlalchemy import create_engine, inspect

@pytest.fixture
def engine():
    return create_engine(os.environ["DATABASE_URL"])

def test_memory_activities_has_workflow_id_column(engine):
    cols = {c["name"] for c in inspect(engine).get_columns("memory_activities")}
    assert "workflow_id" in cols
    # Existing columns we rely on:
    assert "event_type" in cols
    assert "description" in cols
    assert "source" in cols
    assert "metadata" in cols  # mapped to event_metadata in ORM
    assert "workflow_run_id" in cols
```

- [ ] **Step 2: Run, verify fail.**

- [ ] **Step 3: Migration**

```sql
-- apps/api/migrations/088_memory_activities_workflow_id.sql
-- Memory-First Phase 1: add workflow_id (workflow type) to memory_activities.
-- workflow_run_id already exists. Source identifiers (source_id, target_table,
-- target_id, actor_slug) are stored in the existing `metadata` JSON column.
ALTER TABLE memory_activities
    ADD COLUMN IF NOT EXISTS workflow_id VARCHAR(200);

CREATE INDEX IF NOT EXISTS idx_memory_activities_workflow
    ON memory_activities (workflow_id) WHERE workflow_id IS NOT NULL;

-- For source-id lookups during dedup, use a GIN expression index on the JSON.
CREATE INDEX IF NOT EXISTS idx_memory_activities_source_ref
    ON memory_activities ((metadata->>'source_id'), (metadata->>'source_type'))
    WHERE metadata IS NOT NULL;
```

- [ ] **Step 4: Update the ORM model**

Add the `workflow_id` field to `apps/api/app/models/memory_activity.py`:

```python
# Add to MemoryActivity class, near workflow_run_id:
workflow_id = Column(String(200), nullable=True)  # Temporal workflow type ID
```

- [ ] **Step 5: Apply + test + commit**

```bash
docker-compose exec -T api python -c "from app.db.migrations import apply_pending; apply_pending()"
pytest tests/migrations/test_088_memory_activities_audit.py -v
git add apps/api/migrations/088_memory_activities_workflow_id.sql \
        apps/api/app/models/memory_activity.py \
        apps/api/tests/migrations/test_088_memory_activities_audit.py
git commit -m "feat(memory-first): migration 088 — workflow_id column on memory_activities"
```

---

## Phase 1.3 — `memory.recall()`

**Goal:** Move the existing `build_memory_context()` (319 lines, `apps/api/app/services/memory_recall.py:342-661`) into `memory/recall.py` as a pure function returning `RecallResponse`. Add visibility filter. Add hard timeout. Hot path will switch in Phase 1.7.

**SIGNATURE NOTE (locked in by code review of PR #130, post-Phase-1.2):**
The `recall()` function takes a `RecallRequest` dataclass, NOT positional args:

```python
def recall(db: Session, request: RecallRequest) -> RecallResponse: ...
```

All test code blocks in this section show the legacy positional form for readability — when implementing, every caller MUST construct a `RecallRequest` first:

```python
from app.memory.types import RecallRequest
req = RecallRequest(tenant_id=t, agent_slug="luna", query="who is Ray")
resp = recall(db, req)
```

This contract mirrors the gRPC IDL exactly so Phase 2 cutover to a Rust gRPC client is a no-op for callers. The stub at `apps/api/app/memory/recall.py` already takes the dataclass form (committed in PR #130 review fix-up).

### Task 10: Port `build_memory_context()` to `memory.recall()`

**Files:**
- Modify: `apps/api/app/memory/recall.py`
- Create: `apps/api/app/memory/_query.py` (internal — pgvector query helpers, factored out)
- Create: `apps/api/tests/memory/test_recall.py`
- Reference (READ ONLY, do not modify yet): `apps/api/app/services/memory_recall.py`

**Strategy:** Copy-then-adapt, NOT cut-and-paste. The old function is still called from `agent_router.py:357-365` until Phase 1.7. We need both to coexist temporarily.

- [ ] **Step 1: Read the existing implementation end-to-end**

```bash
cd apps/api && wc -l app/services/memory_recall.py
# Expect: ~700 lines
```

Read these specific functions in `memory_recall.py`:
- `build_memory_context()` — main entry (lines ~342-661)
- `_build_anticipatory_context()` (lines ~48 onwards)
- Any helper that's called from `build_memory_context`

Take notes on:
- Which DB tables it queries (you should find: `knowledge_entities`, `agent_memories`, `knowledge_observations`, `knowledge_relations`, `conversation_episodes`, `world_state_assertions`, `channel_events`)
- Which functions it calls in `embedding_service` (`embed_text`, `search_entities_semantic`, `search_memories_semantic`)
- Where it logs RL experiences (`log_experience` call ~lines 628-653)
- What side effects it has (UPDATE statements on `recall_count`, `last_recalled_at`, `access_count`, `last_accessed_at`)

- [ ] **Step 2: Write the failing recall test**

```python
# apps/api/tests/memory/test_recall.py
"""Recall tests use the SAME pytest fixtures as the existing memory tests
(see apps/api/tests/test_memory_system.py for the pattern). We require a
real Postgres because pgvector queries do not work on SQLite/in-memory.
"""
import pytest
from uuid import uuid4
from app.memory import recall
from app.memory.types import RecallResponse


@pytest.mark.integration
def test_recall_returns_response_object(db_session_fixture, test_tenant_fixture):
    resp = recall(
        db_session_fixture,
        tenant_id=test_tenant_fixture.id,
        agent_slug="luna",
        query="who is Ray Aristy",
    )
    assert isinstance(resp, RecallResponse)
    assert resp.metadata is not None
    assert resp.metadata.elapsed_ms > 0


@pytest.mark.integration
def test_recall_respects_total_token_budget(db_session_fixture, test_tenant_fixture):
    resp = recall(
        db_session_fixture,
        tenant_id=test_tenant_fixture.id,
        agent_slug="luna",
        query="status update",
        total_token_budget=500,  # very tight
    )
    # Approximate token estimate: 4 chars per token
    assert resp.total_tokens_estimate <= 500
    assert resp.metadata.truncated_for_budget is True


@pytest.mark.integration
def test_recall_keyword_fallback_when_embedding_unavailable(
    db_session_fixture, test_tenant_fixture, monkeypatch
):
    """If embedding_service raises, recall falls back to ILIKE keyword search
    and sets metadata.used_keyword_fallback=True. Don't crash, don't return empty."""
    from app.services import embedding_service
    monkeypatch.setattr(
        embedding_service, "embed_text",
        lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("embedding down"))
    )
    resp = recall(
        db_session_fixture,
        tenant_id=test_tenant_fixture.id,
        agent_slug="luna",
        query="ray",
    )
    assert resp.metadata.used_keyword_fallback is True


@pytest.mark.integration
def test_recall_hard_timeout(db_session_fixture, test_tenant_fixture, monkeypatch):
    """A 1500ms hard timeout returns a degraded response, not an exception."""
    import time
    from app.services import embedding_service
    monkeypatch.setattr(
        embedding_service, "embed_text",
        lambda *a, **kw: (time.sleep(2.0), [0.0]*768)[1]
    )
    resp = recall(
        db_session_fixture,
        tenant_id=test_tenant_fixture.id,
        agent_slug="luna",
        query="anything",
    )
    assert resp.metadata.degraded is True
    assert resp.metadata.elapsed_ms < 2000  # bailed before the 2s sleep finished
```

- [ ] **Step 3: Run to verify failure**

```bash
pytest tests/memory/test_recall.py -v -m integration
```

Expected: NotImplementedError (the stub raises).

- [ ] **Step 4: Implement `recall()`**

Port the logic from `services/memory_recall.py:build_memory_context()`. Key adaptations:

1. **Return type**: build a `RecallResponse` instead of a dict. Map the old dict keys to typed fields:
   - `relevant_entities` → `RecallResponse.entities` (as `EntitySummary` instances)
   - `entity_observations` → `RecallResponse.observations`
   - `relevant_relations` → `RecallResponse.relations`
   - `recent_episodes` → `RecallResponse.episodes`
   - `contradictions` (world_state) → `RecallResponse.contradictions`
   - The old `recalled_entity_names` and `anticipatory_context` fields are dropped — anticipatory context moves to a separate `_get_anticipatory_context()` helper that the chat service composes outside recall.

2. **Hard timeout**: wrap the whole function body in:
   ```python
   import time
   from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeout
   t0 = time.perf_counter()
   try:
       with ThreadPoolExecutor(max_workers=1) as ex:
           future = ex.submit(_recall_inner, db, tenant_id, agent_slug, query, ...)
           result = future.result(timeout=1.5)  # hard 1500ms
   except FuturesTimeout:
       return RecallResponse(metadata=RecallMetadata(
           elapsed_ms=(time.perf_counter() - t0) * 1000,
           degraded=True,
       ))
   ```
   **Caveat:** ThreadPoolExecutor + SQLAlchemy session is risky (sessions aren't thread-safe). Easier alternative: use `asyncio.wait_for` if recall becomes async, OR pass a deadline timestamp deeper into the query and check it between sub-queries. For Phase 1, the soft target (500ms) is the goal; the 1500ms hard cap is a backstop. Implement the deadline-checkpoint approach: pass a `_deadline` arg through `_query.py` helpers and check `time.perf_counter() > _deadline` between each sub-query, returning what you have so far with `degraded=True`.

3. **Token budget enforcement**: estimate tokens as `len(content) // 4`. Iterate through the result lists in priority order (commitments, contradictions, entities, observations, episodes, past_conversations) and drop the lowest-priority items until total ≤ budget. Set `truncated_for_budget=True` if anything dropped.

4. **Visibility filter**: stub for now — call `from app.memory.visibility import filter_query` and pass through. Task 11 implements it.

5. **Keyword fallback**: catch any exception from `embedding_service.embed_text()` and route to `_recall_keyword_only()` which uses `ILIKE` against entity name + observation content. Set `metadata.used_keyword_fallback=True`.

6. **Side effects (UPDATE recall_count etc)**: KEEP these. They're reads-with-side-effects today and the chat service depends on them for the recall feedback loop. We're moving them, not deleting them.

7. **RL logging**: KEEP the `log_experience(decision_point="memory_recall")` call. Don't refactor RL in this task — it's a separate concern.

Factor pgvector queries into `apps/api/app/memory/_query.py`:
```python
def search_entities(db, tenant_id, query_embedding, top_k, agent_slug, _deadline): ...
def search_observations(db, tenant_id, entity_ids, query_embedding, top_k, _deadline): ...
def search_episodes(db, tenant_id, query_embedding, top_k, _deadline): ...
def search_commitments(db, tenant_id, query_embedding, top_k, agent_slug, _deadline): ...
def search_goals(db, tenant_id, query_embedding, top_k, agent_slug, _deadline): ...
```

Each helper is ≤30 lines. The main `recall()` orchestrates.

- [ ] **Step 5: Run tests**

```bash
pytest tests/memory/test_recall.py -v -m integration
```

Expected: 4 passing.

- [ ] **Step 6: Commit**

```bash
git add apps/api/app/memory/recall.py apps/api/app/memory/_query.py apps/api/tests/memory/test_recall.py
git commit -m "feat(memory-first): port build_memory_context() to memory.recall()"
```

---

### Task 11: Visibility filter implementation

**Files:**
- Modify: `apps/api/app/memory/visibility.py`
- Modify: `apps/api/app/memory/_query.py` (apply filter to entity + commitment + memory queries)
- Create: `apps/api/tests/memory/test_visibility.py`

- [ ] **Step 1: Failing test — multi-agent scoping**

```python
# apps/api/tests/memory/test_visibility.py
import pytest
from uuid import uuid4
from app.models.knowledge_entity import KnowledgeEntity
from app.memory import recall


@pytest.mark.integration
def test_agent_scoped_entity_only_visible_to_owner(db_session_fixture, test_tenant_fixture):
    # Create an agent_scoped entity owned by sales_agent
    private = KnowledgeEntity(
        tenant_id=test_tenant_fixture.id,
        name="Sales pipeline draft Q2",
        category="note",
        owner_agent_slug="sales_agent",
        visibility="agent_scoped",
        description="Internal sales notes",
    )
    db_session_fixture.add(private)
    db_session_fixture.commit()

    # Luna recalls — should NOT see it
    luna_resp = recall(db_session_fixture, test_tenant_fixture.id, "luna", "sales pipeline")
    assert not any(e.name == "Sales pipeline draft Q2" for e in luna_resp.entities)

    # Sales agent recalls — SHOULD see it
    sales_resp = recall(db_session_fixture, test_tenant_fixture.id, "sales_agent", "sales pipeline")
    assert any(e.name == "Sales pipeline draft Q2" for e in sales_resp.entities)


@pytest.mark.integration
def test_agent_group_entity_visible_to_listed_agents(db_session_fixture, test_tenant_fixture):
    shared = KnowledgeEntity(
        tenant_id=test_tenant_fixture.id,
        name="SRE incident playbook v3",
        category="document",
        visibility="agent_group",
        visible_to=["sre_agent", "devops_agent"],
        description="Playbook",
    )
    db_session_fixture.add(shared)
    db_session_fixture.commit()

    sre = recall(db_session_fixture, test_tenant_fixture.id, "sre_agent", "incident playbook")
    devops = recall(db_session_fixture, test_tenant_fixture.id, "devops_agent", "incident playbook")
    support = recall(db_session_fixture, test_tenant_fixture.id, "support_agent", "incident playbook")

    assert any(e.name == "SRE incident playbook v3" for e in sre.entities)
    assert any(e.name == "SRE incident playbook v3" for e in devops.entities)
    assert not any(e.name == "SRE incident playbook v3" for e in support.entities)
```

- [ ] **Step 2: Run, verify fail.**

- [ ] **Step 3: Implement `visibility.py`**

```python
# apps/api/app/memory/visibility.py
"""SQL visibility filter for multi-agent scoping (design doc §7).

Applied at the query layer in memory/_query.py. Business logic does not
need to think about visibility — the memory API enforces it.
"""
from sqlalchemy import or_, and_
from sqlalchemy.orm.query import Query


def apply_visibility(query: Query, model, agent_slug: str) -> Query:
    """Filter `query` to records visible to `agent_slug`.

    A record is visible iff:
    1. visibility = 'tenant_wide' (default), OR
    2. visibility = 'agent_scoped' AND owner_agent_slug = agent_slug, OR
    3. visibility = 'agent_group' AND agent_slug IN visible_to[]
    """
    return query.filter(
        or_(
            model.visibility == "tenant_wide",
            and_(
                model.visibility == "agent_scoped",
                model.owner_agent_slug == agent_slug,
            ),
            and_(
                model.visibility == "agent_group",
                model.visible_to.any(agent_slug),  # PostgreSQL ANY operator on TEXT[]
            ),
        )
    )
```

- [ ] **Step 4: Wire into `_query.py`**

In each `search_*` helper that has a `model` parameter, call:
```python
from app.memory.visibility import apply_visibility
q = apply_visibility(q, model, agent_slug)
```

Specifically: `search_entities`, `search_commitments`, `search_goals`, `search_memories`. Episodes (`conversation_episodes`) and observations are NOT filtered — they inherit visibility from their parent entity.

- [ ] **Step 5: Run tests**

```bash
pytest tests/memory/test_visibility.py -v -m integration
```

Expected: 2 passing.

- [ ] **Step 6: Commit**

```bash
git add apps/api/app/memory/visibility.py apps/api/app/memory/_query.py apps/api/tests/memory/test_visibility.py
git commit -m "feat(memory-first): visibility filter for multi-agent scoping"
```

---

### Task 12: Cross-tenant isolation integration test

**Why:** Design doc §11.1 says "Any cross-tenant data leak detected → HARD STOP." This test is the canary.

**Files:**
- Create: `apps/api/tests/memory/test_tenant_isolation.py`

- [ ] **Step 1: Write the test**

```python
# apps/api/tests/memory/test_tenant_isolation.py
"""Cross-tenant isolation — the most important test in the file."""
import pytest
from uuid import uuid4
from app.models.tenant import Tenant
from app.models.knowledge_entity import KnowledgeEntity
from app.memory import recall


@pytest.mark.integration
def test_tenant_a_cannot_recall_tenant_b_entities(db_session_fixture):
    tenant_a = Tenant(name="Tenant A", slug=f"a-{uuid4().hex[:8]}")
    tenant_b = Tenant(name="Tenant B", slug=f"b-{uuid4().hex[:8]}")
    db_session_fixture.add_all([tenant_a, tenant_b])
    db_session_fixture.commit()

    secret = KnowledgeEntity(
        tenant_id=tenant_b.id,
        name="Tenant B Secret Project Atlas",
        category="project",
        description="Top secret",
    )
    db_session_fixture.add(secret)
    db_session_fixture.commit()

    # Tenant A queries with the EXACT name — must not see it
    resp = recall(db_session_fixture, tenant_a.id, "luna", "Atlas Secret Project")
    assert not any("Atlas" in e.name for e in resp.entities)
    assert not any("Atlas" in (o.content or "") for o in resp.observations)


@pytest.mark.integration
def test_recall_with_invalid_tenant_id_returns_empty(db_session_fixture):
    resp = recall(db_session_fixture, uuid4(), "luna", "anything")
    assert resp.entities == []
    assert resp.observations == []
```

- [ ] **Step 2: Run, verify pass (it should already pass — visibility filter is per-tenant, but this is the explicit canary).**

- [ ] **Step 3: Commit**

```bash
git add apps/api/tests/memory/test_tenant_isolation.py
git commit -m "test(memory-first): cross-tenant isolation canary"
```

---

### Task 13: Latency micro-benchmark for `recall()`

**Files:**
- Create: `apps/api/tests/memory/test_recall_latency.py`

- [ ] **Step 1: Write the benchmark**

```python
# apps/api/tests/memory/test_recall_latency.py
"""Recall latency micro-benchmark.

Soft target: p50 < 500ms on tenant 0f134606 with current data volume.
Hard target: p95 < 1500ms (the timeout).

This test is opt-in via -m latency to avoid slowing the regular test
suite. Run before merging Phase 1 to validate against the §11 SLO.
"""
import os, time, pytest
from app.memory import recall


@pytest.mark.latency
def test_recall_latency_p50(db_session_fixture, real_tenant_fixture):
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
        t0 = time.perf_counter()
        recall(db_session_fixture, real_tenant_fixture.id, "luna", q)
        latencies.append((time.perf_counter() - t0) * 1000)
    latencies.sort()
    p50 = latencies[len(latencies)//2]
    p95 = latencies[int(len(latencies)*0.95)]
    print(f"\nrecall p50={p50:.0f}ms p95={p95:.0f}ms")
    assert p50 < 500, f"p50 regressed: {p50:.0f}ms (target <500ms)"
    assert p95 < 1500, f"p95 exceeded hard timeout: {p95:.0f}ms"
```

- [ ] **Step 2: Run + commit**

```bash
pytest tests/memory/test_recall_latency.py -v -m latency
git add apps/api/tests/memory/test_recall_latency.py
git commit -m "test(memory-first): recall latency micro-benchmark"
```

---

## Phase 1.4 — `memory.record_*()`

**Goal:** Wrap the existing `knowledge.py`, `commitment_service.py`, `goal_service.py` services in a uniform memory.record_* API. Phase 1 implementation = thin delegation. Phase 2 swaps for Rust.

### Task 14: Implement `memory.record_observation/commitment/goal()`

**Files:**
- Modify: `apps/api/app/memory/record.py`
- Create: `apps/api/tests/memory/test_record.py`

- [ ] **Step 1: Failing test**

```python
# apps/api/tests/memory/test_record.py
import pytest
from datetime import datetime, timezone, timedelta
from app.memory import record_observation, record_commitment, record_goal


@pytest.mark.integration
def test_record_observation_creates_row_and_audit(
    db_session_fixture, test_tenant_fixture, sample_entity_fixture
):
    obs = record_observation(
        db_session_fixture,
        tenant_id=test_tenant_fixture.id,
        entity_id=sample_entity_fixture.id,
        content="Ray confirmed the meeting for Friday",
        confidence=0.9,
        source_type="chat",
        source_id="msg-abc",
        actor_slug="luna",
    )
    assert obs.id is not None
    # Check memory_activities audit row
    from app.models.memory_activity import MemoryActivity
    audit = db_session_fixture.query(MemoryActivity).filter_by(
        target_id=obs.id, source_type="chat"
    ).first()
    assert audit is not None
    assert audit.source_id == "msg-abc"


@pytest.mark.integration
def test_record_commitment_creates_row_and_audit(
    db_session_fixture, test_tenant_fixture
):
    c = record_commitment(
        db_session_fixture,
        tenant_id=test_tenant_fixture.id,
        owner_agent_slug="luna",
        title="Send report to Ray",
        commitment_type="delivery",
        due_at=datetime.now(timezone.utc) + timedelta(days=2),
        source_type="chat",
        source_id="msg-def",
    )
    assert c.id is not None
    assert c.state == "open"


@pytest.mark.integration
def test_record_observation_dedup_by_source_id(
    db_session_fixture, test_tenant_fixture, sample_entity_fixture
):
    """Same source_type+source_id = idempotent. Returns existing row."""
    o1 = record_observation(
        db_session_fixture, test_tenant_fixture.id,
        entity_id=sample_entity_fixture.id, content="x",
        source_type="chat", source_id="dedup-key-1", confidence=0.5,
    )
    o2 = record_observation(
        db_session_fixture, test_tenant_fixture.id,
        entity_id=sample_entity_fixture.id, content="x",
        source_type="chat", source_id="dedup-key-1", confidence=0.5,
    )
    assert o1.id == o2.id
```

- [ ] **Step 2: Run, verify fail.**

- [ ] **Step 3: Add public `upsert_entity_by_name` and `get_entity_by_name` to `knowledge.py`**

The existing `knowledge.py` has private `_find_or_create_entity(db, tenant_id, name, entity_type, category, ...)` at line 459. Add two public wrappers BEFORE implementing `record.py`:

```python
# Add to apps/api/app/services/knowledge.py, after _find_or_create_entity:

def upsert_entity_by_name(
    db: Session,
    tenant_id: uuid.UUID,
    name: str,
    *,
    entity_type: str = "general",
    category: Optional[str] = None,
    description: Optional[str] = None,
) -> tuple[KnowledgeEntity, bool]:
    """Public wrapper around _find_or_create_entity. Returns (entity, created)."""
    existing = db.query(KnowledgeEntity).filter(
        KnowledgeEntity.tenant_id == tenant_id,
        KnowledgeEntity.name == name,
    ).first()
    if existing:
        return existing, False
    entity = _find_or_create_entity(
        db, tenant_id=tenant_id, name=name,
        entity_type=entity_type, category=category or "general",
        description=description,
    )
    return entity, True


def get_entity_by_name(
    db: Session, tenant_id: uuid.UUID, name: str,
) -> Optional[KnowledgeEntity]:
    return db.query(KnowledgeEntity).filter(
        KnowledgeEntity.tenant_id == tenant_id,
        KnowledgeEntity.name == name,
    ).first()
```

- [ ] **Step 4: Implement `record.py` against the REAL service APIs**

Verified signatures (from reading the actual files):
- `commitment_service.create_commitment(db, tenant_id, commitment_in: CommitmentRecordCreate, created_by=None)` — module function, takes Pydantic schema.
- `goal_service.create_goal(db, tenant_id, goal_in: GoalCreate, created_by=None)` — same pattern.
- `knowledge.create_observation(db, tenant_id, observation_text, observation_type, source_type, ..., entity_id, confidence, ...)` — note `observation_text` not `content`.
- `MemoryActivity` columns: `event_type`, `description` (NOT NULL), `source`, `event_metadata` (JSON, mapped to `metadata`), `entity_id`, `memory_id`, `workflow_id` (added in Task 9), `workflow_run_id`, `agent_id`, `user_id`. NO `target_table`/`target_id`/`actor_slug`/`source_id` columns — those go in `event_metadata`.

```python
# apps/api/app/memory/record.py
"""Sync memory writes — small, fast, request-thread.

Phase 1: thin wrappers over existing services (knowledge, commitment_service,
goal_service) that ALSO write to memory_activities for audit traceability.
Phase 2: Rust memory-core gRPC service replaces the wrappers.
"""
from datetime import datetime, timezone
from typing import Optional
from uuid import UUID
from sqlalchemy.orm import Session
from sqlalchemy import text as sql_text

from app.services import knowledge as knowledge_service
from app.services import commitment_service, goal_service
from app.schemas.commitment_record import CommitmentRecordCreate
from app.schemas.goal_record import GoalCreate  # verify exact schema name in apps/api/app/schemas/goal_record.py
from app.models.knowledge_observation import KnowledgeObservation
from app.models.commitment_record import CommitmentRecord
from app.models.goal_record import GoalRecord
from app.models.memory_activity import MemoryActivity


def _audit(
    db: Session, *,
    tenant_id: UUID,
    event_type: str,
    description: str,
    target_table: str,  # logical table name — stored in metadata
    target_id: UUID,
    source_type: Optional[str] = None,
    source_id: Optional[str] = None,
    actor_slug: Optional[str] = None,
    workflow_id: Optional[str] = None,
    workflow_run_id: Optional[str] = None,
    entity_id: Optional[UUID] = None,
    memory_id: Optional[UUID] = None,
):
    """Write a MemoryActivity audit row using ONLY columns that exist."""
    db.add(MemoryActivity(
        tenant_id=tenant_id,
        event_type=event_type,
        description=description,
        source=source_type,  # the existing column is just `source`
        event_metadata={
            "target_table": target_table,
            "target_id": str(target_id),
            "source_id": source_id,
            "actor_slug": actor_slug,
        },
        workflow_id=workflow_id,
        workflow_run_id=workflow_run_id,
        entity_id=entity_id,
        memory_id=memory_id,
        created_at=datetime.utcnow(),
    ))


def _find_existing_by_source(
    db: Session, tenant_id: UUID, target_table: str,
    source_type: str, source_id: str,
):
    """Look up an existing memory_activities row for dedup. Returns the
    target_id if found, else None."""
    row = db.execute(sql_text("""
        SELECT (metadata->>'target_id') AS target_id
        FROM memory_activities
        WHERE tenant_id = :t
          AND metadata->>'source_type' = :st
          AND metadata->>'source_id' = :sid
          AND metadata->>'target_table' = :tt
        ORDER BY created_at DESC LIMIT 1
    """), {"t": str(tenant_id), "st": source_type, "sid": source_id, "tt": target_table}).first()
    return row.target_id if row else None


def record_observation(
    db: Session, tenant_id: UUID, *,
    entity_id: UUID, content: str, confidence: float = 0.7,
    source_type: Optional[str] = None, source_id: Optional[str] = None,
    actor_slug: Optional[str] = None, workflow_id: Optional[str] = None,
) -> KnowledgeObservation:
    # Dedup by source_type + source_id (stored in metadata)
    if source_type and source_id:
        existing_id = _find_existing_by_source(
            db, tenant_id, "knowledge_observations", source_type, source_id
        )
        if existing_id:
            existing = db.get(KnowledgeObservation, UUID(existing_id))
            if existing:
                return existing

    obs = knowledge_service.create_observation(
        db, tenant_id=tenant_id,
        observation_text=content,
        observation_type="fact",
        source_type=source_type or "memory_record",
        entity_id=entity_id,
        confidence=confidence,
    )
    _audit(db, tenant_id=tenant_id,
           event_type="observation_created",
           description=f"Observation on entity {entity_id}: {content[:80]}",
           target_table="knowledge_observations", target_id=obs.id,
           source_type=source_type, source_id=source_id,
           actor_slug=actor_slug, workflow_id=workflow_id,
           entity_id=entity_id)
    db.commit()
    return obs


def record_commitment(
    db: Session, tenant_id: UUID, *,
    owner_agent_slug: str, title: str, description: Optional[str] = None,
    commitment_type: str = "action", due_at: Optional[datetime] = None,
    source_type: Optional[str] = None, source_id: Optional[str] = None,
    workflow_id: Optional[str] = None,
) -> CommitmentRecord:
    if source_type and source_id:
        existing_id = _find_existing_by_source(
            db, tenant_id, "commitment_records", source_type, source_id
        )
        if existing_id:
            existing = db.get(CommitmentRecord, UUID(existing_id))
            if existing:
                return existing

    # Build the Pydantic schema the service expects.
    # NOTE: read apps/api/app/schemas/commitment_record.py to confirm the exact
    # field names + enum values for commitment_type, priority, source_type.
    commitment_in = CommitmentRecordCreate(
        owner_agent_slug=owner_agent_slug,
        title=title,
        description=description,
        commitment_type=commitment_type,  # may need .value or enum lookup
        due_at=due_at,
        source_type="tool_call",  # CommitmentSourceType enum value
        source_ref={"memory_source_type": source_type, "memory_source_id": source_id} if source_type else {},
    )
    c = commitment_service.create_commitment(db, tenant_id=tenant_id, commitment_in=commitment_in)

    _audit(db, tenant_id=tenant_id,
           event_type="commitment_created",
           description=f"Commitment: {title[:80]}",
           target_table="commitment_records", target_id=c.id,
           source_type=source_type, source_id=source_id,
           actor_slug=owner_agent_slug, workflow_id=workflow_id)
    db.commit()
    return c


def record_goal(
    db: Session, tenant_id: UUID, *,
    owner_agent_slug: str, title: str,
    source_type: Optional[str] = None, source_id: Optional[str] = None,
    **kwargs,
) -> GoalRecord:
    # NOTE: read apps/api/app/schemas/goal_record.py for the actual Create schema name.
    goal_in = GoalCreate(owner_agent_slug=owner_agent_slug, title=title, **kwargs)
    g = goal_service.create_goal(db, tenant_id=tenant_id, goal_in=goal_in)
    _audit(db, tenant_id=tenant_id,
           event_type="goal_created",
           description=f"Goal: {title[:80]}",
           target_table="goal_records", target_id=g.id,
           source_type=source_type, source_id=source_id,
           actor_slug=owner_agent_slug)
    db.commit()
    return g
```

**Pre-implementation reading list (mandatory):**
1. `apps/api/app/schemas/commitment_record.py` — confirm `CommitmentRecordCreate` field names and enum values for `commitment_type`, `priority`, `source_type`. The plan code uses `commitment_type=commitment_type` as a raw string; if the schema expects an enum instance, wrap with the enum class.
2. `apps/api/app/schemas/goal_record.py` — confirm the exact Create-schema class name (`GoalCreate` vs `GoalRecordCreate`).
3. `apps/api/app/services/knowledge.py:535` — confirm `create_observation` keyword args.

- [ ] **Step 4: Run tests + commit**

```bash
pytest tests/memory/test_record.py -v -m integration
git add apps/api/app/memory/record.py apps/api/tests/memory/test_record.py
git commit -m "feat(memory-first): record_observation/commitment/goal with audit"
```

---

### Task 15: Implement `memory.ingest_events()` for bulk writes

**Files:**
- Modify: `apps/api/app/memory/ingest.py`
- Create: `apps/api/tests/memory/test_ingest.py`

- [ ] **Step 1: Failing test**

```python
# apps/api/tests/memory/test_ingest.py
import pytest
from datetime import datetime, timezone
from uuid import uuid4
from app.memory import ingest_events
from app.memory.types import MemoryEvent
from app.memory.adapters.registry import register_adapter


@pytest.fixture(autouse=True)
def register_test_adapter():
    """Tests need a registered 'chat' adapter to validate ingest_events."""
    class ChatTestAdapter:
        source_type = "chat"
        async def ingest(self, raw, source_metadata, tenant_id): return []
        def deduplication_key(self, raw): return f"chat:{raw}"
    register_adapter(ChatTestAdapter())


@pytest.mark.integration
def test_ingest_events_writes_proposed_entities_and_observations(
    db_session_fixture, test_tenant_fixture
):
    ev = MemoryEvent(
        tenant_id=test_tenant_fixture.id,
        source_type="chat",
        source_id="msg-ingest-test-1",
        actor_slug="luna",
        occurred_at=datetime.now(timezone.utc),
        ingested_at=datetime.now(timezone.utc),
        kind="text",
        text="Ray confirmed the Friday meeting",
        proposed_entities=[{"name": "Ray", "category": "person"}],
        proposed_observations=[{"entity_name": "Ray", "content": "confirmed Friday meeting"}],
    )
    result = ingest_events(db_session_fixture, test_tenant_fixture.id, [ev], workflow_id="test-wf-1")
    assert result.events_processed == 1
    assert result.entities_created >= 1
    assert result.observations_created >= 1


@pytest.mark.integration
def test_ingest_events_unknown_source_type_raises(db_session_fixture, test_tenant_fixture):
    ev = MemoryEvent(
        tenant_id=test_tenant_fixture.id,
        source_type="nonexistent_xyz",
        source_id="x",
        occurred_at=datetime.now(timezone.utc),
        ingested_at=datetime.now(timezone.utc),
        kind="text",
        text="hi",
    )
    with pytest.raises(KeyError):
        ingest_events(db_session_fixture, test_tenant_fixture.id, [ev])
```

- [ ] **Step 2: Implement `ingest.py`**

```python
# apps/api/app/memory/ingest.py
"""Bulk ingestion entry point.

Receives MemoryEvents from source adapters (or directly from workflows like
PostChatMemoryWorkflow). For each event:
  1. Validate source_type via adapter registry (fail-fast on unknown).
  2. Resolve or create entities listed in proposed_entities (dedup by name+tenant).
  3. Insert observations linked to those entities.
  4. Insert relations between entities.
  5. Insert commitments via record_commitment().
  6. Audit each write to memory_activities with workflow_id.
"""
from dataclasses import dataclass
from typing import Optional
from uuid import UUID
from sqlalchemy.orm import Session

from app.memory.types import MemoryEvent
from app.memory.adapters.registry import get_adapter
from app.memory.record import record_observation, record_commitment
from app.services import knowledge as knowledge_service


@dataclass
class IngestResult:
    events_processed: int = 0
    entities_created: int = 0
    entities_reused: int = 0
    observations_created: int = 0
    relations_created: int = 0
    commitments_created: int = 0
    skipped: int = 0
    errors: list[str] = None

    def __post_init__(self):
        if self.errors is None:
            self.errors = []


def ingest_events(
    db: Session,
    tenant_id: UUID,
    events: list[MemoryEvent],
    workflow_id: Optional[str] = None,
) -> IngestResult:
    result = IngestResult()
    for ev in events:
        # Validate source_type — KeyError if unknown
        get_adapter(ev.source_type)

        try:
            for prop in ev.proposed_entities:
                ent, created = knowledge_service.upsert_entity_by_name(
                    db, tenant_id=tenant_id,
                    name=prop["name"],
                    category=prop.get("category"),
                    description=prop.get("description"),
                )
                if created:
                    result.entities_created += 1
                else:
                    result.entities_reused += 1

            entity_lookup = {
                e["name"]: knowledge_service.get_entity_by_name(db, tenant_id, e["name"])
                for e in ev.proposed_entities
            }

            for obs_dict in ev.proposed_observations:
                ent = entity_lookup.get(obs_dict["entity_name"])
                if not ent:
                    result.skipped += 1
                    continue
                record_observation(
                    db, tenant_id=tenant_id,
                    entity_id=ent.id,
                    content=obs_dict["content"],
                    confidence=obs_dict.get("confidence", ev.confidence),
                    source_type=ev.source_type,
                    source_id=ev.source_id,
                    actor_slug=ev.actor_slug,
                    workflow_id=workflow_id,
                )
                result.observations_created += 1

            for c_dict in ev.proposed_commitments:
                record_commitment(
                    db, tenant_id=tenant_id,
                    owner_agent_slug=ev.actor_slug or "system",
                    title=c_dict["title"],
                    commitment_type=c_dict.get("type", "action"),
                    due_at=c_dict.get("due_at"),
                    source_type=ev.source_type,
                    source_id=ev.source_id,
                    workflow_id=workflow_id,
                )
                result.commitments_created += 1

            # TODO Phase 1.6: relations support
            result.events_processed += 1
        except Exception as e:
            result.errors.append(f"event {ev.source_id}: {e}")
            db.rollback()

    return result
```

**Note:** This task assumes `knowledge_service.upsert_entity_by_name` and `knowledge_service.get_entity_by_name` exist. **READ `apps/api/app/services/knowledge.py` first.** If they don't exist, add them as small helper functions on knowledge_service in the SAME commit (don't fan out into separate refactors).

- [ ] **Step 3: Run tests + commit**

```bash
pytest tests/memory/test_ingest.py -v -m integration
git add apps/api/app/memory/ingest.py apps/api/tests/memory/test_ingest.py
git commit -m "feat(memory-first): ingest_events bulk write entry point"
```

---

## Phase 1.5 — Commitment classifier

**Goal:** Replace the disabled regex `commitment_extractor.py` with a Gemma4 LLM classifier. Phase 0 Task 2 produced the gold set; this phase builds the classifier and proves it hits F1 ≥ 0.7.

**Acceptance gate:** §11.1 anti-success criterion #3 — if F1 < 0.7, do NOT enable the classifier in PostChatMemoryWorkflow. Keep the no-op stub deletion gated on this number.

### Task 16: Build the Gemma4 commitment classifier

**Files:**
- Create: `apps/api/app/memory/classifiers/__init__.py`
- Create: `apps/api/app/memory/classifiers/commitment.py`
- Create: `apps/api/tests/memory/classifiers/test_commitment.py`

- [ ] **Step 1: Failing test on a known-positive and known-negative example**

```python
# apps/api/tests/memory/classifiers/test_commitment.py
import pytest
from app.memory.classifiers.commitment import classify_commitment


@pytest.mark.integration  # requires Ollama running
def test_classifies_obvious_commitment():
    result = classify_commitment(
        "I'll send you the report by Friday at 5pm",
        role="user",
    )
    assert result.is_commitment is True
    assert result.title  # non-empty
    assert result.type in {"action", "delivery", "response", "meeting"}


@pytest.mark.integration
def test_classifies_obvious_non_commitment():
    result = classify_commitment(
        "Ray usually sends his reports on Fridays",
        role="user",
    )
    assert result.is_commitment is False


@pytest.mark.integration
def test_classifies_meta_discussion_as_non_commitment():
    """Critical: don't extract commitments from descriptions of the feature itself.
    This was the bug that made commitment_extractor.py a no-op."""
    result = classify_commitment(
        "Gap 3 is about commitment tracking and stakes management",
        role="assistant",
    )
    assert result.is_commitment is False
```

- [ ] **Step 2: Implement `commitment.py`**

```python
# apps/api/app/memory/classifiers/commitment.py
"""Gemma4-based commitment classifier.

Replaces the regex-based commitment_extractor.py (which was disabled
because regex couldn't distinguish "I'll send the report" from
"the report-sending feature"). Uses structured output to force a
JSON response and parse it deterministically.
"""
import json
import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Optional, Literal

from app.services.local_inference import generate_sync, QUALITY_MODEL

logger = logging.getLogger(__name__)


@dataclass
class CommitmentClassification:
    is_commitment: bool
    title: Optional[str] = None
    due_at: Optional[datetime] = None
    type: Optional[Literal["action", "delivery", "response", "meeting"]] = None
    confidence: float = 0.0
    raw_response: Optional[str] = None


SYSTEM_PROMPT = """You are a binary classifier. Decide whether a single chat message contains a COMMITMENT — a statement where the speaker (user or assistant) commits THEMSELVES OR SOMEONE ELSE to a future action, with an explicit or implicit deadline.

A commitment is:
- "I'll send the report Friday" — first-person, dated
- "Luna, follow up with Ray tomorrow" — directive
- "We need to ship before the freeze" — first-person plural, dated
- "Confirmed for 3pm Thursday" — meeting confirmation

NOT a commitment:
- "Ray usually sends reports on Fridays" — third-person description
- "The commitment-tracking feature has 47 records" — meta/data
- "What if we shipped on Friday?" — question
- "I sent the report yesterday" — past tense
- "I'm thinking about reviewing the PR" — intent without commitment
- "Maybe tomorrow" — hedged

Respond with JSON only:
{"is_commitment": true|false, "title": "<short title or null>", "due_at_iso": "<ISO datetime or null>", "type": "action|delivery|response|meeting|null", "confidence": 0.0-1.0}"""


def classify_commitment(text: str, role: str = "user") -> CommitmentClassification:
    """Run Gemma4 against a single message. Returns a parsed classification."""
    user_prompt = f"[role={role}] {text}"
    try:
        raw = generate_sync(
            prompt=user_prompt,
            model=QUALITY_MODEL,  # gemma4 by default
            system=SYSTEM_PROMPT,
            temperature=0.0,
            max_tokens=200,
            timeout=20.0,
            response_format="json",  # see local_inference.py — pass through to Ollama format=json
        )
    except Exception as e:
        logger.warning("classify_commitment ollama failure: %s", e)
        return CommitmentClassification(is_commitment=False, confidence=0.0)

    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        logger.warning("classify_commitment got non-JSON: %r", raw[:200])
        return CommitmentClassification(is_commitment=False, confidence=0.0, raw_response=raw)

    due_at = None
    if parsed.get("due_at_iso"):
        try:
            due_at = datetime.fromisoformat(parsed["due_at_iso"].replace("Z", "+00:00"))
        except ValueError:
            pass

    return CommitmentClassification(
        is_commitment=bool(parsed.get("is_commitment", False)),
        title=parsed.get("title"),
        due_at=due_at,
        type=parsed.get("type") if parsed.get("type") not in (None, "null") else None,
        confidence=float(parsed.get("confidence", 0.5)),
        raw_response=raw,
    )
```

**Note:** Verify `generate_sync` accepts a `response_format="json"` kwarg. If not, add a `format` parameter that gets passed to the underlying Ollama HTTP call (it accepts `format="json"` natively). Inspect `local_inference.py:generate_sync` (around line 274) before implementing.

- [ ] **Step 3: Run + commit**

```bash
pytest tests/memory/classifiers/test_commitment.py -v -m integration
git add apps/api/app/memory/classifiers apps/api/tests/memory/classifiers
git commit -m "feat(memory-first): Gemma4 commitment classifier"
```

---

### Task 17: F1 evaluation harness against gold set

**Files:**
- Create: `apps/api/scripts/evaluate_commitment_classifier.py`
- Create: `docs/plans/baselines/commitment-classifier-f1.md` (results)

- [ ] **Step 1: Write the evaluator**

```python
# apps/api/scripts/evaluate_commitment_classifier.py
"""Run the Gemma4 commitment classifier against the gold set, report F1.

Acceptance gate (design doc §11.1 / open Q #2): F1 ≥ 0.7 to ship the classifier.
"""
import json, sys, time
from dataclasses import asdict
from app.memory.classifiers.commitment import classify_commitment


def main():
    gold = []
    with open("apps/api/tests/fixtures/commitment_gold_set.jsonl") as f:
        for line in f:
            gold.append(json.loads(line))

    print(f"Loaded {len(gold)} gold examples")

    tp = fp = tn = fn = 0
    misclassified = []
    t0 = time.perf_counter()
    for i, ex in enumerate(gold):
        result = classify_commitment(ex["text"], role=ex.get("role", "user"))
        pred = 1 if result.is_commitment else 0
        actual = ex["label"]
        if pred == 1 and actual == 1: tp += 1
        elif pred == 1 and actual == 0: fp += 1
        elif pred == 0 and actual == 0: tn += 1
        elif pred == 0 and actual == 1: fn += 1

        if pred != actual:
            misclassified.append({
                "text": ex["text"][:100],
                "actual": actual,
                "predicted": pred,
                "confidence": result.confidence,
            })
        if (i + 1) % 20 == 0:
            print(f"  {i+1}/{len(gold)} processed")

    precision = tp / (tp + fp) if (tp + fp) else 0.0
    recall = tp / (tp + fn) if (tp + fn) else 0.0
    f1 = 2 * precision * recall / (precision + recall) if (precision + recall) else 0.0
    elapsed = time.perf_counter() - t0

    report = {
        "n": len(gold),
        "tp": tp, "fp": fp, "tn": tn, "fn": fn,
        "precision": round(precision, 3),
        "recall": round(recall, 3),
        "f1": round(f1, 3),
        "elapsed_seconds": round(elapsed, 1),
        "elapsed_per_example_ms": round(elapsed / len(gold) * 1000, 0),
        "misclassified_count": len(misclassified),
    }
    print("\n" + json.dumps(report, indent=2))

    with open("docs/plans/baselines/commitment-classifier-f1.md", "w") as f:
        f.write("# Commitment Classifier F1 Evaluation\n\n")
        f.write(f"**Date:** {time.strftime('%Y-%m-%d')}\n\n")
        f.write(f"**Result:** F1 = {f1:.3f}\n\n")
        f.write(f"**Acceptance:** {'✅ PASS' if f1 >= 0.7 else '❌ FAIL'} (target ≥ 0.7)\n\n")
        f.write("## Metrics\n```json\n" + json.dumps(report, indent=2) + "\n```\n\n")
        f.write("## Sample misclassifications (first 20)\n")
        for m in misclassified[:20]:
            f.write(f"- `{m['text']}` actual={m['actual']} predicted={m['predicted']} conf={m['confidence']:.2f}\n")

    sys.exit(0 if f1 >= 0.7 else 1)


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Run it**

```bash
docker-compose exec -T api python scripts/evaluate_commitment_classifier.py
```

Expected: prints F1 score. **If F1 ≥ 0.7, proceed. If < 0.7, STOP and surface to user — do NOT proceed past Task 19.**

- [ ] **Step 3: Commit the result file**

```bash
git add apps/api/scripts/evaluate_commitment_classifier.py docs/plans/baselines/commitment-classifier-f1.md
git commit -m "feat(memory-first): commitment classifier F1 evaluation harness + baseline"
```

---

### Task 18: Iterate on prompt if F1 < 0.85

**Conditional task** — only execute if Task 17 returned F1 ∈ [0.7, 0.85).

**Files:**
- Modify: `apps/api/app/memory/classifiers/commitment.py` (refine SYSTEM_PROMPT)

- [ ] **Step 1: Review the misclassified examples in the F1 report file**

Look for patterns:
- All FPs are third-person descriptions? → strengthen the "NOT a commitment" examples in the prompt
- All FNs are Spanish? → add more Spanish positive examples
- All FPs are meta-discussion? → add explicit anti-pattern

- [ ] **Step 2: Adjust the prompt and re-run evaluation**

Update SYSTEM_PROMPT in `commitment.py`. Re-run:
```bash
docker-compose exec -T api python scripts/evaluate_commitment_classifier.py
```

Cap iterations at **3 prompt revisions**. If F1 still < 0.85 after 3 attempts, the design doc target was 0.7 minimum and 0.85 ideal — accept anything ≥ 0.7 and move on.

- [ ] **Step 3: Commit each iteration separately**

```bash
git add apps/api/app/memory/classifiers/commitment.py docs/plans/baselines/commitment-classifier-f1.md
git commit -m "tune(memory-first): commitment classifier prompt revision N"
```

---

### Task 19: Decision gate — proceed or STOP

**Files:**
- Modify: `docs/plans/2026-04-07-memory-first-phase-1-plan.md` (this file — add a gate marker)

- [ ] **Step 1: Read `docs/plans/baselines/commitment-classifier-f1.md`**

- [ ] **Step 2: Make the decision**

| F1 Result | Action |
|---|---|
| F1 ≥ 0.85 | ✅ Proceed to Task 20. Mark gate PASS in this plan file. |
| 0.7 ≤ F1 < 0.85 | ✅ Proceed to Task 20 with caveat — log to `docs/plans/baselines/`. |
| F1 < 0.7 | ❌ STOP. Surface to user. Do not start Task 20. The Phase 1 commitment-detection deliverable is BLOCKED until either the gold set is improved or the model is changed. |

- [ ] **Step 3: If proceeding, commit a marker**

```bash
# Append a line to this plan file under Task 19:
# "Gate result: F1=0.XX on YYYY-MM-DD. Proceed=YES."
git add docs/plans/2026-04-07-memory-first-phase-1-plan.md
git commit -m "gate(memory-first): commitment classifier F1 decision recorded"
```

---

## Phase 1.6 — Memory workflows

**Goal:** Move all post-chat side effects out of the daemon threads at `chat.py:501-715` into Temporal workflows. This is the biggest reliability win in Phase 1 — failures become retriable, observable, and survive process restarts.

**Pattern:** Model on `Gap1JournalSynthesis` workflow (already in repo). One main workflow (`PostChatMemoryWorkflow`) with N independent activities. Each activity manages its own DB session via `SessionLocal`. Failures in one activity do NOT block the others.

**Queue:** All workflows run on the existing `servicetsunami-orchestration` queue. We do NOT create a new `servicetsunami-memory` queue in Phase 1 — that's Phase 3a when we have K8s. Phase 1 reuses the existing orchestration worker.

### Task 20: `PostChatMemoryWorkflow` skeleton + dispatch from chat hot path

**Files:**
- Create: `apps/api/app/workflows/post_chat_memory.py`
- Create: `apps/api/app/workflows/activities/post_chat_memory_activities.py`
- Modify: `apps/api/app/workers/orchestration_worker.py` (register new workflow + activities)
- Create: `apps/api/tests/workflows/test_post_chat_memory.py`

- [ ] **Step 1: Failing test for the workflow signature**

```python
# apps/api/tests/workflows/test_post_chat_memory.py
import pytest
from temporalio.testing import WorkflowEnvironment
from temporalio.client import Client
from temporalio.worker import Worker
from app.workflows.post_chat_memory import PostChatMemoryWorkflow
from app.workflows.activities import post_chat_memory_activities as acts


@pytest.mark.asyncio
async def test_post_chat_memory_skeleton_runs():
    async with await WorkflowEnvironment.start_time_skipping() as env:
        async with Worker(
            env.client, task_queue="test-pcm",
            workflows=[PostChatMemoryWorkflow],
            activities=[
                acts.extract_knowledge,
                acts.detect_commitment,
                acts.update_world_state,
                acts.update_behavioral_signals,
                acts.maybe_trigger_episode,
            ],
        ):
            result = await env.client.execute_workflow(
                PostChatMemoryWorkflow.run,
                args=["00000000-0000-0000-0000-000000000000",
                      "00000000-0000-0000-0000-000000000001",
                      "00000000-0000-0000-0000-000000000002",
                      "00000000-0000-0000-0000-000000000003"],
                id="test-pcm-1",
                task_queue="test-pcm",
            )
            assert result["activities_run"] == 5
            # All activities are no-ops in this test (real DB not connected),
            # but the workflow must complete without exceptions.
```

- [ ] **Step 2: Implement the workflow skeleton**

```python
# apps/api/app/workflows/post_chat_memory.py
"""PostChatMemoryWorkflow — fires async after every chat turn.

Replaces the daemon-thread side effects at apps/api/app/services/chat.py:501-715.
Each activity is independent and retriable. One activity's failure does NOT
block the others. Bounded by 60-second per-activity timeout.
"""
from datetime import timedelta
from temporalio import workflow
from temporalio.common import RetryPolicy

with workflow.unsafe.imports_passed_through():
    from app.workflows.activities.post_chat_memory_activities import (
        extract_knowledge,
        detect_commitment,
        update_world_state,
        update_behavioral_signals,
        maybe_trigger_episode,
    )


_RETRY = RetryPolicy(
    initial_interval=timedelta(seconds=2),
    maximum_interval=timedelta(seconds=30),
    maximum_attempts=3,
    backoff_coefficient=2.0,
)
_TIMEOUT = timedelta(seconds=60)


@workflow.defn
class PostChatMemoryWorkflow:
    """Activities run sequentially for session/tenant isolation. Each activity
    return value is captured in `results["activity_results"]` so the parent can
    act on it (Task 25 uses this to dispatch EpisodeWorkflow as a child)."""

    @workflow.run
    async def run(
        self,
        tenant_id: str,
        chat_session_id: str,
        user_message_id: str,
        assistant_message_id: str,
    ) -> dict:
        results: dict = {"activities_run": 0, "errors": [], "activity_results": {}}
        args = [tenant_id, chat_session_id, user_message_id, assistant_message_id]

        async def _safe(activity, name):
            try:
                ret = await workflow.execute_activity(
                    activity, args=args,
                    start_to_close_timeout=_TIMEOUT,
                    retry_policy=_RETRY,
                )
                results["activities_run"] += 1
                results["activity_results"][name] = ret
                return ret
            except Exception as e:
                results["errors"].append(f"{name}: {e}")
                return None

        # Independent activities — could be parallel, but Temporal Python
        # ergonomics for parallel-with-isolation are simpler in sequence.
        # Total budget: ~5 * 60s = 5 min worst case. Acceptable for an
        # async post-chat workflow.
        await _safe(extract_knowledge, "extract_knowledge")
        await _safe(detect_commitment, "detect_commitment")
        await _safe(update_world_state, "update_world_state")
        await _safe(update_behavioral_signals, "update_behavioral_signals")
        await _safe(maybe_trigger_episode, "maybe_trigger_episode")

        return results
```

- [ ] **Step 3: Implement activity stubs (real logic in tasks 21-25)**

```python
# apps/api/app/workflows/activities/post_chat_memory_activities.py
"""Activities for PostChatMemoryWorkflow.

Each activity manages its own SessionLocal. Real implementations land
in tasks 21-25. This file ships with no-op stubs first so the workflow
skeleton compiles + tests pass.
"""
import logging
from temporalio import activity

logger = logging.getLogger(__name__)


@activity.defn
async def extract_knowledge(
    tenant_id: str, chat_session_id: str,
    user_message_id: str, assistant_message_id: str,
) -> dict:
    """Stub. Real impl in Task 21."""
    logger.info("extract_knowledge stub called")
    return {"extracted": 0}


@activity.defn
async def detect_commitment(
    tenant_id: str, chat_session_id: str,
    user_message_id: str, assistant_message_id: str,
) -> dict:
    """Stub. Real impl in Task 22."""
    return {"detected": False}


@activity.defn
async def update_world_state(
    tenant_id: str, chat_session_id: str,
    user_message_id: str, assistant_message_id: str,
) -> dict:
    """Stub. Real impl in Task 23."""
    return {"updated": 0}


@activity.defn
async def update_behavioral_signals(
    tenant_id: str, chat_session_id: str,
    user_message_id: str, assistant_message_id: str,
) -> dict:
    """Stub. Real impl in Task 24."""
    return {"signals": 0}


@activity.defn
async def maybe_trigger_episode(
    tenant_id: str, chat_session_id: str,
    user_message_id: str, assistant_message_id: str,
) -> dict:
    """Stub. Real impl in Task 25."""
    return {"triggered": False}
```

- [ ] **Step 4: Register in `orchestration_worker.py`**

Add to the worker's workflows + activities lists. Read the existing file first to find the right pattern (probably an `if __name__ == "__main__"` block that calls `Worker(...)` with all workflows).

- [ ] **Step 5: Run tests + commit**

```bash
pytest tests/workflows/test_post_chat_memory.py -v
git add apps/api/app/workflows/post_chat_memory.py \
        apps/api/app/workflows/activities/post_chat_memory_activities.py \
        apps/api/app/workers/orchestration_worker.py \
        apps/api/tests/workflows/test_post_chat_memory.py
git commit -m "feat(memory-first): PostChatMemoryWorkflow skeleton + activity stubs"
```

---

### Task 21: Activity — `extract_knowledge` (move from daemon thread)

**Files:**
- Modify: `apps/api/app/workflows/activities/post_chat_memory_activities.py`
- Reference (READ ONLY): `apps/api/app/services/chat.py:503-529` (current daemon thread implementation)

- [ ] **Step 1: Read the existing implementation in chat.py**

Find the daemon thread block that calls `KnowledgeExtractionService.extract_from_content()`. Note:
- What inputs it builds (user message + assistant response, joined)
- What it does with the extracted entities/observations
- How it handles errors today (probably a try/except that swallows)

- [ ] **Step 2: Implement the activity**

Verified signature from `apps/api/app/services/knowledge_extraction.py:120`:
```
extract_from_content(self, db, tenant_id, content,
    content_type='plain_text', *, entity_schema=None, source_url=None,
    source_agent_id=None, collection_task_id=None, activity_source='chat')
```

There is NO `source_id` kwarg. The plan must use `activity_source='chat'` and pass the message id via the `content_type` or post-process. For dedup, the audit row in `_audit()` (Task 14's `record.py`) is the source-of-truth, not the extraction call itself.

```python
@activity.defn
async def extract_knowledge(
    tenant_id: str, chat_session_id: str,
    user_message_id: str, assistant_message_id: str,
) -> dict:
    from uuid import UUID
    from app.db.session import SessionLocal
    from app.models.chat import ChatMessage
    from app.services.knowledge_extraction import KnowledgeExtractionService

    db = SessionLocal()
    try:
        user_msg = db.get(ChatMessage, UUID(user_message_id))
        asst_msg = db.get(ChatMessage, UUID(assistant_message_id))
        if not user_msg or not asst_msg:
            return {"extracted": 0, "skipped": "messages not found"}

        content = f"USER: {user_msg.content}\nASSISTANT: {asst_msg.content}"
        svc = KnowledgeExtractionService()
        result = svc.extract_from_content(
            db,
            tenant_id=UUID(tenant_id),
            content=content,
            content_type='plain_text',
            activity_source='chat',
        )
        db.commit()
        # result shape: read knowledge_extraction.py to confirm. Often returns
        # a dict or dataclass with .entities and .observations counts.
        entities = getattr(result, "entities", None) or []
        observations = getattr(result, "observations", None) or []
        return {
            "extracted": len(entities),
            "observations": len(observations),
        }
    finally:
        db.close()
```

**Note:** the existing daemon-thread implementation in `chat.py:497-529` is the most reliable reference for the actual call shape and the result handling — port THAT, don't reinvent.

- [ ] **Step 3: Test against a real chat session**

Add a test variant that requires a real DB:
```python
@pytest.mark.integration
async def test_extract_knowledge_against_real_messages(...):
    # Insert two ChatMessage rows, run the activity, assert entities created
```

- [ ] **Step 4: Run + commit**

```bash
pytest tests/workflows/test_post_chat_memory.py -v
git add apps/api/app/workflows/activities/post_chat_memory_activities.py apps/api/tests/workflows/test_post_chat_memory.py
git commit -m "feat(memory-first): extract_knowledge activity (moved from daemon thread)"
```

---

### Task 22: Activity — `detect_commitment` (uses Gemma4 classifier)

**Files:**
- Modify: `apps/api/app/workflows/activities/post_chat_memory_activities.py`

- [ ] **Step 1: Implement**

```python
@activity.defn
async def detect_commitment(
    tenant_id: str, chat_session_id: str,
    user_message_id: str, assistant_message_id: str,
) -> dict:
    from uuid import UUID
    from app.db.session import SessionLocal
    from app.models.chat import ChatMessage
    from app.memory.classifiers.commitment import classify_commitment
    from app.memory import record_commitment

    db = SessionLocal()
    try:
        user_msg = db.get(ChatMessage, UUID(user_message_id))
        asst_msg = db.get(ChatMessage, UUID(assistant_message_id))

        detections = []
        for msg in (user_msg, asst_msg):
            if not msg:
                continue
            cls = classify_commitment(msg.content, role=msg.role)
            if not cls.is_commitment:
                continue
            owner = "luna" if msg.role == "assistant" else "user"
            c = record_commitment(
                db, tenant_id=UUID(tenant_id),
                owner_agent_slug=owner,
                title=cls.title or msg.content[:80],
                commitment_type=cls.type or "action",
                due_at=cls.due_at,
                source_type="chat",
                source_id=str(msg.id),
            )
            detections.append(str(c.id))
        return {"detected": len(detections), "commitment_ids": detections}
    finally:
        db.close()
```

- [ ] **Step 2: Integration test**

```python
@pytest.mark.integration
async def test_detect_commitment_creates_commitment_row(...):
    # Insert ChatMessage with explicit commitment text, run activity,
    # assert CommitmentRecord row created with correct title
```

- [ ] **Step 3: Run + commit**

```bash
git add apps/api/app/workflows/activities/post_chat_memory_activities.py apps/api/tests/workflows/test_post_chat_memory.py
git commit -m "feat(memory-first): detect_commitment activity using Gemma4 classifier"
```

---

### Task 23: Activity — `update_world_state` (handles contradictions)

**Files:**
- Modify: `apps/api/app/workflows/activities/post_chat_memory_activities.py`
- Reference: `apps/api/app/services/world_state_service.py` (if it exists; read first)

- [ ] **Step 1: Implement**

```python
@activity.defn
async def update_world_state(
    tenant_id: str, chat_session_id: str,
    user_message_id: str, assistant_message_id: str,
) -> dict:
    """For each new observation created in this turn, check against
    existing world_state_assertions. If conflict: create dispute. If
    corroboration: increment corroboration_count + update confidence.
    If novel: create new assertion."""
    from uuid import UUID
    from app.db.session import SessionLocal
    # Defer to existing world state service if present, else inline minimal logic.
    db = SessionLocal()
    try:
        # READ: app/services/world_state_service.py for the actual API.
        # If no service exists, this activity is a no-op for Phase 1
        # and gets implemented properly in Phase 2.
        return {"updated": 0, "disputes": 0, "novel": 0}
    finally:
        db.close()
```

**Note:** This activity may end up as a no-op stub if `world_state_service` doesn't yet have the API we need. **That's fine for Phase 1.** Document the gap and move on. World state reconciliation gets a dedicated workflow in Phase 2.

- [ ] **Step 2: Run + commit**

```bash
git add apps/api/app/workflows/activities/post_chat_memory_activities.py
git commit -m "feat(memory-first): update_world_state activity (or stub if WS service incomplete)"
```

---

### Task 24: Activity — `update_behavioral_signals` (move from daemon thread)

**Files:**
- Modify: `apps/api/app/workflows/activities/post_chat_memory_activities.py`
- Reference: `apps/api/app/services/behavioral_signals.py`, `apps/api/app/services/chat.py:693-715`

- [ ] **Step 1: Implement against verified signatures**

Verified from `apps/api/app/services/behavioral_signals.py`:
- `extract_suggestions_from_response(db, tenant_id, response_text, message_id=None, session_id=None) -> List[BehavioralSignal]` — already commits internally.
- `detect_acted_on_signals(db, tenant_id, user_message: str, session_id=None) -> List[Tuple[BehavioralSignal, bool]]` — already commits internally.

```python
@activity.defn
async def update_behavioral_signals(
    tenant_id: str, chat_session_id: str,
    user_message_id: str, assistant_message_id: str,
) -> dict:
    """Two operations:
    1. EXTRACT suggestions from the assistant response → pending behavioral_signals.
    2. DETECT whether the user's current message acts on any prior pending signals.
    """
    from uuid import UUID
    from app.db.session import SessionLocal
    from app.models.chat import ChatMessage
    from app.services.behavioral_signals import (
        extract_suggestions_from_response,
        detect_acted_on_signals,
    )

    db = SessionLocal()
    try:
        user_msg = db.get(ChatMessage, UUID(user_message_id))
        asst_msg = db.get(ChatMessage, UUID(assistant_message_id))

        new_signals = []
        if asst_msg:
            new_signals = extract_suggestions_from_response(
                db, tenant_id=UUID(tenant_id),
                response_text=asst_msg.content,
                message_id=asst_msg.id,
                session_id=UUID(chat_session_id),
            )

        confirmations = []
        if user_msg:
            confirmations = detect_acted_on_signals(
                db, tenant_id=UUID(tenant_id),
                user_message=user_msg.content,
                session_id=UUID(chat_session_id),
            )

        return {
            "new_signals": len(new_signals),
            "confirmations": len(confirmations),
        }
    finally:
        db.close()
```

**Note:** Both helper functions commit internally — do NOT add an extra `db.commit()` here (would be a no-op anyway, but it's cleaner not to).

- [ ] **Step 2: Run + commit**

```bash
git add apps/api/app/workflows/activities/post_chat_memory_activities.py
git commit -m "feat(memory-first): update_behavioral_signals activity (moved from daemon thread)"
```

---

### Task 25: Activity — `maybe_trigger_episode`

**Files:**
- Modify: `apps/api/app/workflows/activities/post_chat_memory_activities.py`

**Architectural rule:** Activities CANNOT call `workflow.start_child_workflow()` — that's a workflow-only API. The activity computes whether an episode should be triggered and returns the parameters; the parent `PostChatMemoryWorkflow` dispatches the child workflow.

This requires Task 20's `PostChatMemoryWorkflow.run()` to be revised so per-activity return values flow back into a results dict the parent can act on. **Revise Task 20 first if not already done.**

- [ ] **Step 1: Implement the activity (no workflow APIs inside)**

```python
@activity.defn
async def maybe_trigger_episode(
    tenant_id: str, chat_session_id: str,
    user_message_id: str, assistant_message_id: str,
) -> dict:
    """Check if the chat session has accumulated >= 30 messages since the last
    episode. Returns the dispatch parameters (or {"should_trigger": False}).
    The PARENT workflow starts the child workflow."""
    from uuid import UUID
    from datetime import datetime, timezone
    from sqlalchemy import func
    from app.db.session import SessionLocal
    from app.models.chat import ChatMessage
    from app.models.conversation_episode import ConversationEpisode

    db = SessionLocal()
    try:
        last_episode = db.query(ConversationEpisode).filter(
            ConversationEpisode.session_id == UUID(chat_session_id),
        ).order_by(ConversationEpisode.window_end.desc().nullslast()).first()

        cutoff = last_episode.window_end if (last_episode and last_episode.window_end) else datetime.min.replace(tzinfo=timezone.utc)
        new_count = db.query(func.count(ChatMessage.id)).filter(
            ChatMessage.session_id == UUID(chat_session_id),
            ChatMessage.created_at > cutoff,
        ).scalar() or 0

        if new_count < 30:
            return {"should_trigger": False, "new_messages": new_count}

        first_new_msg = db.query(ChatMessage).filter(
            ChatMessage.session_id == UUID(chat_session_id),
            ChatMessage.created_at > cutoff,
        ).order_by(ChatMessage.created_at.asc()).first()

        return {
            "should_trigger": True,
            "window_start_iso": first_new_msg.created_at.isoformat(),
            "window_end_iso": datetime.now(timezone.utc).isoformat(),
            "trigger_reason": "n_30",
            "new_messages": new_count,
        }
    finally:
        db.close()
```

- [ ] **Step 2: Revise `PostChatMemoryWorkflow.run()` to capture activity results and dispatch the child workflow**

The Task 20 skeleton's `_safe` helper discards activity return values. Replace it with a version that captures them:

```python
# apps/api/app/workflows/post_chat_memory.py — revised run()
from temporalio.common import RetryPolicy
from temporalio.workflow import ParentClosePolicy

@workflow.defn
class PostChatMemoryWorkflow:
    @workflow.run
    async def run(
        self,
        tenant_id: str,
        chat_session_id: str,
        user_message_id: str,
        assistant_message_id: str,
    ) -> dict:
        results: dict = {"activities_run": 0, "errors": [], "activity_results": {}}
        args = (tenant_id, chat_session_id, user_message_id, assistant_message_id)

        async def _safe(activity, name):
            try:
                ret = await workflow.execute_activity(
                    activity, args=list(args),
                    start_to_close_timeout=_TIMEOUT, retry_policy=_RETRY,
                )
                results["activities_run"] += 1
                results["activity_results"][name] = ret
                return ret
            except Exception as e:
                results["errors"].append(f"{name}: {e}")
                return None

        await _safe(extract_knowledge, "extract_knowledge")
        await _safe(detect_commitment, "detect_commitment")
        await _safe(update_world_state, "update_world_state")
        await _safe(update_behavioral_signals, "update_behavioral_signals")
        episode_signal = await _safe(maybe_trigger_episode, "maybe_trigger_episode")

        # Parent dispatches child workflow if the activity said so.
        if episode_signal and episode_signal.get("should_trigger"):
            window_start = episode_signal["window_start_iso"]
            window_end = episode_signal["window_end_iso"]
            try:
                await workflow.start_child_workflow(
                    "EpisodeWorkflow",
                    args=[tenant_id, chat_session_id, window_start, window_end,
                          episode_signal.get("trigger_reason", "n_30")],
                    id=f"episode-{chat_session_id}-{window_start}",
                    task_queue="servicetsunami-orchestration",
                    parent_close_policy=ParentClosePolicy.ABANDON,
                )
                results["episode_dispatched"] = True
            except Exception as e:
                # Already-exists is fine — deterministic ID handles races.
                results["errors"].append(f"episode_dispatch: {e}")

        return results
```

- [ ] **Step 3: Run + commit**

```bash
pytest tests/workflows/test_post_chat_memory.py -v
git add apps/api/app/workflows/activities/post_chat_memory_activities.py \
        apps/api/app/workflows/post_chat_memory.py
git commit -m "feat(memory-first): maybe_trigger_episode + parent-dispatches-child episode pattern"
```

---

### Task 26: `EpisodeWorkflow` — generate session episodes

**Files:**
- Create: `apps/api/app/workflows/episode_workflow.py`
- Create: `apps/api/app/workflows/activities/episode_activities.py`
- Modify: `apps/api/app/workers/orchestration_worker.py` (register)
- Create: `apps/api/tests/workflows/test_episode_workflow.py`

- [ ] **Step 1: Failing test**

```python
@pytest.mark.asyncio
async def test_episode_workflow_creates_conversation_episode_row(...):
    # Insert ~5 ChatMessage rows in a session window
    # Run EpisodeWorkflow with that window
    # Assert ConversationEpisode row created with summary, key_topics, key_entities
```

- [ ] **Step 2: Implement workflow + activities**

```python
# apps/api/app/workflows/episode_workflow.py
from datetime import timedelta
from temporalio import workflow
from temporalio.common import RetryPolicy

with workflow.unsafe.imports_passed_through():
    from app.workflows.activities.episode_activities import (
        fetch_window_messages,
        summarize_window,
        embed_and_store_episode,
    )


@workflow.defn
class EpisodeWorkflow:
    @workflow.run
    async def run(
        self,
        tenant_id: str,
        chat_session_id: str,
        window_start_iso: str,
        window_end_iso: str,
        trigger_reason: str,
    ) -> dict:
        retry = RetryPolicy(maximum_attempts=3)
        timeout = timedelta(seconds=120)

        msgs = await workflow.execute_activity(
            fetch_window_messages,
            args=[chat_session_id, window_start_iso, window_end_iso],
            start_to_close_timeout=timeout, retry_policy=retry,
        )
        if not msgs or len(msgs) < 2:
            return {"created": False, "reason": "too_few_messages"}

        summary = await workflow.execute_activity(
            summarize_window, args=[msgs],
            start_to_close_timeout=timeout, retry_policy=retry,
        )
        episode_id = await workflow.execute_activity(
            embed_and_store_episode,
            args=[tenant_id, chat_session_id, window_start_iso, window_end_iso,
                  trigger_reason, summary],
            start_to_close_timeout=timeout, retry_policy=retry,
        )
        return {"created": True, "episode_id": episode_id}
```

```python
# apps/api/app/workflows/activities/episode_activities.py
from temporalio import activity
from datetime import datetime
from uuid import UUID, uuid4


@activity.defn
async def fetch_window_messages(
    chat_session_id: str, window_start_iso: str, window_end_iso: str
) -> list[dict]:
    from app.db.session import SessionLocal
    from app.models.chat import ChatMessage
    db = SessionLocal()
    try:
        rows = db.query(ChatMessage).filter(
            ChatMessage.session_id == UUID(chat_session_id),
            ChatMessage.created_at >= datetime.fromisoformat(window_start_iso),
            ChatMessage.created_at <= datetime.fromisoformat(window_end_iso),
        ).order_by(ChatMessage.created_at.asc()).all()
        return [{"role": r.role, "content": r.content, "created_at": r.created_at.isoformat()} for r in rows]
    finally:
        db.close()


@activity.defn
async def summarize_window(messages: list[dict]) -> dict:
    """Gemma4 summarization with structured output.
    Returns: {summary: str, key_topics: list[str], key_entities: list[str], mood: str}

    Calls a NEW helper `summarize_chat_window()` added to local_inference.py
    in this same task — see Step 2b below."""
    from app.services.local_inference import summarize_chat_window
    return summarize_chat_window(messages)


@activity.defn
async def embed_and_store_episode(
    tenant_id: str, chat_session_id: str,
    window_start_iso: str, window_end_iso: str,
    trigger_reason: str, summary: dict,
) -> str:
    from app.db.session import SessionLocal
    from app.models.conversation_episode import ConversationEpisode
    from app.services.embedding_service import embed_text
    db = SessionLocal()
    try:
        emb = embed_text(summary["summary"], task_type="RETRIEVAL_DOCUMENT")
        ep = ConversationEpisode(
            tenant_id=UUID(tenant_id),
            session_id=UUID(chat_session_id),
            summary=summary["summary"],
            key_topics=summary.get("key_topics", []),
            key_entities=summary.get("key_entities", []),
            mood=summary.get("mood"),
            message_count=len(summary.get("messages", [])),
            window_start=datetime.fromisoformat(window_start_iso),
            window_end=datetime.fromisoformat(window_end_iso),
            trigger_reason=trigger_reason,
            generated_by="gemma4",
            embedding=emb,
        )
        db.add(ep)
        db.commit()
        return str(ep.id)
    finally:
        db.close()
```

- [ ] **Step 2b: Add `summarize_chat_window()` to `local_inference.py`**

The existing `summarize_conversation_sync(text: str) -> Optional[str]` (line 596) returns plain text only. For episodes we need structured output. Add a new helper:

```python
# apps/api/app/services/local_inference.py — append after summarize_conversation_sync

import json

def summarize_chat_window(messages: list[dict]) -> dict:
    """Summarize a chat window with structured output via Gemma4 JSON mode.

    Args:
        messages: list of {"role", "content", "created_at"} dicts.

    Returns:
        {"summary": str, "key_topics": list[str], "key_entities": list[str],
         "mood": str, "messages": list (echoed for downstream count)}
    """
    if not messages:
        return {"summary": "", "key_topics": [], "key_entities": [], "mood": "neutral", "messages": []}

    convo_text = "\n".join(
        f"[{m['role']}] {m['content'][:500]}" for m in messages[:60]
    )
    prompt = f"""Summarize this conversation window. Return JSON ONLY in this exact shape:
{{"summary": "<2-3 sentence narrative summary>",
  "key_topics": ["topic1", "topic2"],
  "key_entities": ["Person A", "Project B"],
  "mood": "positive|neutral|concerned|escalated"}}

CONVERSATION:
{convo_text[:6000]}"""

    raw = generate_sync(
        prompt=prompt,
        model=QUALITY_MODEL,
        system="You are a conversation summarizer. Output valid JSON only, no prose, no markdown.",
        temperature=0.2,
        max_tokens=500,
        timeout=60.0,
    )
    try:
        parsed = json.loads(raw or "{}")
    except (json.JSONDecodeError, TypeError):
        parsed = {"summary": (raw or "")[:500], "key_topics": [], "key_entities": [], "mood": "neutral"}

    return {
        "summary": parsed.get("summary", "")[:2000],
        "key_topics": parsed.get("key_topics", [])[:10],
        "key_entities": parsed.get("key_entities", [])[:10],
        "mood": parsed.get("mood", "neutral"),
        "messages": messages,  # passed through for message_count downstream
    }
```

- [ ] **Step 3: Register in orchestration_worker.py + run tests + commit**

```bash
pytest tests/workflows/test_episode_workflow.py -v
git add apps/api/app/workflows/episode_workflow.py \
        apps/api/app/workflows/activities/episode_activities.py \
        apps/api/app/services/local_inference.py \
        apps/api/app/workers/orchestration_worker.py \
        apps/api/tests/workflows/test_episode_workflow.py
git commit -m "feat(memory-first): EpisodeWorkflow with window-based episode generation"
```

---

### Task 27: `IdleEpisodeScanWorkflow` — sweep idle sessions every hour

**Files:**
- Create: `apps/api/app/workflows/idle_episode_scan.py`
- Modify: `apps/api/app/workers/orchestration_worker.py`

- [ ] **Step 1: Implement**

```python
# apps/api/app/workflows/idle_episode_scan.py
"""Per-tenant long-running workflow that scans for idle sessions every hour
and triggers EpisodeWorkflow for any session idle ≥ 10 minutes with ≥ 2
unsummarised messages. continue_as_new every cycle to keep history small."""
from datetime import timedelta
from temporalio import workflow

with workflow.unsafe.imports_passed_through():
    from app.workflows.activities.episode_activities import find_idle_sessions


@workflow.defn
class IdleEpisodeScanWorkflow:
    @workflow.run
    async def run(self, tenant_id: str) -> None:
        idle = await workflow.execute_activity(
            find_idle_sessions,
            args=[tenant_id, 10],  # 10 minute idle threshold
            start_to_close_timeout=timedelta(seconds=60),
        )
        for session in idle:
            await workflow.start_child_workflow(
                "EpisodeWorkflow",
                args=[
                    tenant_id, session["id"],
                    session["window_start"], session["window_end"],
                    "idle_timeout",
                ],
                id=f"episode-{session['id']}-{session['window_start']}",
                task_queue="servicetsunami-orchestration",
            )
        # Sleep one hour, then continue_as_new
        await workflow.sleep(timedelta(hours=1))
        workflow.continue_as_new(args=[tenant_id])
```

Add `find_idle_sessions` activity to `episode_activities.py`.

- [ ] **Step 2: Auto-start on tenant creation**

In `apps/api/app/services/tenant.py` (or wherever tenants get created), after commit, schedule:
```python
client = await Client.connect(settings.TEMPORAL_ADDRESS)
await client.start_workflow(
    IdleEpisodeScanWorkflow.run,
    str(tenant.id),
    id=f"idle-episode-scan-{tenant.id}",
    task_queue="servicetsunami-orchestration",
    id_reuse_policy=WorkflowIDReusePolicy.ALLOW_DUPLICATE_FAILED_ONLY,
)
```

For existing tenants, add a one-shot startup script that starts the workflow for each tenant on first deploy.

- [ ] **Step 3: Test + commit**

```bash
git add apps/api/app/workflows/idle_episode_scan.py apps/api/app/workflows/activities/episode_activities.py apps/api/app/workers/orchestration_worker.py
git commit -m "feat(memory-first): IdleEpisodeScanWorkflow for idle session sweeps"
```

---

### Task 28: Test PostChatMemoryWorkflow + EpisodeWorkflow integration

**Files:**
- Create: `apps/api/tests/workflows/test_memory_workflows_integration.py`

- [ ] **Step 1: End-to-end test**

```python
@pytest.mark.integration
@pytest.mark.asyncio
async def test_full_post_chat_pipeline(real_temporal_env, db_session_fixture, real_tenant_fixture):
    """Insert 30+ ChatMessage rows, run PostChatMemoryWorkflow once, verify:
    - Knowledge entities created
    - At least one commitment detected (if test data has one)
    - ConversationEpisode row created (because >=30 messages triggers it)
    - memory_activities audit rows exist
    """
    # Setup: 30 chat messages
    # Act: client.execute_workflow(PostChatMemoryWorkflow, ...)
    # Assert: side effects visible in DB
```

- [ ] **Step 2: Run + commit**

```bash
pytest tests/workflows/test_memory_workflows_integration.py -v -m integration
git add apps/api/tests/workflows/test_memory_workflows_integration.py
git commit -m "test(memory-first): integration test for full memory workflow pipeline"
```

---

## Phase 1.7 — Hot path cutover

**Goal:** Switch the chat hot path to use `memory.recall()` instead of `build_memory_context_with_git()`, and dispatch `PostChatMemoryWorkflow` instead of spawning daemon threads. This is the riskiest phase — touching the user-facing path. Feature-flagged with `USE_MEMORY_V2`.

**Safety net:** All changes gated on `settings.USE_MEMORY_V2`. Default OFF in Phase 1; flipped per-tenant in Phase 1.10. Old code path remains untouched until Phase 2.

### Task 29: Add `USE_MEMORY_V2` feature flag

**Files:**
- Modify: `apps/api/app/core/config.py`
- Modify: `docker-compose.yml`

- [ ] **Step 1: Add the setting**

```python
# apps/api/app/core/config.py — inside Settings class
USE_MEMORY_V2: bool = False  # Memory-First Phase 1 cutover flag
USE_MEMORY_V2_TENANT_ALLOWLIST: list[str] = []  # if non-empty, only these tenants get V2
```

- [ ] **Step 2: Wire env var in docker-compose.yml**

```yaml
- USE_MEMORY_V2=${USE_MEMORY_V2:-false}
- USE_MEMORY_V2_TENANT_ALLOWLIST=${USE_MEMORY_V2_TENANT_ALLOWLIST:-}
```

- [ ] **Step 3: Helper for the per-tenant check**

```python
# apps/api/app/memory/feature_flag.py
from uuid import UUID
from app.core.config import settings

def is_v2_enabled(tenant_id: UUID) -> bool:
    if not settings.USE_MEMORY_V2:
        return False
    if settings.USE_MEMORY_V2_TENANT_ALLOWLIST:
        return str(tenant_id) in settings.USE_MEMORY_V2_TENANT_ALLOWLIST
    return True
```

- [ ] **Step 4: Commit**

```bash
git add apps/api/app/core/config.py apps/api/app/memory/feature_flag.py docker-compose.yml
git commit -m "feat(memory-first): USE_MEMORY_V2 feature flag with per-tenant allowlist"
```

---

### Task 30: Refactor `agent_router.py` to call `memory.recall()` behind the flag

**Files:**
- Modify: `apps/api/app/services/agent_router.py` — there are **TWO** call sites of `build_memory_context_with_git`: one at lines 357-365 (initial recall when no entities pre-loaded) and one at lines 372-380 (rebuild when entities passed in). **Both must be wrapped in the V2 branch.**

- [ ] **Step 1: Failing test for the dual-path behavior**

```python
# apps/api/tests/services/test_agent_router_memory_v2.py
def test_router_uses_v2_when_flag_on(monkeypatch, db_session_fixture, test_tenant_fixture):
    monkeypatch.setattr("app.core.config.settings.USE_MEMORY_V2", True)
    called = {"v2": False, "v1": False}
    monkeypatch.setattr("app.memory.recall", lambda *a, **kw: (called.update(v2=True), RecallResponse())[1])
    monkeypatch.setattr("app.services.memory_recall.build_memory_context_with_git",
                        lambda *a, **kw: called.update(v1=True))
    # ... call route_and_execute ...
    assert called["v2"] is True
    assert called["v1"] is False

def test_router_uses_v1_when_flag_off(monkeypatch, ...):
    monkeypatch.setattr("app.core.config.settings.USE_MEMORY_V2", False)
    # ... assert v1 called, v2 not called
```

- [ ] **Step 2: Refactor BOTH call sites**

In `agent_router.py`, replace BOTH calls (line ~357 and line ~372). Easiest pattern: extract a helper function that picks V1 or V2 based on the flag, then call it from both sites.

```python
# Add near the top of agent_router.py
from app.memory.feature_flag import is_v2_enabled

def _build_memory_context(
    db, tenant_id, message, *,
    session_entity_names, domains, max_entities, max_observations,
    include_relations, include_episodes, agent_slug,
):
    """V2 → memory.recall(); V1 → legacy build_memory_context_with_git."""
    if is_v2_enabled(tenant_id):
        from app.memory import recall
        resp = recall(
            db, tenant_id=tenant_id,
            agent_slug=agent_slug or "luna",
            query=message,
            total_token_budget=8000,
        )
        return _recall_response_to_legacy_dict(resp)
    return build_memory_context_with_git(
        db, tenant_id, message,
        session_entity_names=session_entity_names,
        domains=domains,
        max_entities=max_entities,
        max_observations=max_observations,
        include_relations=include_relations,
        include_episodes=include_episodes,
    )
```

Then replace BOTH call sites (lines ~357 and ~372) with calls to `_build_memory_context(...)` passing the same kwargs they currently pass.

Add the adapter helper:
```python
def _recall_response_to_legacy_dict(resp: "RecallResponse") -> dict:
    """Convert typed RecallResponse to the dict shape the CLI prompt builder
    expects. Phase 2 deletes this when the prompt builder consumes RecallResponse
    directly."""
    return {
        "recalled_entity_names": [e.name for e in resp.entities],
        "relevant_entities": [{"name": e.name, "category": e.category, "description": e.description, "similarity": e.similarity} for e in resp.entities],
        "relevant_memories": [],  # agent_memories are absorbed into entities in V2
        "relevant_relations": [{"from": r.from_entity, "to": r.to_entity, "type": r.relation_type} for r in resp.relations],
        "entity_observations": {},  # legacy shape was nested dict; not needed when entities carry observations
        "recent_episodes": [{"summary": ep.summary, "key_topics": ep.key_topics} for ep in resp.episodes],
        "anticipatory_context": "",  # moved to a separate composer outside recall
        "contradictions": [{"subject": c.subject, "predicate": c.predicate, "winner": c.winning_value, "loser": c.losing_value} for c in resp.contradictions],
    }
```

- [ ] **Step 3: Run + commit**

```bash
pytest tests/services/test_agent_router_memory_v2.py -v
git add apps/api/app/services/agent_router.py apps/api/tests/services/test_agent_router_memory_v2.py
git commit -m "feat(memory-first): agent_router calls memory.recall() behind USE_MEMORY_V2 flag"
```

---

### Task 31: Refactor `chat.py` post-response side effects → dispatch `PostChatMemoryWorkflow`

**Files:**
- Modify: `apps/api/app/services/chat.py` (lines ~487-733, the daemon thread blocks)

- [ ] **Step 1: Read the existing daemon thread blocks**

In `chat.py:_generate_agentic_response()`, the side-effect blocks are at:
- Lines 497-529: `KnowledgeExtractionService.extract_from_content()` daemon
- Lines 531-564: Recall feedback logging daemon
- Lines 566-686: `_maybe_create_episode()` daemon
- Lines 688-715: Behavioral signal extraction daemon
- Lines 717-720: Commitment extractor (already disabled)
- Lines 722-731: Confidence scoring (synchronous, KEEP — it stores into execution_trace.context)

The cleanest cutover pattern is **early-exit at the top of the side-effect section**: if V2 is on, dispatch the workflow and skip the entire daemon-thread block. The legacy code is preserved verbatim, no `else:` indentation churn.

- [ ] **Step 2: Add a sync workflow-dispatch helper**

Because `_generate_agentic_response()` is a SYNC function called from a sync FastAPI route, you can NOT use `await Client.connect(...)`. Use a small sync helper that runs the async dispatch on a thread:

```python
# apps/api/app/memory/dispatch.py
"""Sync wrapper to dispatch PostChatMemoryWorkflow from sync code paths.

The chat hot path is sync (chat.py:_generate_agentic_response). Temporal's
client is async-only. We bridge with a fire-and-forget thread that owns its
own event loop. Non-blocking from the caller's POV.
"""
import asyncio
import logging
import threading
from uuid import UUID

logger = logging.getLogger(__name__)


def dispatch_post_chat_memory(
    tenant_id: UUID,
    chat_session_id: UUID,
    user_message_id: UUID,
    assistant_message_id: UUID,
) -> None:
    """Fire-and-forget. Returns immediately. Logs failure but does not raise."""
    def _runner():
        try:
            from temporalio.client import Client
            from app.core.config import settings
            async def _go():
                client = await Client.connect(settings.TEMPORAL_ADDRESS)
                await client.start_workflow(
                    "PostChatMemoryWorkflow",
                    args=[str(tenant_id), str(chat_session_id),
                          str(user_message_id), str(assistant_message_id)],
                    id=f"post-chat-{user_message_id}",
                    task_queue="servicetsunami-orchestration",
                )
            asyncio.run(_go())
        except Exception as e:
            logger.warning("PostChatMemoryWorkflow dispatch failed: %s", e)

    threading.Thread(target=_runner, daemon=True).start()
```

- [ ] **Step 3: Add the early-exit gate at the top of the side-effect section**

In `apps/api/app/services/chat.py` `_generate_agentic_response()`, locate line ~497 (just BEFORE the first daemon thread spawn for knowledge extraction). Insert:

```python
# ── Memory-First V2 cutover ──
from app.memory.feature_flag import is_v2_enabled
from app.memory.dispatch import dispatch_post_chat_memory

if is_v2_enabled(tenant_id):
    dispatch_post_chat_memory(
        tenant_id=tenant_id,
        chat_session_id=session.id,
        user_message_id=user_msg.id,
        assistant_message_id=asst_msg.id,
    )
    # Keep the synchronous confidence-scoring block (lines ~722-731) running
    # below — it writes execution_trace.context, which V2 doesn't replace.
    # But SKIP all the legacy daemon threads — V2 handles them via Temporal.
    # Jump to the confidence-scoring tail by setting a flag we check below.
    _v2_active = True
else:
    _v2_active = False
```

Then wrap each of the four legacy daemon-thread spawn blocks (497-529, 531-564, 566-686, 688-715) in `if not _v2_active:`. The commitment extractor block (717-720) is already a no-op so don't touch it. The confidence-scoring block (722-731) runs unchanged in BOTH paths.

**Verification trick:** before committing, search the function for the daemon spawn calls:
```bash
grep -n "threading.Thread" apps/api/app/services/chat.py
```
Each one should be inside an `if not _v2_active:` block. Confidence scoring should NOT be wrapped.

**Critical:** Do NOT delete the legacy code. The wrapper preserves it identically — `if not _v2_active:` is the only change to the existing lines. Phase 2 cleanup deletes the legacy branch after rollout completes.

- [ ] **Step 3: Test the dispatch**

```python
@pytest.mark.integration
def test_chat_with_v2_dispatches_workflow(monkeypatch, ...):
    monkeypatch.setattr("app.core.config.settings.USE_MEMORY_V2", True)
    # Mock Temporal client to record the dispatch
    # Send a chat message
    # Assert the workflow was dispatched once with the right args
    # Assert NO daemon threads were spawned (introspect threading.enumerate())
```

- [ ] **Step 4: Commit**

```bash
pytest tests/services/test_chat_v2.py -v
git add apps/api/app/services/chat.py apps/api/tests/services/test_chat_v2.py
git commit -m "feat(memory-first): chat.py dispatches PostChatMemoryWorkflow under USE_MEMORY_V2"
```

---

### Task 32: Smoke-test V2 path end-to-end (no production cutover yet)

**Files:**
- Create: `apps/api/tests/integration/test_chat_v2_smoke.py`

- [ ] **Step 1: Smoke test**

```python
# apps/api/tests/integration/test_chat_v2_smoke.py
"""V2 smoke test — runs the full hot path with USE_MEMORY_V2=True against
a real local stack. Pre-condition: docker-compose stack running, Temporal
worker running, Ollama available."""
import pytest
from app.core.config import settings


@pytest.mark.integration
@pytest.mark.smoke
def test_send_chat_message_under_v2(http_client, real_tenant_fixture, monkeypatch):
    monkeypatch.setattr(settings, "USE_MEMORY_V2", True)

    # Send a message that should trigger memory recall
    r = http_client.post(
        f"/api/v1/chat/sessions/{real_tenant_fixture.session_id}/messages",
        json={"content": "remind me what we discussed about wolfpoint"},
        headers={"Authorization": f"Bearer {real_tenant_fixture.token}"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["assistant_message"]["content"]
    # Check execution_trace logged memory_recall as the source
    # Check workflow_history shows PostChatMemoryWorkflow was dispatched
```

- [ ] **Step 2: Run it manually against local stack**

```bash
USE_MEMORY_V2=true docker-compose up -d --no-deps api
pytest tests/integration/test_chat_v2_smoke.py -v -m smoke
```

- [ ] **Step 3: Capture logs + commit**

```bash
git add apps/api/tests/integration/test_chat_v2_smoke.py
git commit -m "test(memory-first): V2 hot path smoke test"
```

---

## Phase 1.8 — Backfill

**Goal:** Backfill embeddings for the existing 1,239+ chat messages so V2 recall can find historical context. Without this, V2's "recall past conversations" promise is broken for everything older than Phase 1 deployment.

### Task 33: `BackfillEmbeddingsWorkflow`

**Files:**
- Create: `apps/api/app/workflows/backfill_embeddings.py`
- Create: `apps/api/app/workflows/activities/backfill_activities.py`
- Create: `apps/api/app/api/v1/internal/memory_admin.py` (admin endpoint to trigger)
- Create: `apps/api/tests/workflows/test_backfill_embeddings.py`

- [ ] **Step 1: Implement the workflow**

```python
# apps/api/app/workflows/backfill_embeddings.py
"""BackfillEmbeddingsWorkflow — embeds historical chat_messages that are
missing rows in the embeddings table.

Idempotent: skips messages that already have content_type='chat_message'
embedding rows. Throttled to 50 rows per activity batch to respect the
embedding service. Continues-as-new every 10k rows to keep history small.
"""
from datetime import timedelta
from temporalio import workflow

with workflow.unsafe.imports_passed_through():
    from app.workflows.activities.backfill_activities import (
        find_unembedded_chat_messages,
        embed_message_batch,
    )


@workflow.defn
class BackfillEmbeddingsWorkflow:
    @workflow.run
    async def run(self, tenant_id: str, batch_size: int = 50, max_batches: int = 200) -> dict:
        total_embedded = 0
        for i in range(max_batches):
            batch = await workflow.execute_activity(
                find_unembedded_chat_messages,
                args=[tenant_id, batch_size],
                start_to_close_timeout=timedelta(seconds=30),
            )
            if not batch:
                break
            embedded = await workflow.execute_activity(
                embed_message_batch, args=[batch],
                start_to_close_timeout=timedelta(seconds=120),
            )
            total_embedded += embedded
            workflow.logger.info(f"Backfilled {total_embedded} embeddings so far")

        # If we hit max_batches, continue_as_new to avoid history bloat
        if i + 1 == max_batches:
            workflow.continue_as_new(args=[tenant_id, batch_size, max_batches])
        return {"embedded": total_embedded}
```

```python
# apps/api/app/workflows/activities/backfill_activities.py
from temporalio import activity
from uuid import UUID


@activity.defn
async def find_unembedded_chat_messages(tenant_id: str, batch_size: int) -> list[dict]:
    from app.db.session import SessionLocal
    from sqlalchemy import text
    db = SessionLocal()
    try:
        rows = db.execute(text("""
            SELECT cm.id, cm.role, cm.content
            FROM chat_messages cm
            JOIN chat_sessions cs ON cs.id = cm.session_id
            WHERE cs.tenant_id = CAST(:t AS uuid)
              AND char_length(cm.content) > 5
              AND NOT EXISTS (
                SELECT 1 FROM embeddings e
                WHERE e.content_type = 'chat_message'
                  AND e.content_id = cm.id::text
              )
            LIMIT :n
        """), {"t": tenant_id, "n": batch_size}).fetchall()
        # tenant_id propagated to the embed step so backfilled rows are
        # tenant-scoped (NOT NULL — that would defeat tenant isolation in recall).
        return [
            {"id": str(r.id), "role": r.role, "content": r.content, "tenant_id": tenant_id}
            for r in rows
        ]
    finally:
        db.close()


@activity.defn
async def embed_message_batch(messages: list[dict]) -> int:
    from app.db.session import SessionLocal
    from app.services.embedding_service import embed_and_store
    from uuid import UUID
    db = SessionLocal()
    try:
        for m in messages:
            text_to_embed = f"[{m['role']}] {m['content'][:2000]}"
            embed_and_store(
                db,
                tenant_id=UUID(m["tenant_id"]),  # CRITICAL: real tenant_id, not None
                content_type="chat_message",
                content_id=m["id"],
                text_content=text_to_embed,
            )
        db.commit()
        return len(messages)
    finally:
        db.close()
```

**Note:** `embed_and_store` signature in `apps/api/app/services/embedding_service.py:214`: `(db, tenant_id, content_type, content_id, text_content, task_type='RETRIEVAL_DOCUMENT')`. Plan matches — tenant_id is positional but the kwarg form is also accepted.

- [ ] **Step 2: Admin endpoint to trigger**

```python
# apps/api/app/api/v1/internal/memory_admin.py
from fastapi import APIRouter, Depends, Header, HTTPException
from temporalio.client import Client
from app.core.config import settings

router = APIRouter(prefix="/internal/memory", tags=["internal"])


@router.post("/backfill/{tenant_id}")
async def backfill_embeddings(
    tenant_id: str,
    x_internal_key: str = Header(...),
):
    if x_internal_key != settings.API_INTERNAL_KEY:
        raise HTTPException(401, "invalid internal key")
    client = await Client.connect(settings.TEMPORAL_ADDRESS)
    handle = await client.start_workflow(
        "BackfillEmbeddingsWorkflow",
        args=[tenant_id],
        id=f"backfill-embeddings-{tenant_id}",
        task_queue="servicetsunami-orchestration",
    )
    return {"workflow_id": handle.id, "tenant_id": tenant_id}
```

Mount in `app/api/v1/routes.py`.

- [ ] **Step 3: Run against local DB to backfill the test tenant**

```bash
curl -X POST http://localhost:8001/api/v1/internal/memory/backfill/0f134606-3906-44a5-9e88-6c2020f0f776 \
     -H "X-Internal-Key: $API_INTERNAL_KEY"
```

Then watch the Temporal UI at http://localhost:8233 to see the workflow progress. Expected runtime for ~1,239 messages: 2-5 minutes.

- [ ] **Step 4: Verify backfill**

```sql
SELECT COUNT(*) FROM embeddings WHERE content_type='chat_message';
-- Expect: matches count of chat_messages with content longer than 5 chars
```

- [ ] **Step 5: Commit**

```bash
git add apps/api/app/workflows/backfill_embeddings.py \
        apps/api/app/workflows/activities/backfill_activities.py \
        apps/api/app/api/v1/internal/memory_admin.py
git commit -m "feat(memory-first): BackfillEmbeddingsWorkflow + admin trigger endpoint"
```

---

## Phase 1.9 — Acceptance gates

**Goal:** Verify all the success criteria from design doc §11 BEFORE flipping `USE_MEMORY_V2=true` for any production tenant. No tenant gets V2 until every gate in this section passes.

### Task 34: End-to-end recall regression suite

**Files:**
- Create: `apps/api/tests/integration/test_memory_v2_e2e.py`

- [ ] **Step 1: Build the test corpus**

The test needs a tenant pre-loaded with known data:
- 2 entities: "Ray Aristy" (person), "Wolfpoint" (project)
- 3 observations on each
- 1 commitment ("Send report to Ray by Friday", state=open)
- 1 conversation_episode summarizing 5 messages about Wolfpoint

Create as a fixture in `tests/integration/fixtures/memory_v2_corpus.py`.

- [ ] **Step 2: Write the test cases**

```python
# apps/api/tests/integration/test_memory_v2_e2e.py
import pytest
from app.memory import recall


@pytest.mark.integration
@pytest.mark.acceptance
def test_recall_finds_known_entity(loaded_corpus_fixture):
    resp = recall(loaded_corpus_fixture.db, loaded_corpus_fixture.tenant_id, "luna",
                  "who is Ray Aristy")
    assert any("Ray Aristy" in e.name for e in resp.entities)


@pytest.mark.integration
@pytest.mark.acceptance
def test_recall_finds_known_commitment(loaded_corpus_fixture):
    resp = recall(loaded_corpus_fixture.db, loaded_corpus_fixture.tenant_id, "luna",
                  "what's open on my plate")
    assert any("Send report to Ray" in c.title for c in resp.commitments)


@pytest.mark.integration
@pytest.mark.acceptance
def test_recall_finds_episode(loaded_corpus_fixture):
    resp = recall(loaded_corpus_fixture.db, loaded_corpus_fixture.tenant_id, "luna",
                  "what did we discuss about wolfpoint")
    assert len(resp.episodes) >= 1
    assert any("wolfpoint" in (ep.summary or "").lower() for ep in resp.episodes)


@pytest.mark.integration
@pytest.mark.acceptance
def test_recall_does_not_leak_across_tenants(loaded_corpus_fixture, second_tenant_fixture):
    resp = recall(loaded_corpus_fixture.db, second_tenant_fixture.id, "luna",
                  "Ray Aristy")
    assert not any("Ray Aristy" in e.name for e in resp.entities)
```

- [ ] **Step 3: All 4 must pass — commit**

```bash
pytest tests/integration/test_memory_v2_e2e.py -v -m acceptance
git add apps/api/tests/integration/test_memory_v2_e2e.py apps/api/tests/integration/fixtures/memory_v2_corpus.py
git commit -m "test(memory-first): acceptance — V2 e2e recall regression suite"
```

---

### Task 35: Latency regression check

**Files:**
- Modify: `apps/api/scripts/baseline_chat_latency.py` (extend with V2 mode)
- Create: `docs/plans/baselines/2026-04-XX-chat-latency-v2.md` (date filled at run time)

- [ ] **Step 1: Run latency benchmark with V2 ON**

```bash
USE_MEMORY_V2=true \
BASELINE_TOKEN=<jwt> BASELINE_SESSION_ID=<sess> BASELINE_N=30 \
  python apps/api/scripts/baseline_chat_latency.py | tee /tmp/baseline_v2.json
```

- [ ] **Step 2: Compare against Phase 0 baseline**

```python
# Quick comparison script
import json
v1 = json.load(open("/tmp/baseline.json"))
v2 = json.load(open("/tmp/baseline_v2.json"))
p95_delta = (v2["p95"] - v1["p95"]) / v1["p95"]
print(f"p95 V1={v1['p95']:.2f}s V2={v2['p95']:.2f}s delta={p95_delta:+.0%}")
assert p95_delta < 0.30, f"V2 regressed p95 by {p95_delta:+.0%} — anti-success criterion #1 hit"
```

**Acceptance:** §11.1 anti-success criterion: "Fast-path p95 regresses > 30% from pre-Phase-1 baseline → roll back". Phase 1 target is to be **no worse than baseline** (we are not delivering the warm-pod fast path until Phase 3a).

- [ ] **Step 3: Commit comparison report**

```bash
git add docs/plans/baselines/2026-04-XX-chat-latency-v2.md
git commit -m "test(memory-first): V2 latency baseline (vs Phase 0)"
```

---

### Task 36: Anti-success criteria automated tripwire

**Files:**
- Create: `apps/api/scripts/check_acceptance_gates.sh`

- [ ] **Step 1: Write the gate script**

```bash
#!/bin/bash
# apps/api/scripts/check_acceptance_gates.sh
# Runs all Phase 1 acceptance gates. Exits non-zero if ANY fails.
set -e

echo "Gate 1: F1 ≥ 0.7 commitment classifier"
F1=$(jq .f1 docs/plans/baselines/commitment-classifier-f1.md 2>/dev/null || \
     grep -oP 'F1 = \K[\d.]+' docs/plans/baselines/commitment-classifier-f1.md)
python -c "import sys; sys.exit(0 if float('$F1') >= 0.7 else 1)"
echo "  PASS: F1 = $F1"

echo "Gate 2: latency p95 not regressed > 30%"
python apps/api/scripts/check_latency_regression.py
echo "  PASS"

echo "Gate 3: cross-tenant isolation tests"
cd apps/api && pytest tests/memory/test_tenant_isolation.py tests/integration/test_memory_v2_e2e.py::test_recall_does_not_leak_across_tenants -v
echo "  PASS"

echo "Gate 4: V2 e2e acceptance suite"
pytest tests/integration/test_memory_v2_e2e.py -v -m acceptance
echo "  PASS"

echo "Gate 5: zero InFailedSqlTransaction errors in last hour"
ERRS=$(docker-compose logs --since 1h api 2>&1 | grep -c InFailedSqlTransaction || true)
[ "$ERRS" -eq 0 ] || { echo "  FAIL: $ERRS InFailedSqlTransaction errors"; exit 1; }
echo "  PASS"

echo ""
echo "ALL GATES PASSED. Safe to flip USE_MEMORY_V2=true for the first tenant."
```

- [ ] **Step 2: Run + commit**

```bash
chmod +x apps/api/scripts/check_acceptance_gates.sh
./apps/api/scripts/check_acceptance_gates.sh
git add apps/api/scripts/check_acceptance_gates.sh
git commit -m "feat(memory-first): acceptance gate runner script"
```

---

### Task 37: Manual QA checklist (1 hour against local stack)

**Files:**
- Create: `docs/plans/baselines/manual-qa-checklist.md`

- [ ] **Step 1: Walk through the checklist** (no code, just human verification)

```markdown
# Phase 1 Manual QA — V2 Smoke Pass

Tester: Simon
Date: ____
Stack: docker-compose, USE_MEMORY_V2=true, tenant 0f134606

## Recall verification (10 minutes)
- [ ] Open chat at http://localhost:8002/chat
- [ ] Ask: "who is Ray Aristy" → Luna answers without saying "I don't know"
- [ ] Ask: "what are my open commitments" → Luna lists at least one real one
- [ ] Ask: "what did we discuss yesterday" → Luna references at least one real episode
- [ ] Ask: "thanks" → Luna responds in <3 seconds (no recall blocking on trivial)
- [ ] Check: response references entities recalled (not hallucinated)

## Workflow verification (10 minutes)
- [ ] Open Temporal UI at http://localhost:8233
- [ ] Send a chat message
- [ ] Verify PostChatMemoryWorkflow appears with id=post-chat-<uuid>
- [ ] Verify all 5 activities completed
- [ ] Verify no daemon thread errors in api logs

## Episode verification (15 minutes)
- [ ] Send 30+ messages in a session (use a script if needed)
- [ ] Wait for PostChatMemoryWorkflow to fire
- [ ] Verify EpisodeWorkflow was triggered (Temporal UI)
- [ ] Verify a new conversation_episodes row exists with window_start/window_end set
- [ ] Verify the episode summary references actual content from the messages

## Commitment detection (15 minutes)
- [ ] Send: "I'll send the design doc to Ray by tomorrow"
- [ ] Wait 30 seconds
- [ ] SELECT * FROM commitment_records WHERE owner_agent_slug='user' ORDER BY created_at DESC LIMIT 1;
- [ ] Verify a row exists with title containing "design doc" and due_at ≈ tomorrow

## Multi-tenant isolation (10 minutes)
- [ ] Log in as tenant A, send "remember: ProjectAtlas is launching Friday"
- [ ] Log out
- [ ] Log in as tenant B, ask "what do you know about ProjectAtlas"
- [ ] Verify Luna says she doesn't know (no leakage)

## Sign-off
- [ ] All checks above pass
- [ ] No console errors in api logs
- [ ] No errors in temporal-worker logs
```

- [ ] **Step 2: Execute the checklist**

This is 60 minutes of focused testing. Don't speed-run it. Note any failures in a TestRail-style block at the bottom.

- [ ] **Step 3: Commit the signed checklist**

```bash
git add docs/plans/baselines/manual-qa-checklist.md
git commit -m "test(memory-first): manual QA checklist completed and signed"
```

---

### Task 38: Phase 1 final acceptance gate

**Files:**
- Modify: `docs/plans/2026-04-07-memory-first-phase-1-plan.md` (this file — write a final gate marker)

- [ ] **Step 1: Verify all preceding gates passed**

```bash
./apps/api/scripts/check_acceptance_gates.sh
```

- [ ] **Step 2: Verify the manual QA checklist is signed and committed**

```bash
git log --oneline | grep "manual QA"
```

- [ ] **Step 3: Make the call**

| All gates green? | Action |
|---|---|
| Yes | ✅ Proceed to Phase 1.10 rollout. |
| No (any single gate failed) | ❌ STOP. Fix the failing gate's root cause. Do NOT skip. |

- [ ] **Step 4: Commit the marker**

```bash
# Append to this plan file:
# "Phase 1.9 acceptance gate: PASS on YYYY-MM-DD by <who>. Proceeding to Phase 1.10."
git add docs/plans/2026-04-07-memory-first-phase-1-plan.md
git commit -m "gate(memory-first): Phase 1.9 acceptance — PASS"
```

---

## Phase 1.10 — Rollout

**Goal:** Flip `USE_MEMORY_V2=true` per tenant in a staged rollout. Cap blast radius to one tenant at a time. 48-hour observation window between flips.

### Task 39: Enable V2 for `saguilera1608@gmail.com` (tenant `0f134606`) only

**Files:**
- Modify: `docker-compose.yml` (env var update)
- OR: API admin endpoint to update tenant features

- [ ] **Step 1: Set the allowlist**

```bash
# In docker-compose.yml, set:
- USE_MEMORY_V2=true
- USE_MEMORY_V2_TENANT_ALLOWLIST=0f134606-3906-44a5-9e88-6c2020f0f776

# Restart api container
docker-compose up -d --no-deps api
```

- [ ] **Step 2: Verify only tenant 0f134606 sees V2**

Send a chat message as tenant 0f134606 → check Temporal UI for PostChatMemoryWorkflow dispatch (V2 path).
Send as a different tenant → verify NO PostChatMemoryWorkflow dispatched (V1 daemon-thread path still in use).

- [ ] **Step 3: Set up monitoring for 48 hours**

Watch:
- Temporal UI for failed PostChatMemoryWorkflow runs
- API logs for `InFailedSqlTransaction` errors
- API logs for `memory.recall` warnings
- Chat latency (should not regress)

If any anti-success criterion trips during the 48h window, IMMEDIATELY revert by unsetting `USE_MEMORY_V2_TENANT_ALLOWLIST`.

- [ ] **Step 4: Commit the rollout step**

```bash
git add docker-compose.yml
git commit -m "rollout(memory-first): enable V2 for tenant 0f134606 only"
```

---

### Task 40: 48-hour observation + go/no-go decision

**Files:**
- Create: `docs/plans/baselines/v2-rollout-observation.md`

- [ ] **Step 1: After 48 hours, collect metrics**

```bash
# Latency samples
docker-compose logs --since 48h api | grep "memory.recall" | python apps/api/scripts/parse_recall_latencies.py

# PostChatMemoryWorkflow success rate
docker-compose logs --since 48h temporal | grep PostChatMemory | grep -E "(Completed|Failed)" | sort | uniq -c

# Error rate on chat endpoint
docker-compose logs --since 48h api | grep -E "POST /chat/.*messages" | grep -c "5[0-9][0-9]"
```

- [ ] **Step 2: Document findings**

```markdown
# V2 Rollout — 48 Hour Observation (tenant 0f134606)

Period: YYYY-MM-DD HH:MM to YYYY-MM-DD HH:MM
Total chat turns: ____
PostChatMemoryWorkflow success rate: ___%
Recall p50 / p95: ___ms / ___ms (Phase 0 baseline was ___ / ___)
InFailedSqlTransaction errors: ____
User feedback (Simon's qualitative report): ____

## Decision
[ ] Go: enable for next tenant cohort (Task 41)
[ ] No-go: revert and root-cause
```

- [ ] **Step 3: Commit the decision**

```bash
git add docs/plans/baselines/v2-rollout-observation.md
git commit -m "rollout(memory-first): 48h observation report — GO/NO-GO"
```

---

### Task 41: Enable V2 globally (or staged cohorts)

**Files:**
- Modify: `docker-compose.yml`

- [ ] **Step 1: Decide rollout shape**

Three options:
- **A. All-at-once**: clear `USE_MEMORY_V2_TENANT_ALLOWLIST` so all tenants get V2.
- **B. Staged cohorts**: add 3-5 tenants at a time, observe 24 hours between batches.
- **C. Indefinite single-tenant pilot**: keep V2 on tenant 0f134606 only until Phase 2 stabilization.

Recommendation: **B**. Lowest risk while still completing Phase 1 rollout.

- [ ] **Step 2: Roll out cohort 1** (add 3 tenants to allowlist)

```bash
# Pick 3 less-active tenants from the 17. Add to allowlist.
# Edit docker-compose.yml USE_MEMORY_V2_TENANT_ALLOWLIST
docker-compose up -d --no-deps api
```

- [ ] **Step 3: Observe 24 hours, then cohort 2 (5 tenants), then 4 (remaining)**

Each cohort flip is its own commit:
```bash
git commit -m "rollout(memory-first): V2 cohort N enabled"
```

- [ ] **Step 4: When all 17 tenants are on V2, drop the allowlist**

```yaml
- USE_MEMORY_V2=true
# (USE_MEMORY_V2_TENANT_ALLOWLIST removed entirely → V2 for all)
```

- [ ] **Step 5: Mark Phase 1 complete**

```bash
git commit -m "rollout(memory-first): Phase 1 complete — V2 enabled for all tenants"
```

- [ ] **Step 6: Open the draft PR for review**

```bash
gh pr ready feat/memory-first-phase-1
gh pr view --web
```

The PR has been a draft throughout. Now it's ready-for-review with the full Phase 1 history as the audit trail. Mark as **ready** for cross-tool review.

---

## Done — Phase 1 Complete

When Task 41 ships:
- `apps/api/app/memory/` is the single API for recall, record, ingest
- All post-chat side effects run as Temporal activities, not daemon threads
- Commitment detection works (Gemma4 classifier, F1 ≥ 0.7)
- Episodes generate from real session windows (not just legacy weekly rollups)
- Embeddings are backfilled for historical chat messages
- All 17 tenants on V2 with monitored stability
- Phase 2 (Rust extraction) can begin against the frozen Phase 1 contract

**What Phase 1 deliberately did NOT do** (deferred to later phases):
- Rust embedding-service or memory-core (Phase 2)
- gRPC wire protocol (Phase 2)
- K8s migration / helm charts / warm chat-runtime pods (Phase 3a)
- Email/calendar/jira/github/ads ingesters (Phase 3b)
- Federation, voice, devices, scrapers, marketplace (Phase 4)
- The "fast path p50 < 2s" SLO — Phase 1 maintains baseline; warm pods deliver this in Phase 3a
- Long-running Claude CLI supervisor (Phase 4 sub-design)

**Cleanup deferred to Phase 2 kickoff:**
- Delete the V1 daemon-thread branches in `chat.py` (kept as fallback during rollout)
- Delete `apps/api/app/services/memory_recall.py` (kept as fallback)
- Delete `apps/api/app/services/commitment_extractor.py` (verified zero callers)
- Delete the `_recall_response_to_legacy_dict` adapter in `agent_router.py`

---

## Reference: total task count

- Phase 0: 3 tasks
- Phase 1.1: 3 tasks
- Phase 1.2: 3 tasks
- Phase 1.3: 4 tasks
- Phase 1.4: 2 tasks
- Phase 1.5: 4 tasks
- Phase 1.6: 9 tasks
- Phase 1.7: 4 tasks
- Phase 1.8: 1 task
- Phase 1.9: 5 tasks
- Phase 1.10: 3 tasks

**Total: 41 tasks.**

Realistic timeline at 1-2 tasks per working day with TDD discipline: **6-8 weeks**, matching the design doc Phase 1 estimate. Acceptance gates (Tasks 17, 19, 36, 38, 40) are blocking checkpoints — do not skip.
\nGate result: F1=0.722 on 2026-04-08. Proceed=YES.
