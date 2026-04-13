# A2A Collaboration: Live Demo Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Wire the existing Coalition Workflow, Shared Blackboard, and Collaboration Service into a fully demo-ready agent-to-agent collaboration system with real-time SSE visibility and replay.

**Architecture:** 4-phase Temporal workflow (CoalitionWorkflow on agentprovision-orchestration) dispatches child ChatCliWorkflow calls (agentprovision-code) per phase. Blackboard entries are written to Postgres and published to Redis pub/sub. Frontend opens a long-lived session-level SSE stream to receive async workflow events, then opens a collaboration-specific SSE stream for detailed phase/blackboard updates. CollaborationPanel shows live phase timeline + blackboard feed + replay mode.

**Tech Stack:** Python/FastAPI, Temporal.io, Redis pub/sub (`redis[hiredis]`), PostgreSQL, React 18, SSE (text/event-stream), ChatCliWorkflow (code-worker).

**Spec:** `docs/plans/2026-04-12-a2a-collaboration-demo-design.md`

---

## File Map

| File | Status | Responsibility |
|------|--------|----------------|
| `apps/api/migrations/091_blackboard_chat_session_and_source_node.sql` | **Create** | Add `chat_session_id` to blackboards, `source_node_id` to blackboard_entries |
| `apps/api/app/models/blackboard.py` | **Modify** | Add `chat_session_id` column to `Blackboard` |
| `apps/api/app/schemas/blackboard.py` | **Modify** | Add `chat_session_id` to `BlackboardCreate` and `BlackboardInDB` |
| `apps/api/app/services/blackboard_service.py` | **Modify** | Pass `chat_session_id` in `create_blackboard()` |
| `apps/api/app/schemas/collaboration.py` | **Modify** | Add `INCIDENT_INVESTIGATION` pattern, 4 new phase enums, pattern/role maps |
| `apps/api/app/services/collaboration_service.py` | **Modify** | Add phase-to-entry-type and phase-to-role mappings, unknown-phase guard |
| `apps/api/app/services/collaboration_events.py` | **Create** | Redis pub/sub client singleton + `publish_event()` + `subscribe_stream()` |
| `apps/api/app/core/config.py` | **Modify** | Add `REDIS_URL` setting |
| `apps/api/requirements.txt` | **Modify** | Add `redis[hiredis]` |
| `helm/values/agentprovision-api-local.yaml` | **Modify** | Add `REDIS_URL` env var to configMap |
| `apps/api/app/workflows/activities/coalition_activities.py` | **Modify** | Fix hyphen/underscore bug; add incident keywords; replace stubs with `prepare_collaboration_step` + `record_collaboration_step` |
| `apps/api/app/workflows/coalition_workflow.py` | **Modify** | Refactor step loop: prepare activity → `ChatCliWorkflow` child → record activity |
| `apps/api/app/api/v1/chat.py` | **Modify** | Add `GET /sessions/{id}/events` long-lived session SSE endpoint |
| `apps/api/app/api/v1/collaborations.py` | **Modify** | Add `GET /{id}/stream`, `GET /{id}/detail`, `POST /trigger` endpoints |
| `apps/web/src/components/CollaborationPanel.js` | **Create** | Phase timeline + blackboard feed + status bar + live/replay modes |
| `apps/web/src/components/CollaborationPanel.css` | **Create** | Ocean theme styles for CollaborationPanel |
| `apps/web/src/pages/ChatPage.js` | **Modify** | Open session events SSE, render CollaborationPanel |
| `apps/api/scripts/seed_incident_demo.py` | **Create** | Idempotent demo scenario seed (4 agents, knowledge graph, coalition template) |

---

## Task 1: Database Migration 091

**Files:**
- Create: `apps/api/migrations/091_blackboard_chat_session_and_source_node.sql`

- [ ] **Step 1: Write migration SQL**

```sql
-- Migration 091: Add chat_session_id to blackboards and source_node_id to blackboard_entries

-- Link blackboards to the chat session that spawned them
ALTER TABLE blackboards ADD COLUMN IF NOT EXISTS chat_session_id UUID REFERENCES chat_sessions(id) ON DELETE SET NULL;
CREATE INDEX IF NOT EXISTS idx_blackboards_chat_session_id ON blackboards(chat_session_id);

-- Federation-readiness: track which node authored an entry (NULL = local)
ALTER TABLE blackboard_entries ADD COLUMN IF NOT EXISTS source_node_id VARCHAR(100) DEFAULT NULL;

INSERT INTO _migrations (version, name, applied_at)
VALUES (91, '091_blackboard_chat_session_and_source_node', NOW())
ON CONFLICT (version) DO NOTHING;
```

- [ ] **Step 2: Apply migration**

```bash
PG_POD=$(kubectl get pod -n agentprovision -l app.kubernetes.io/name=postgresql -o jsonpath='{.items[0].metadata.name}')
kubectl cp apps/api/migrations/091_blackboard_chat_session_and_source_node.sql agentprovision/$PG_POD:/tmp/migration.sql
kubectl exec -n agentprovision $PG_POD -- psql -U postgres agentprovision -f /tmp/migration.sql
```

Expected output includes `ALTER TABLE`, `CREATE INDEX`, `INSERT 0 1`.

- [ ] **Step 3: Verify**

```bash
kubectl exec -n agentprovision $PG_POD -- psql -U postgres agentprovision -c "\d blackboards" | grep chat_session
kubectl exec -n agentprovision $PG_POD -- psql -U postgres agentprovision -c "\d blackboard_entries" | grep source_node
```

Both should show the new columns.

- [ ] **Step 4: Commit**

```bash
git add apps/api/migrations/091_blackboard_chat_session_and_source_node.sql
git commit -m "feat: migration 091 — add chat_session_id to blackboards, source_node_id to blackboard_entries"
```

---

## Task 2: Blackboard Model + Schema + Service

**Files:**
- Modify: `apps/api/app/models/blackboard.py`
- Modify: `apps/api/app/schemas/blackboard.py`
- Modify: `apps/api/app/services/blackboard_service.py`
- Test: `apps/api/tests/test_blackboard_chat_session.py`

- [ ] **Step 1: Write failing tests**

Create `apps/api/tests/test_blackboard_chat_session.py`:

```python
import os
os.environ["TESTING"] = "True"

import uuid
from app.schemas.blackboard import BlackboardCreate, BlackboardInDB


def test_blackboard_create_accepts_chat_session_id():
    board = BlackboardCreate(title="Test board", chat_session_id=uuid.uuid4())
    assert board.chat_session_id is not None


def test_blackboard_create_chat_session_id_optional():
    board = BlackboardCreate(title="Test board")
    assert board.chat_session_id is None


def test_blackboard_in_db_has_chat_session_id():
    assert "chat_session_id" in BlackboardInDB.model_fields
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd apps/api && pytest tests/test_blackboard_chat_session.py -v
```

Expected: FAIL — `BlackboardCreate` has no `chat_session_id` field.

- [ ] **Step 3: Update Blackboard model**

In `apps/api/app/models/blackboard.py`, add after the `goal_id` column:

```python
from sqlalchemy import Column, DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint
# existing imports...

class Blackboard(Base):
    # ... existing columns ...
    goal_id = Column(UUID(as_uuid=True), ForeignKey("goal_records.id"), nullable=True)
    chat_session_id = Column(UUID(as_uuid=True), ForeignKey("chat_sessions.id", ondelete="SET NULL"), nullable=True)
    # ... rest unchanged ...
```

- [ ] **Step 4: Update BlackboardCreate and BlackboardInDB schemas**

In `apps/api/app/schemas/blackboard.py`:

```python
class BlackboardCreate(BaseModel):
    title: str
    plan_id: Optional[uuid.UUID] = None
    goal_id: Optional[uuid.UUID] = None
    chat_session_id: Optional[uuid.UUID] = None  # ADD THIS


class BlackboardInDB(BaseModel):
    id: uuid.UUID
    tenant_id: uuid.UUID
    plan_id: Optional[uuid.UUID] = None
    goal_id: Optional[uuid.UUID] = None
    chat_session_id: Optional[uuid.UUID] = None  # ADD THIS
    title: str
    status: str
    version: int
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True
```

- [ ] **Step 5: Update create_blackboard() service**

In `apps/api/app/services/blackboard_service.py`, update the `create_blackboard()` function:

```python
def create_blackboard(
    db: Session,
    tenant_id: uuid.UUID,
    board_in: BlackboardCreate,
) -> Blackboard:
    _validate_plan_ref(db, tenant_id, board_in.plan_id)
    _validate_goal_ref(db, tenant_id, board_in.goal_id)

    board = Blackboard(
        tenant_id=tenant_id,
        title=board_in.title,
        plan_id=board_in.plan_id,
        goal_id=board_in.goal_id,
        chat_session_id=board_in.chat_session_id,  # ADD THIS
        status="active",
        version=0,
    )
    db.add(board)
    db.commit()
    db.refresh(board)
    return board
```

- [ ] **Step 6: Run tests to verify they pass**

```bash
cd apps/api && pytest tests/test_blackboard_chat_session.py -v
```

Expected: 3 PASSED.

- [ ] **Step 7: Commit**

```bash
git add apps/api/app/models/blackboard.py apps/api/app/schemas/blackboard.py apps/api/app/services/blackboard_service.py apps/api/tests/test_blackboard_chat_session.py
git commit -m "feat: add chat_session_id to Blackboard model, schema, and service"
```

---

## Task 3: Collaboration Schema Additions

**Files:**
- Modify: `apps/api/app/schemas/collaboration.py`
- Test: `apps/api/tests/test_collaboration_schema.py`

- [ ] **Step 1: Write failing tests**

Create `apps/api/tests/test_collaboration_schema.py`:

```python
import os
os.environ["TESTING"] = "True"

from app.schemas.collaboration import (
    CollaborationPattern,
    CollaborationPhase,
    PATTERN_PHASES,
    PHASE_REQUIRED_ROLES,
)


def test_incident_investigation_pattern_exists():
    assert CollaborationPattern.INCIDENT_INVESTIGATION == "incident_investigation"


def test_incident_investigation_phases():
    phases = PATTERN_PHASES["incident_investigation"]
    assert phases == ["triage", "investigate", "analyze", "command"]


def test_incident_investigation_roles():
    assert PHASE_REQUIRED_ROLES["triage"] == ["triage_agent"]
    assert PHASE_REQUIRED_ROLES["investigate"] == ["investigator"]
    assert PHASE_REQUIRED_ROLES["analyze"] == ["analyst"]
    assert PHASE_REQUIRED_ROLES["command"] == ["commander"]


def test_new_phase_enums_exist():
    assert CollaborationPhase.TRIAGE == "triage"
    assert CollaborationPhase.INVESTIGATE == "investigate"
    assert CollaborationPhase.ANALYZE == "analyze"
    assert CollaborationPhase.COMMAND == "command"
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd apps/api && pytest tests/test_collaboration_schema.py -v
```

Expected: FAIL — `CollaborationPattern` has no `INCIDENT_INVESTIGATION`.

- [ ] **Step 3: Update collaboration schemas**

In `apps/api/app/schemas/collaboration.py`, update the enums and dicts:

```python
class CollaborationPattern(str, Enum):
    PROPOSE_CRITIQUE_REVISE = "propose_critique_revise"
    PLAN_VERIFY = "plan_verify"
    RESEARCH_SYNTHESIZE = "research_synthesize"
    DEBATE_RESOLVE = "debate_resolve"
    INCIDENT_INVESTIGATION = "incident_investigation"  # ADD


class CollaborationPhase(str, Enum):
    PROPOSE = "propose"
    CRITIQUE = "critique"
    REVISE = "revise"
    VERIFY = "verify"
    SYNTHESIZE = "synthesize"
    RESEARCH = "research"
    DEBATE = "debate"
    RESOLVE = "resolve"
    COMPLETE = "complete"
    TRIAGE = "triage"         # ADD
    INVESTIGATE = "investigate"  # ADD
    ANALYZE = "analyze"       # ADD
    COMMAND = "command"       # ADD


PATTERN_PHASES = {
    "propose_critique_revise": ["propose", "critique", "revise", "verify"],
    "plan_verify": ["propose", "verify"],
    "research_synthesize": ["research", "synthesize", "verify"],
    "debate_resolve": ["propose", "debate", "resolve"],
    "incident_investigation": ["triage", "investigate", "analyze", "command"],  # ADD
}

PHASE_REQUIRED_ROLES = {
    "propose": ["planner"],
    "critique": ["critic"],
    "revise": ["planner"],
    "verify": ["verifier"],
    "synthesize": ["synthesizer"],
    "research": ["researcher"],
    "debate": ["critic", "planner"],
    "resolve": ["synthesizer"],
    "triage": ["triage_agent"],      # ADD
    "investigate": ["investigator"],  # ADD
    "analyze": ["analyst"],           # ADD
    "command": ["commander"],         # ADD
}
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
cd apps/api && pytest tests/test_collaboration_schema.py -v
```

Expected: 4 PASSED.

- [ ] **Step 5: Commit**

```bash
git add apps/api/app/schemas/collaboration.py apps/api/tests/test_collaboration_schema.py
git commit -m "feat: add incident_investigation pattern and phases to collaboration schemas"
```

---

## Task 4: Collaboration Service Phase Mappings

**Files:**
- Modify: `apps/api/app/services/collaboration_service.py`
- Test: `apps/api/tests/test_collaboration_phase_mappings.py`

- [ ] **Step 1: Write failing tests**

Create `apps/api/tests/test_collaboration_phase_mappings.py`:

```python
import os
os.environ["TESTING"] = "True"

import pytest
from unittest.mock import MagicMock, patch
from app.schemas.blackboard import EntryType, AuthorRole


def test_triage_phase_uses_evidence_entry_type():
    """advance_phase with triage phase should create an EVIDENCE entry."""
    from app.services.collaboration_service import _phase_entry_type, _phase_author_role
    assert _phase_entry_type("triage") == EntryType.EVIDENCE
    assert _phase_entry_type("investigate") == EntryType.EVIDENCE
    assert _phase_entry_type("analyze") == EntryType.CRITIQUE
    assert _phase_entry_type("command") == EntryType.SYNTHESIS


def test_triage_phase_uses_correct_author_role():
    from app.services.collaboration_service import _phase_author_role
    assert _phase_author_role("triage") == AuthorRole.RESEARCHER
    assert _phase_author_role("investigate") == AuthorRole.RESEARCHER
    assert _phase_author_role("analyze") == AuthorRole.CRITIC
    assert _phase_author_role("command") == AuthorRole.SYNTHESIZER


def test_unknown_phase_raises_value_error():
    from app.services.collaboration_service import _phase_entry_type
    with pytest.raises(ValueError, match="Unknown collaboration phase"):
        _phase_entry_type("nonexistent_phase")
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd apps/api && pytest tests/test_collaboration_phase_mappings.py -v
```

Expected: FAIL — `_phase_entry_type` not defined.

- [ ] **Step 3: Extract and extend phase mapping helpers in collaboration_service.py**

In `apps/api/app/services/collaboration_service.py`, add two helper functions near the top (after imports) and update `advance_phase()` to use them:

```python
# Phase mapping helpers — extend here when adding new patterns
_PHASE_TO_ENTRY_TYPE = {
    "propose": EntryType.PROPOSAL,
    "critique": EntryType.CRITIQUE,
    "revise": EntryType.PROPOSAL,
    "verify": EntryType.EVIDENCE,
    "synthesize": EntryType.SYNTHESIS,
    "research": EntryType.EVIDENCE,
    "debate": EntryType.DISAGREEMENT,
    "resolve": EntryType.RESOLUTION,
    "triage": EntryType.EVIDENCE,
    "investigate": EntryType.EVIDENCE,
    "analyze": EntryType.CRITIQUE,
    "command": EntryType.SYNTHESIS,
}

_PHASE_TO_AUTHOR_ROLE = {
    "propose": AuthorRole.PLANNER,
    "critique": AuthorRole.CRITIC,
    "revise": AuthorRole.PLANNER,
    "verify": AuthorRole.VERIFIER,
    "synthesize": AuthorRole.SYNTHESIZER,
    "research": AuthorRole.RESEARCHER,
    "debate": AuthorRole.CRITIC,
    "resolve": AuthorRole.SYNTHESIZER,
    "triage": AuthorRole.RESEARCHER,
    "investigate": AuthorRole.RESEARCHER,
    "analyze": AuthorRole.CRITIC,
    "command": AuthorRole.SYNTHESIZER,
}


def _phase_entry_type(phase: str) -> EntryType:
    if phase not in _PHASE_TO_ENTRY_TYPE:
        raise ValueError(f"Unknown collaboration phase: '{phase}'. Add it to _PHASE_TO_ENTRY_TYPE.")
    return _PHASE_TO_ENTRY_TYPE[phase]


def _phase_author_role(phase: str) -> AuthorRole:
    if phase not in _PHASE_TO_AUTHOR_ROLE:
        raise ValueError(f"Unknown collaboration phase: '{phase}'. Add it to _PHASE_TO_AUTHOR_ROLE.")
    return _PHASE_TO_AUTHOR_ROLE[phase]
```

Then in `advance_phase()`, replace the inline dicts at lines 155-179 with calls to the helpers:

```python
# Replace:
#   entry_type = phase_to_entry_type.get(current_phase, EntryType.PROPOSAL)
#   author_role = phase_to_role.get(current_phase, AuthorRole.CONTRIBUTOR)
# With:
entry_type = _phase_entry_type(current_phase)
author_role = _phase_author_role(current_phase)
```

Note: the `"debate"` phase had special handling (`EntryType.DISAGREEMENT if not agrees_with_previous else EntryType.EVIDENCE`). Preserve this override after the helper call:

```python
entry_type = _phase_entry_type(current_phase)
# Debate phase overrides based on agreement
if current_phase == "debate" and not agrees_with_previous:
    entry_type = EntryType.DISAGREEMENT
author_role = _phase_author_role(current_phase)
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
cd apps/api && pytest tests/test_collaboration_phase_mappings.py -v
```

Expected: 3 PASSED.

- [ ] **Step 5: Run existing collaboration tests to verify no regression**

```bash
cd apps/api && pytest tests/ -k "collab or blackboard" -v
```

Expected: All pass.

- [ ] **Step 6: Commit**

```bash
git add apps/api/app/services/collaboration_service.py apps/api/tests/test_collaboration_phase_mappings.py
git commit -m "feat: add incident_investigation phase mappings and unknown-phase guard to collaboration service"
```

---

## Task 5: Redis Client + Config

**Files:**
- Modify: `apps/api/requirements.txt`
- Modify: `apps/api/app/core/config.py`
- Create: `apps/api/app/services/collaboration_events.py`
- Modify: `helm/values/agentprovision-api-local.yaml`
- Test: `apps/api/tests/test_collaboration_events.py`

- [ ] **Step 1: Add redis dependency**

In `apps/api/requirements.txt`, add:

```
redis[hiredis]>=5.0.0
```

- [ ] **Step 2: Add REDIS_URL to settings**

In `apps/api/app/core/config.py`, add after `TEMPORAL_ADDRESS`:

```python
REDIS_URL: str = "redis://redis:6379/0"
```

- [ ] **Step 3: Add REDIS_URL to Helm configMap**

In `helm/values/agentprovision-api-local.yaml`, add to the `configMap.data` section:

```yaml
configMap:
  enabled: true
  data:
    # ... existing entries ...
    REDIS_URL: "redis://redis:6379/0"
```

- [ ] **Step 4: Write failing tests**

Create `apps/api/tests/test_collaboration_events.py`:

```python
import os
os.environ["TESTING"] = "True"

import json
from unittest.mock import MagicMock, patch


def test_publish_event_sends_to_correct_channel():
    mock_redis = MagicMock()
    with patch("app.services.collaboration_events._get_redis", return_value=mock_redis):
        from app.services.collaboration_events import publish_event
        publish_event("collab-123", "phase_started", {"phase": "triage"})
        mock_redis.publish.assert_called_once()
        channel, message = mock_redis.publish.call_args[0]
        assert channel == "collaboration:collab-123"
        data = json.loads(message)
        assert data["event_type"] == "phase_started"
        assert data["payload"]["phase"] == "triage"
        assert "timestamp" in data


def test_publish_session_event_sends_to_session_channel():
    mock_redis = MagicMock()
    with patch("app.services.collaboration_events._get_redis", return_value=mock_redis):
        from app.services.collaboration_events import publish_session_event
        publish_session_event("session-456", "collaboration_started", {"collaboration_id": "collab-123"})
        channel, message = mock_redis.publish.call_args[0]
        assert channel == "session:session-456"
```

- [ ] **Step 5: Run tests to verify they fail**

```bash
cd apps/api && pytest tests/test_collaboration_events.py -v
```

Expected: FAIL — module not found.

- [ ] **Step 6: Create collaboration_events.py**

Create `apps/api/app/services/collaboration_events.py`:

```python
"""Redis pub/sub publisher for collaboration events.

Two channel types:
  session:{chat_session_id}      — session-level events (collaboration_started)
  collaboration:{collab_id}      — per-collaboration events (phase_started, blackboard_entry, ...)
"""

import json
import logging
import time
from typing import Generator, Optional

import redis

from app.core.config import settings

logger = logging.getLogger(__name__)

_pool: Optional[redis.ConnectionPool] = None


def _get_pool() -> redis.ConnectionPool:
    global _pool
    if _pool is None:
        _pool = redis.ConnectionPool.from_url(
            settings.REDIS_URL,
            decode_responses=True,
            max_connections=20,
        )
    return _pool


def _get_redis() -> redis.Redis:
    return redis.Redis(connection_pool=_get_pool())


def publish_event(collaboration_id: str, event_type: str, payload: dict) -> None:
    """Publish a per-collaboration event to Redis pub/sub."""
    channel = f"collaboration:{collaboration_id}"
    message = json.dumps({
        "event_type": event_type,
        "payload": payload,
        "timestamp": time.time(),
    })
    try:
        r = _get_redis()
        r.publish(channel, message)
    except Exception as e:
        logger.warning("Redis publish failed (collaboration %s): %s", collaboration_id, e)


def publish_session_event(chat_session_id: str, event_type: str, payload: dict) -> None:
    """Publish a session-level event (e.g. collaboration_started) to Redis pub/sub."""
    channel = f"session:{chat_session_id}"
    message = json.dumps({
        "event_type": event_type,
        "payload": payload,
        "timestamp": time.time(),
    })
    try:
        r = _get_redis()
        r.publish(channel, message)
    except Exception as e:
        logger.warning("Redis publish failed (session %s): %s", chat_session_id, e)


def subscribe_collaboration(collaboration_id: str) -> Generator[str, None, None]:
    """SSE generator for collaboration events via Redis pub/sub.

    Yields Server-Sent Events strings. Reconnects on failure (up to 3 attempts).
    """
    channel = f"collaboration:{collaboration_id}"
    attempts = 0
    max_attempts = 3
    heartbeat_interval = 15  # seconds

    while attempts < max_attempts:
        try:
            r = redis.Redis.from_url(settings.REDIS_URL, decode_responses=True)
            pubsub = r.pubsub()
            pubsub.subscribe(channel)
            last_heartbeat = time.time()

            for message in pubsub.listen():
                if message["type"] == "message":
                    attempts = 0  # reset on successful message
                    yield f"data: {message['data']}\n\n"

                    # Check if collaboration is done — close stream
                    try:
                        data = json.loads(message["data"])
                        if data.get("event_type") == "collaboration_completed":
                            pubsub.unsubscribe(channel)
                            return
                    except Exception:
                        pass

                # Heartbeat to keep connection alive through proxies
                if time.time() - last_heartbeat > heartbeat_interval:
                    yield ": heartbeat\n\n"
                    last_heartbeat = time.time()

        except Exception as e:
            attempts += 1
            logger.warning("Redis subscription error (attempt %d/%d): %s", attempts, max_attempts, e)
            if attempts < max_attempts:
                time.sleep(1)
            else:
                yield f"data: {json.dumps({'event_type': 'error', 'payload': {'detail': 'Stream connection lost'}})}\n\n"


def subscribe_session(chat_session_id: str) -> Generator[str, None, None]:
    """SSE generator for session-level events (collaboration_started, etc.)."""
    channel = f"session:{chat_session_id}"
    attempts = 0
    max_attempts = 3
    heartbeat_interval = 15

    while attempts < max_attempts:
        try:
            r = redis.Redis.from_url(settings.REDIS_URL, decode_responses=True)
            pubsub = r.pubsub()
            pubsub.subscribe(channel)
            last_heartbeat = time.time()

            for message in pubsub.listen():
                if message["type"] == "message":
                    attempts = 0
                    yield f"data: {message['data']}\n\n"

                if time.time() - last_heartbeat > heartbeat_interval:
                    yield ": heartbeat\n\n"
                    last_heartbeat = time.time()

        except Exception as e:
            attempts += 1
            logger.warning("Redis session subscription error (attempt %d/%d): %s", attempts, max_attempts, e)
            if attempts < max_attempts:
                time.sleep(1)
            else:
                yield f"data: {json.dumps({'event_type': 'error', 'payload': {'detail': 'Stream connection lost'}})}\n\n"
```

- [ ] **Step 7: Run tests to verify they pass**

```bash
cd apps/api && pytest tests/test_collaboration_events.py -v
```

Expected: 2 PASSED.

- [ ] **Step 8: Commit**

```bash
git add apps/api/requirements.txt apps/api/app/core/config.py apps/api/app/services/collaboration_events.py helm/values/agentprovision-api-local.yaml apps/api/tests/test_collaboration_events.py
git commit -m "feat: add Redis pub/sub client and collaboration events service"
```

---

## Task 6: Fix coalition_activities.py — Bugs + New Activities

**Files:**
- Modify: `apps/api/app/workflows/activities/coalition_activities.py`
- Test: `apps/api/tests/test_coalition_activities.py`

- [ ] **Step 1: Write failing tests**

Create `apps/api/tests/test_coalition_activities.py`:

```python
import os
os.environ["TESTING"] = "True"

import pytest
from unittest.mock import MagicMock, patch, AsyncMock


def test_select_template_returns_underscored_pattern():
    """Pattern names must use underscores to match CollaborationPattern enum."""
    # The bug: old code returned "propose-critique-revise" (hyphens)
    # Fix: must return "propose_critique_revise" (underscores)
    from app.workflows.activities.coalition_activities import _infer_pattern
    assert "_" in _infer_pattern("research competitors")
    assert "-" not in _infer_pattern("research competitors")


def test_incident_keywords_route_to_incident_investigation():
    from app.workflows.activities.coalition_activities import _infer_pattern
    assert _infer_pattern("investigate the incident in prod") == "incident_investigation"
    assert _infer_pattern("service outage detected") == "incident_investigation"
    assert _infer_pattern("pods are crash-looping") == "incident_investigation"
    assert _infer_pattern("pricing alert on SKUs") == "incident_investigation"


def test_infer_pattern_research_returns_research_synthesize():
    from app.workflows.activities.coalition_activities import _infer_pattern
    assert _infer_pattern("research the market") == "research_synthesize"


def test_infer_pattern_deploy_returns_plan_verify():
    from app.workflows.activities.coalition_activities import _infer_pattern
    assert _infer_pattern("deploy this fix") == "plan_verify"


def test_infer_pattern_default_returns_propose_critique_revise():
    from app.workflows.activities.coalition_activities import _infer_pattern
    assert _infer_pattern("write a poem") == "propose_critique_revise"
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd apps/api && pytest tests/test_coalition_activities.py -v
```

Expected: FAIL — `_infer_pattern` not defined.

- [ ] **Step 3: Refactor coalition_activities.py**

Replace `apps/api/app/workflows/activities/coalition_activities.py` entirely:

```python
"""Activities for CoalitionWorkflow."""
import json
import logging
from typing import Optional
from uuid import UUID, uuid4
from temporalio import activity

from app.db.session import SessionLocal
from app.services import blackboard_service
from app.schemas.blackboard import BlackboardCreate, BlackboardEntryInDB
from app.schemas.collaboration import CollaborationSessionCreate, CollaborationPattern

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _infer_pattern(task_lower: str) -> str:
    """Infer collaboration pattern from task description. Returns underscore format."""
    if any(k in task_lower for k in ["incident", "investigate", "outage", "degraded", "crash", "alert"]):
        return "incident_investigation"
    if any(k in task_lower for k in ["research", "market", "competitor"]):
        return "research_synthesize"
    if any(k in task_lower for k in ["deploy", "fix", "implement"]):
        return "plan_verify"
    return "propose_critique_revise"


def _required_roles_for_pattern(pattern: str) -> list:
    roles_map = {
        "incident_investigation": ["triage_agent", "investigator", "analyst", "commander"],
        "research_synthesize": ["researcher", "synthesizer"],
        "plan_verify": ["planner", "verifier"],
        "propose_critique_revise": ["planner", "critic"],
    }
    return roles_map.get(pattern, ["planner", "critic"])


def _build_blackboard_context(entries: list) -> str:
    """Format blackboard entries as readable context for the next agent."""
    if not entries:
        return "No prior contributions."
    lines = []
    for e in entries:
        entry_dict = e if isinstance(e, dict) else {
            "author_agent_slug": e.author_agent_slug,
            "author_role": e.author_role,
            "entry_type": e.entry_type,
            "content": e.content,
            "confidence": e.confidence,
            "board_version": e.board_version,
        }
        lines.append(
            f"[v{entry_dict['board_version']}] {entry_dict['author_agent_slug']} "
            f"({entry_dict['author_role']}/{entry_dict['entry_type']}, "
            f"confidence={entry_dict['confidence']:.2f}):\n{entry_dict['content']}"
        )
    return "\n\n---\n\n".join(lines)


def _build_phase_prompt(
    phase: str,
    agent_role: str,
    task_description: str,
    blackboard_context: str,
    agent_persona: str = "",
) -> str:
    """Build the CLI prompt for a collaboration phase."""
    role_instructions = {
        "triage_agent": (
            "Your job is to triage this incident. Classify the severity (P1/P2/P3), "
            "identify all affected systems using the knowledge graph context, and scope the blast radius. "
            "Be concise and structured."
        ),
        "investigator": (
            "Your job is to investigate the root data and timeline. "
            "Pull all relevant observations, correlate events in chronological order, "
            "and identify the most likely change that introduced the problem. "
            "Reference specific evidence from the blackboard."
        ),
        "analyst": (
            "Your job is to confirm and analyze the root cause. "
            "Validate the investigator's findings with quantitative reasoning. "
            "Calculate impact (how many records, how much revenue, which regions). "
            "If you disagree with any prior finding, say so explicitly with evidence."
        ),
        "commander": (
            "Your job is to synthesize a clear action plan. "
            "Provide: (1) immediate remediation steps, (2) validation steps, (3) preventive measures. "
            "Reference all prior blackboard entries. Be specific and actionable."
        ),
    }
    instruction = role_instructions.get(agent_role, f"Contribute as {agent_role} for the {phase} phase.")

    return f"""{agent_persona}

## Incident Investigation — {phase.upper()} Phase

You are the **{agent_role}**. {instruction}

## Task

{task_description}

## Blackboard (Prior Agent Contributions)

{blackboard_context}

## Your Contribution

Write your {phase} contribution below. Be thorough and structured.
"""


# ---------------------------------------------------------------------------
# Temporal activities
# ---------------------------------------------------------------------------

@activity.defn
async def select_coalition_template(tenant_id: str, chat_session_id: str, task_description: str) -> dict:
    """Select optimal coalition template and resolve roles from the session's AgentKit."""
    from app.models.chat import ChatSession
    from app.models.agent import Agent

    db = SessionLocal()
    try:
        task_lower = task_description.lower()
        pattern = _infer_pattern(task_lower)
        required_roles = _required_roles_for_pattern(pattern)

        def _slug(name): return name.lower().replace(" ", "-")

        from app.services.agent_identity import resolve_primary_agent_slug
        primary_slug = resolve_primary_agent_slug(db, UUID(tenant_id))

        agents = db.query(Agent).filter(Agent.tenant_id == UUID(tenant_id)).all()
        role_agent_map = {}

        for role in required_roles:
            match = next((a for a in agents if a.role == role), None)
            if not match:
                match = next((a for a in agents if role in a.name.lower()), None)
            role_agent_map[role] = _slug(match.name) if match else primary_slug

        return {
            "template_id": None,
            "pattern": pattern,
            "roles": role_agent_map,
            "name": f"Dynamic {pattern.replace('_', ' ').title()} Team",
        }
    finally:
        db.close()


@activity.defn
async def initialize_collaboration(tenant_id: str, chat_session_id: str, template: dict) -> dict:
    """Create the Shared Blackboard and start the Collaboration Session."""
    from app.services import collaboration_service
    from app.services.collaboration_events import publish_session_event

    db = SessionLocal()
    try:
        board_in = BlackboardCreate(
            title=f"Task: {template['name']}",
            chat_session_id=UUID(chat_session_id),
        )
        board = blackboard_service.create_blackboard(db, UUID(tenant_id), board_in)

        collab_in = CollaborationSessionCreate(
            blackboard_id=board.id,
            pattern=template["pattern"],
            role_assignments=template["roles"],
        )
        session = collaboration_service.create_session(db, UUID(tenant_id), collab_in)

        # Publish session-level event so the frontend session stream picks it up
        agents_list = [
            {"slug": slug, "role": role}
            for role, slug in template["roles"].items()
        ]
        publish_session_event(chat_session_id, "collaboration_started", {
            "collaboration_id": str(session.id),
            "pattern": template["pattern"],
            "agents": agents_list,
            "blackboard_id": str(board.id),
        })

        return {
            "blackboard_id": str(board.id),
            "collaboration_id": str(session.id),
            "max_rounds": session.max_rounds,
        }
    finally:
        db.close()


@activity.defn
async def prepare_collaboration_step(
    tenant_id: str,
    collaboration_id: str,
    round_index: int,
) -> dict:
    """Read blackboard, build phase prompt, resolve CLI platform.

    Returns a dict suitable for constructing ChatCliInput in the workflow.
    """
    from app.models.collaboration import CollaborationSession
    from app.models.agent import Agent
    from app.services.rl_routing import get_best_platform
    from app.models.tenant_features import TenantFeatures
    from app.services.collaboration_events import publish_event

    db = SessionLocal()
    try:
        session = db.query(CollaborationSession).filter(
            CollaborationSession.id == UUID(collaboration_id),
            CollaborationSession.tenant_id == UUID(tenant_id),
        ).first()
        if not session:
            raise ValueError(f"CollaborationSession {collaboration_id} not found")

        current_phase = session.current_phase
        role_assignments = session.role_assignments or {}

        # Find agent slug for current phase's required role
        from app.schemas.collaboration import PHASE_REQUIRED_ROLES
        required_roles = PHASE_REQUIRED_ROLES.get(current_phase, [])
        agent_slug = None
        agent_role = required_roles[0] if required_roles else "contributor"
        for role in required_roles:
            if role in role_assignments:
                agent_slug = role_assignments[role]
                agent_role = role
                break

        # Get agent persona if available
        agent_persona = ""
        if agent_slug:
            agent = db.query(Agent).filter(
                Agent.tenant_id == UUID(tenant_id),
                Agent.name.ilike(agent_slug.replace("-", " ") + "%"),
            ).first()
            if agent and agent.personality:
                agent_persona = f"You are {agent.name}. {agent.personality.get('description', '')}"

        # Read all blackboard entries for context
        entries = blackboard_service.get_active_entries(db, UUID(tenant_id), session.blackboard_id)
        blackboard_context = _build_blackboard_context(entries)

        # Get original task description from the first entry or session title
        board = blackboard_service.get_blackboard(db, UUID(tenant_id), session.blackboard_id)
        task_description = board.title.replace("Task: Dynamic ", "").replace(" Team", "")

        # Resolve CLI platform via RL routing or tenant default
        try:
            platform = get_best_platform(db, UUID(tenant_id), decision_point="collaboration_step")
        except Exception:
            features = db.query(TenantFeatures).filter(
                TenantFeatures.tenant_id == UUID(tenant_id)
            ).first()
            platform = (features.default_cli_platform if features else None) or "gemini"

        prompt = _build_phase_prompt(
            phase=current_phase,
            agent_role=agent_role,
            task_description=task_description,
            blackboard_context=blackboard_context,
            agent_persona=agent_persona,
        )

        # Publish phase_started event
        publish_event(collaboration_id, "phase_started", {
            "phase": current_phase,
            "agent_slug": agent_slug or "unknown",
            "agent_role": agent_role,
            "round": round_index + 1,
        })

        return {
            "platform": platform,
            "message": prompt,
            "tenant_id": tenant_id,
            "instruction_md_content": agent_persona,
            "collaboration_id": collaboration_id,
            "agent_slug": agent_slug or "unknown",
            "agent_role": agent_role,
            "current_phase": current_phase,
        }
    finally:
        db.close()


@activity.defn
async def record_collaboration_step(
    tenant_id: str,
    collaboration_id: str,
    response_text: str,
    agent_slug: str,
    agent_role: str,
    current_phase: str,
) -> dict:
    """Write CLI response to blackboard, advance phase, publish Redis events, score async."""
    from app.services import collaboration_service
    from app.services.collaboration_events import publish_event
    from app.services.auto_quality_scorer import score_response_async

    db = SessionLocal()
    try:
        result = collaboration_service.advance_phase(
            db,
            UUID(tenant_id),
            UUID(collaboration_id),
            agent_slug=agent_slug,
            contribution=response_text,
            confidence=0.8,
            agrees_with_previous=True,  # Always True for incident_investigation phases
            # Note on outcome: advance_phase calls _find_last_proposal() to set session.outcome.
            # For incident_investigation (no propose/revise entries), _find_last_proposal returns None
            # and advance_phase falls back to using the contribution text as session.outcome.
            # This is correct — the Commander's synthesis becomes the final outcome. No extra logic needed.
        )
        if not result:
            raise ValueError(f"advance_phase failed for {collaboration_id}")

        # Publish blackboard_entry event
        publish_event(collaboration_id, "blackboard_entry", {
            "entry_id": result.get("entry_id"),
            "entry_type": current_phase,
            "author_slug": agent_slug,
            "author_role": agent_role,
            "content_preview": response_text[:200],
            "content_full": response_text,
            "confidence": 0.8,
            "board_version": result.get("board_version"),
        })

        # Publish phase_completed event
        publish_event(collaboration_id, "phase_completed", {
            "phase": current_phase,
            "agent_slug": agent_slug,
            "entry_id": result.get("entry_id"),
            "board_version": result.get("board_version"),
        })

        # Async quality scoring — fire and forget
        try:
            score_response_async(
                tenant_id=tenant_id,
                response_text=response_text,
                decision_point="collaboration_step",
                metadata={"phase": current_phase, "agent_slug": agent_slug},
            )
        except Exception as e:
            logger.debug("Quality scoring skipped: %s", e)

        return {
            "consensus_reached": result.get("status") == "completed",
            "phase_completed": current_phase,
            "board_version": result.get("board_version"),
        }
    finally:
        db.close()


@activity.defn
async def finalize_collaboration(tenant_id: str, collaboration_id: str) -> str:
    """Conclude the collaboration and publish the final report."""
    from app.models.collaboration import CollaborationSession
    from app.services.collaboration_events import publish_event, publish_session_event
    from app.models.chat import ChatSession

    db = SessionLocal()
    try:
        session = db.query(CollaborationSession).filter(
            CollaborationSession.id == UUID(collaboration_id),
            CollaborationSession.tenant_id == UUID(tenant_id),
        ).first()

        final_report = session.outcome or "Collaboration complete. See blackboard for full agent reasoning."

        # Get chat_session_id from blackboard
        board = blackboard_service.get_blackboard(db, UUID(tenant_id), session.blackboard_id)
        chat_session_id = str(board.chat_session_id) if board.chat_session_id else None

        publish_event(collaboration_id, "collaboration_completed", {
            "collaboration_id": collaboration_id,
            "consensus": session.consensus_reached or "yes",
            "rounds": session.rounds_completed,
            "final_report": final_report,
        })

        if chat_session_id:
            publish_session_event(chat_session_id, "collaboration_completed", {
                "collaboration_id": collaboration_id,
                "final_report": final_report,
            })

        return final_report
    finally:
        db.close()
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
cd apps/api && pytest tests/test_coalition_activities.py -v
```

Expected: 5 PASSED.

- [ ] **Step 5: Commit**

```bash
git add apps/api/app/workflows/activities/coalition_activities.py apps/api/tests/test_coalition_activities.py
git commit -m "feat: replace coalition activity stubs with real ChatCliWorkflow dispatch, fix pattern name bug, add incident keywords"
```

---

## Task 7: Refactor CoalitionWorkflow + Update orchestration_worker.py

**Files:**
- Modify: `apps/api/app/workflows/coalition_workflow.py`
- Modify: `apps/api/app/workers/orchestration_worker.py`

**Background:** `ChatCliWorkflow` runs in the code-worker pod — a separate service. The orchestration worker cannot `import` from `apps/code-worker/` at runtime. Use the string-name reference `"ChatCliWorkflow"` for child workflow dispatch, and define a local `ChatCliInput` dataclass matching the code-worker signature (fields must match exactly for Temporal serialization).

- [ ] **Step 1: Update CoalitionWorkflow.run() with prepare → child → record loop**

**Cross-pod dispatch pattern:** This file follows the same pattern as `apps/api/app/workflows/dynamic_executor.py:145-161` — use a fresh Temporal client (`_tc.start_workflow`) rather than `execute_child_workflow`. This is the only reliable cross-pod approach; the orchestration worker cannot import code-worker types. Pass `ChatCliInput` fields as a plain dict; the result comes back as a dict (Temporal default for callers that don't have the result type registered).

Replace `apps/api/app/workflows/coalition_workflow.py`:

```python
"""CoalitionWorkflow — manages structured multi-agent collaboration via ChatCliWorkflow."""
import os
from datetime import timedelta
from temporalio import workflow
from temporalio.common import RetryPolicy

with workflow.unsafe.imports_passed_through():
    from app.workflows.activities.coalition_activities import (
        select_coalition_template,
        initialize_collaboration,
        prepare_collaboration_step,
        record_collaboration_step,
        finalize_collaboration,
    )


@workflow.defn
class CoalitionWorkflow:
    @workflow.run
    async def run(self, tenant_id: str, chat_session_id: str, task_description: str) -> dict:
        retry = RetryPolicy(maximum_attempts=3)
        activity_timeout = timedelta(seconds=60)
        cli_timeout = timedelta(minutes=5)

        # 1. Select the best team shape for this task
        template = await workflow.execute_activity(
            select_coalition_template,
            args=[tenant_id, chat_session_id, task_description],
            start_to_close_timeout=activity_timeout,
            retry_policy=retry,
        )

        # 2. Initialize Shared Blackboard and Collaboration Session
        session_info = await workflow.execute_activity(
            initialize_collaboration,
            args=[tenant_id, chat_session_id, template],
            start_to_close_timeout=activity_timeout,
            retry_policy=retry,
        )

        collaboration_id = session_info["collaboration_id"]
        results = []

        # 3. Execute collaboration phases: prepare → ChatCliWorkflow → record
        for i in range(session_info["max_rounds"]):
            # 3a. Prepare: read blackboard, build step input dict
            step_input = await workflow.execute_activity(
                prepare_collaboration_step,
                args=[tenant_id, collaboration_id, i],
                start_to_close_timeout=activity_timeout,
                retry_policy=retry,
            )

            # 3b. Execute: dispatch ChatCliWorkflow on agentprovision-code queue via Temporal client.
            # Pattern mirrors dynamic_executor.py:145-161 — fresh client, string workflow name, dict input.
            # The code-worker pod registers ChatCliWorkflow; orchestration worker cannot import it directly.
            with workflow.unsafe.imports_passed_through():
                from temporalio.client import Client as _TClient
            _tc = await _TClient.connect(
                os.environ.get("TEMPORAL_ADDRESS", "temporal:7233")
            )
            cli_handle = await _tc.start_workflow(
                "ChatCliWorkflow",
                {
                    "platform": step_input["platform"],
                    "message": step_input["message"],
                    "tenant_id": tenant_id,
                    "instruction_md_content": step_input["instruction_md_content"],
                },
                id=f"coalition-{collaboration_id}-step-{i}",
                task_queue="agentprovision-code",
                execution_timeout=cli_timeout,
            )
            cli_result = await cli_handle.result()
            # cli_result is a dict (Temporal default for callers without the type registered)
            # Keys: response_text, success, error, metadata
            if not isinstance(cli_result, dict):
                cli_result = {"response_text": str(cli_result), "success": True}

            # 3c. Record: write to blackboard + publish Redis events + async scoring
            response_text = (
                cli_result.get("response_text", "")
                if cli_result.get("success")
                else f"[CLI error: {cli_result.get('error', 'unknown')}]"
            )
            step_result = await workflow.execute_activity(
                record_collaboration_step,
                args=[
                    tenant_id,
                    collaboration_id,
                    response_text,
                    step_input["agent_slug"],
                    step_input["agent_role"],
                    step_input["current_phase"],
                ],
                start_to_close_timeout=activity_timeout,
                retry_policy=retry,
            )
            results.append(step_result)

            if step_result.get("consensus_reached"):
                break

        # 4. Finalize and report back to the chat session
        final_report = await workflow.execute_activity(
            finalize_collaboration,
            args=[tenant_id, collaboration_id],
            start_to_close_timeout=activity_timeout,
            retry_policy=retry,
        )

        return {
            "status": "completed",
            "collaboration_id": collaboration_id,
            "final_report": final_report,
            "rounds": len(results),
        }
```

- [ ] **Step 2: Update orchestration_worker.py — replace old activity import/registration**

`execute_collaboration_step` is removed in Task 6. The worker must register the two replacement activities.

In `apps/api/app/workers/orchestration_worker.py`, find:

```python
from app.workflows.activities.coalition_activities import (
    select_coalition_template,
    initialize_collaboration,
    execute_collaboration_step,
    finalize_collaboration,
)
```

Replace with:

```python
from app.workflows.activities.coalition_activities import (
    select_coalition_template,
    initialize_collaboration,
    prepare_collaboration_step,
    record_collaboration_step,
    finalize_collaboration,
)
```

Then find the activity registration list (around line 347–349) and replace:

```python
            select_coalition_template,
            initialize_collaboration,
            execute_collaboration_step,
            finalize_collaboration,
```

With:

```python
            select_coalition_template,
            initialize_collaboration,
            prepare_collaboration_step,
            record_collaboration_step,
            finalize_collaboration,
```

- [ ] **Step 3: Verify registrations**

```bash
grep -n "execute_collaboration_step\|prepare_collaboration_step\|record_collaboration_step" apps/api/app/workers/orchestration_worker.py
```

Expected: no hits for `execute_collaboration_step`, two hits each for the new names (import + registration).

- [ ] **Step 4: Commit**

```bash
git add apps/api/app/workflows/coalition_workflow.py apps/api/app/workers/orchestration_worker.py
git commit -m "feat: refactor CoalitionWorkflow to prepare → ChatCliWorkflow child → record pattern"
```

---

## Task 8: Session Events SSE Endpoint

**Files:**
- Modify: `apps/api/app/api/v1/chat.py`
- Test: Manual (SSE endpoints are hard to unit test — verify via curl after deploy)

- [ ] **Step 1: Add session events SSE endpoint to chat.py**

In `apps/api/app/api/v1/chat.py`, add after the existing imports, a new route at the bottom of the file:

```python
@router.get("/sessions/{session_id}/events")
def session_events_stream(
    session_id: uuid.UUID,
    *,
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(deps.get_current_active_user),
):
    """Long-lived SSE stream for session-level events (collaboration_started, etc.).

    The frontend opens this once per chat session and keeps it open.
    Coalition activities publish collaboration_started to Redis session:{session_id}.
    """
    session = chat_service.get_session(db, session_id=session_id, tenant_id=current_user.tenant_id)
    if not session:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Chat session not found")

    from app.services.collaboration_events import subscribe_session

    return StreamingResponse(
        subscribe_session(str(session_id)),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )
```

- [ ] **Step 2: Also add list-collaborations-by-session endpoint to chat.py**

```python
@router.get("/sessions/{session_id}/collaborations")
def list_session_collaborations(
    session_id: uuid.UUID,
    *,
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(deps.get_current_active_user),
):
    """List all collaborations linked to this chat session."""
    from app.models.blackboard import Blackboard
    from app.models.collaboration import CollaborationSession
    from app.schemas.collaboration import CollaborationSessionInDB

    boards = db.query(Blackboard).filter(
        Blackboard.tenant_id == current_user.tenant_id,
        Blackboard.chat_session_id == session_id,
    ).all()

    board_ids = [b.id for b in boards]
    if not board_ids:
        return []

    sessions = db.query(CollaborationSession).filter(
        CollaborationSession.tenant_id == current_user.tenant_id,
        CollaborationSession.blackboard_id.in_(board_ids),
    ).order_by(CollaborationSession.created_at.desc()).all()

    return [CollaborationSessionInDB.model_validate(s) for s in sessions]
```

- [ ] **Step 3: Commit**

```bash
git add apps/api/app/api/v1/chat.py
git commit -m "feat: add session events SSE stream and list-collaborations-by-session endpoints"
```

---

## Task 9: New Collaboration Endpoints

**Files:**
- Modify: `apps/api/app/api/v1/collaborations.py`
- Test: Manual via curl after deploy

- [ ] **Step 1: Add /stream, /detail, and /trigger endpoints**

In `apps/api/app/api/v1/collaborations.py`, add after the existing imports and existing routes:

```python
# Add to imports at top:
import json
from fastapi.responses import StreamingResponse
from app.models.blackboard import Blackboard, BlackboardEntry
from app.schemas.blackboard import BlackboardEntryInDB, BlackboardInDB


@router.get("/{session_id}/stream")
def collaboration_stream(
    session_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """SSE stream for a specific collaboration — blackboard entries, phase transitions.

    On connect: replays all existing blackboard entries from Postgres (catch-up),
    then streams live Redis events. Frontend opens this after receiving collaboration_started.
    """
    from app.services.collaboration_events import subscribe_collaboration
    from app.models.blackboard import Blackboard, BlackboardEntry

    collab = collaboration_service.get_session(db, current_user.tenant_id, session_id)
    if not collab:
        raise HTTPException(status_code=404, detail="Collaboration session not found")

    def _catch_up_then_live():
        # Catch-up: emit all existing entries from Postgres
        entries = (
            db.query(BlackboardEntry)
            .filter(BlackboardEntry.blackboard_id == collab.blackboard_id)
            .order_by(BlackboardEntry.board_version.asc())
            .all()
        )
        for entry in entries:
            event_data = json.dumps({
                "event_type": "blackboard_entry",
                "payload": {
                    "entry_id": str(entry.id),
                    "entry_type": entry.entry_type,
                    "author_slug": entry.author_agent_slug,
                    "author_role": entry.author_role,
                    "content_preview": (entry.content or "")[:200],
                    "content_full": entry.content,
                    "confidence": entry.confidence,
                    "board_version": entry.board_version,
                },
                "timestamp": entry.created_at.timestamp() if entry.created_at else 0,
            })
            yield f"data: {event_data}\n\n"

        # Then live stream from Redis
        yield from subscribe_collaboration(str(session_id))

    return StreamingResponse(
        _catch_up_then_live(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.get("/{session_id}/detail")
def collaboration_detail(
    session_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Full collaboration detail: session + all blackboard entries ordered by board_version.

    Powers the replay/playback UI and post-demo walkthrough.
    Distinct from GET /{id} which returns only CollaborationSessionInDB.
    """
    collab = collaboration_service.get_session(db, current_user.tenant_id, session_id)
    if not collab:
        raise HTTPException(status_code=404, detail="Collaboration session not found")

    entries = (
        db.query(BlackboardEntry)
        .filter(BlackboardEntry.blackboard_id == collab.blackboard_id)
        .order_by(BlackboardEntry.board_version.asc())
        .all()
    )
    board = db.query(Blackboard).filter(Blackboard.id == collab.blackboard_id).first()

    return {
        "session": CollaborationSessionInDB.model_validate(collab),
        "blackboard": BlackboardInDB.model_validate(board) if board else None,
        "entries": [BlackboardEntryInDB.model_validate(e) for e in entries],
        "entry_count": len(entries),
        "phases_completed": collab.phase_index,
        "rounds_completed": collab.rounds_completed,
    }


# Add to the imports block at the top of collaborations.py alongside other pydantic imports:
# from pydantic import BaseModel


class CollaborationTriggerRequest(BaseModel):
    chat_session_id: uuid.UUID
    task_description: str
    pattern: Optional[str] = None
    role_overrides: Optional[dict] = None


@router.post("/trigger", status_code=202)
def trigger_collaboration(
    request: CollaborationTriggerRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Manually trigger a CoalitionWorkflow — bypasses intent detection.

    Returns 202 Accepted immediately. The workflow runs asynchronously.
    Use GET /sessions/{id}/events to receive the collaboration_started SSE event.
    """
    from app.services.agent_router import dispatch_coalition

    dispatch_coalition(
        tenant_id=current_user.tenant_id,
        chat_session_id=str(request.chat_session_id),
        task_description=request.task_description,
    )

    return {
        "status": "dispatched",
        "chat_session_id": str(request.chat_session_id),
        "task_description": request.task_description,
        "message": "CoalitionWorkflow dispatched. Subscribe to GET /chat/sessions/{id}/events for collaboration_started.",
    }
```

- [ ] **Step 2: Commit**

```bash
git add apps/api/app/api/v1/collaborations.py
git commit -m "feat: add /stream, /detail, /trigger endpoints to collaborations API"
```

---

## Task 10: CollaborationPanel Frontend Component

**Files:**
- Create: `apps/web/src/components/CollaborationPanel.js`
- Create: `apps/web/src/components/CollaborationPanel.css`

- [ ] **Step 1: Create CollaborationPanel.css**

Create `apps/web/src/components/CollaborationPanel.css`:

```css
/* CollaborationPanel — Ocean Theme */
.collaboration-panel {
  display: flex;
  flex-direction: column;
  height: 100%;
  background: rgba(10, 25, 47, 0.85);
  backdrop-filter: blur(20px);
  border-left: 1px solid rgba(100, 180, 255, 0.15);
  color: #e0f0ff;
  font-family: inherit;
  min-width: 380px;
  max-width: 480px;
}

.collaboration-panel__header {
  padding: 16px 20px 12px;
  border-bottom: 1px solid rgba(100, 180, 255, 0.1);
  display: flex;
  align-items: center;
  justify-content: space-between;
}

.collaboration-panel__title {
  font-size: 14px;
  font-weight: 600;
  color: #64b4ff;
  text-transform: uppercase;
  letter-spacing: 0.5px;
  margin: 0;
}

.collaboration-panel__mode-badge {
  font-size: 11px;
  padding: 2px 8px;
  border-radius: 10px;
  background: rgba(100, 180, 255, 0.1);
  color: #64b4ff;
  border: 1px solid rgba(100, 180, 255, 0.2);
}

/* Phase Timeline */
.phase-timeline {
  padding: 16px 20px;
  border-bottom: 1px solid rgba(100, 180, 255, 0.1);
  display: flex;
  gap: 0;
  align-items: center;
}

.phase-step {
  display: flex;
  flex-direction: column;
  align-items: center;
  flex: 1;
  position: relative;
}

.phase-step:not(:last-child)::after {
  content: '';
  position: absolute;
  right: -50%;
  top: 14px;
  width: 100%;
  height: 2px;
  background: rgba(100, 180, 255, 0.15);
  z-index: 0;
}

.phase-step.completed::after {
  background: rgba(100, 220, 150, 0.5);
}

.phase-step__dot {
  width: 28px;
  height: 28px;
  border-radius: 50%;
  border: 2px solid rgba(100, 180, 255, 0.3);
  background: rgba(10, 25, 47, 0.9);
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 11px;
  z-index: 1;
  position: relative;
}

.phase-step.active .phase-step__dot {
  border-color: #64b4ff;
  box-shadow: 0 0 8px rgba(100, 180, 255, 0.5);
  animation: pulse 1.5s infinite;
}

.phase-step.completed .phase-step__dot {
  border-color: #64dc96;
  background: rgba(100, 220, 150, 0.15);
  color: #64dc96;
}

@keyframes pulse {
  0%, 100% { box-shadow: 0 0 4px rgba(100, 180, 255, 0.4); }
  50% { box-shadow: 0 0 12px rgba(100, 180, 255, 0.8); }
}

.phase-step__label {
  font-size: 10px;
  color: rgba(224, 240, 255, 0.5);
  margin-top: 4px;
  text-transform: uppercase;
  letter-spacing: 0.3px;
}

.phase-step.active .phase-step__label {
  color: #64b4ff;
}

.phase-step.completed .phase-step__label {
  color: #64dc96;
}

.phase-step__duration {
  font-size: 9px;
  color: rgba(224, 240, 255, 0.3);
  margin-top: 1px;
}

/* Blackboard Feed */
.blackboard-feed {
  flex: 1;
  overflow-y: auto;
  padding: 12px 16px;
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.blackboard-feed::-webkit-scrollbar {
  width: 4px;
}
.blackboard-feed::-webkit-scrollbar-track { background: transparent; }
.blackboard-feed::-webkit-scrollbar-thumb {
  background: rgba(100, 180, 255, 0.2);
  border-radius: 2px;
}

.blackboard-entry {
  background: rgba(255, 255, 255, 0.04);
  border: 1px solid rgba(100, 180, 255, 0.1);
  border-radius: 8px;
  padding: 12px 14px;
  transition: transform 0.15s ease;
  animation: slide-in 0.2s ease;
}

.blackboard-entry:hover {
  transform: translateY(-2px);
  border-color: rgba(100, 180, 255, 0.2);
}

@keyframes slide-in {
  from { opacity: 0; transform: translateY(6px); }
  to { opacity: 1; transform: translateY(0); }
}

.blackboard-entry__header {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 8px;
}

.blackboard-entry__agent {
  font-size: 12px;
  font-weight: 600;
  color: #64b4ff;
}

.blackboard-entry__role-badge {
  font-size: 10px;
  padding: 1px 6px;
  border-radius: 8px;
  text-transform: uppercase;
  letter-spacing: 0.3px;
}

.role-badge--researcher { background: rgba(100, 180, 255, 0.15); color: #64b4ff; }
.role-badge--critic { background: rgba(255, 160, 100, 0.15); color: #ffa064; }
.role-badge--synthesizer { background: rgba(100, 220, 150, 0.15); color: #64dc96; }
.role-badge--planner { background: rgba(200, 100, 255, 0.15); color: #c864ff; }

.blackboard-entry__type {
  font-size: 10px;
  color: rgba(224, 240, 255, 0.4);
  margin-left: auto;
  text-transform: uppercase;
}

.blackboard-entry__content {
  font-size: 13px;
  color: rgba(224, 240, 255, 0.85);
  line-height: 1.5;
  white-space: pre-wrap;
  word-break: break-word;
}

.blackboard-entry__content.collapsed {
  display: -webkit-box;
  -webkit-line-clamp: 4;
  -webkit-box-orient: vertical;
  overflow: hidden;
}

.blackboard-entry__expand {
  font-size: 11px;
  color: #64b4ff;
  background: none;
  border: none;
  cursor: pointer;
  padding: 4px 0 0;
  text-decoration: underline;
}

.confidence-bar {
  height: 3px;
  background: rgba(255,255,255,0.08);
  border-radius: 2px;
  margin-top: 8px;
  overflow: hidden;
}

.confidence-bar__fill {
  height: 100%;
  background: linear-gradient(90deg, #64b4ff, #64dc96);
  border-radius: 2px;
  transition: width 0.5s ease;
}

.blackboard-entry__meta {
  display: flex;
  gap: 8px;
  margin-top: 6px;
  font-size: 10px;
  color: rgba(224, 240, 255, 0.3);
}

/* Status Bar */
.collaboration-status-bar {
  padding: 10px 20px;
  border-top: 1px solid rgba(100, 180, 255, 0.1);
  display: flex;
  align-items: center;
  gap: 16px;
  font-size: 12px;
  color: rgba(224, 240, 255, 0.5);
}

.status-dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: #64b4ff;
}

.status-dot.active { animation: pulse 1.5s infinite; }
.status-dot.completed { background: #64dc96; animation: none; }

/* Replay Controls */
.replay-controls {
  padding: 10px 16px;
  border-top: 1px solid rgba(100, 180, 255, 0.1);
  display: flex;
  align-items: center;
  gap: 10px;
}

.replay-btn {
  padding: 5px 14px;
  border-radius: 6px;
  border: 1px solid rgba(100, 180, 255, 0.3);
  background: rgba(100, 180, 255, 0.08);
  color: #64b4ff;
  font-size: 12px;
  cursor: pointer;
  transition: all 0.15s;
}

.replay-btn:hover {
  background: rgba(100, 180, 255, 0.15);
  border-color: rgba(100, 180, 255, 0.5);
}

.replay-speed-select {
  background: rgba(10, 25, 47, 0.9);
  border: 1px solid rgba(100, 180, 255, 0.2);
  color: #e0f0ff;
  font-size: 12px;
  padding: 4px 8px;
  border-radius: 4px;
}
```

- [ ] **Step 2: Create CollaborationPanel.js**

Create `apps/web/src/components/CollaborationPanel.js`:

```jsx
import React, { useState, useEffect, useRef, useCallback } from 'react';
import './CollaborationPanel.css';

const PHASE_LABELS = {
  triage: 'Triage',
  investigate: 'Investigate',
  analyze: 'Analyze',
  command: 'Command',
  propose: 'Propose',
  critique: 'Critique',
  revise: 'Revise',
  verify: 'Verify',
  research: 'Research',
  synthesize: 'Synthesize',
};

function EntryCard({ entry, isHighlighted }) {
  const [expanded, setExpanded] = useState(false);
  const isLong = (entry.content_full || entry.content_preview || '').length > 300;

  return (
    <div className={`blackboard-entry${isHighlighted ? ' highlighted' : ''}`}>
      <div className="blackboard-entry__header">
        <span className="blackboard-entry__agent">{entry.author_slug}</span>
        <span className={`blackboard-entry__role-badge role-badge--${entry.author_role}`}>
          {entry.author_role}
        </span>
        <span className="blackboard-entry__type">{entry.entry_type}</span>
      </div>
      <div className={`blackboard-entry__content${isLong && !expanded ? ' collapsed' : ''}`}>
        {entry.content_full || entry.content_preview}
      </div>
      {isLong && (
        <button className="blackboard-entry__expand" onClick={() => setExpanded(!expanded)}>
          {expanded ? 'Show less' : 'Show more'}
        </button>
      )}
      <div className="confidence-bar">
        <div className="confidence-bar__fill" style={{ width: `${(entry.confidence || 0.7) * 100}%` }} />
      </div>
      <div className="blackboard-entry__meta">
        <span>v{entry.board_version}</span>
        {entry.timestamp && (
          <span>{new Date(entry.timestamp * 1000).toLocaleTimeString()}</span>
        )}
      </div>
    </div>
  );
}

/**
 * CollaborationPanel
 *
 * Props:
 *   collaborationId  — UUID of the active/completed collaboration
 *   phases           — array of phase names e.g. ['triage','investigate','analyze','command']
 *   apiBaseUrl       — e.g. '/api/v1'
 *   token            — JWT for auth headers
 *   isCompleted      — boolean, true = replay mode available
 */
export default function CollaborationPanel({ collaborationId, phases, apiBaseUrl, token, isCompleted }) {
  const [entries, setEntries] = useState([]);
  const [activePhase, setActivePhase] = useState(null);
  const [completedPhases, setCompletedPhases] = useState([]);
  const [status, setStatus] = useState(isCompleted ? 'completed' : 'active');
  const [elapsedSeconds, setElapsedSeconds] = useState(0);
  const [mode, setMode] = useState(isCompleted ? 'replay' : 'live');

  // Replay state
  const [replayIndex, setReplayIndex] = useState(-1);
  const [isReplaying, setIsReplaying] = useState(false);
  const [replaySpeed, setReplaySpeed] = useState(1);
  const allEntriesRef = useRef([]);

  const feedRef = useRef(null);
  const startTimeRef = useRef(Date.now());
  const eventSourceRef = useRef(null);

  // Live mode: open SSE stream using fetch (native EventSource cannot send JWT headers)
  useEffect(() => {
    if (mode !== 'live' || !collaborationId) return;

    const ctrl = new AbortController();
    eventSourceRef.current = ctrl;

    (async () => {
      try {
        const res = await fetch(`${apiBaseUrl}/collaborations/${collaborationId}/stream`, {
          headers: { Authorization: `Bearer ${token}` },
          signal: ctrl.signal,
        });
        if (!res.ok) return;
        const reader = res.body.getReader();
        const decoder = new TextDecoder();
        let buf = '';
        while (true) {
          const { done, value } = await reader.read();
          if (done) break;
          buf += decoder.decode(value, { stream: true });
          const lines = buf.split('\n');
          buf = lines.pop();
          for (const line of lines) {
            if (!line.startsWith('data: ')) continue;
            try { handleEvent(JSON.parse(line.slice(6))); } catch (_) {}
          }
        }
      } catch (e) {
        if (e.name !== 'AbortError') console.warn('[CollabPanel] SSE error', e);
      }
    })();

    return () => { ctrl.abort(); };
  }, [collaborationId, mode]);

  // Load full detail for replay mode
  useEffect(() => {
    if (mode !== 'replay' || !collaborationId) return;

    fetch(`${apiBaseUrl}/collaborations/${collaborationId}/detail`, {
      headers: { Authorization: `Bearer ${token}` },
    })
      .then(r => r.json())
      .then(data => {
        allEntriesRef.current = data.entries || [];
        // Reconstruct phase progression from entries
        const phases_seen = [...new Set(data.entries.map(e => e.entry_type))];
        setCompletedPhases(phases || []);
        setEntries([]);
        setReplayIndex(-1);
      });
  }, [collaborationId, mode]);

  function handleEvent(data) {
    switch (data.event_type) {
      case 'phase_started':
        setActivePhase(data.payload.phase);
        break;
      case 'blackboard_entry':
        setEntries(prev => [...prev, data.payload]);
        setTimeout(() => {
          feedRef.current?.scrollTo({ top: feedRef.current.scrollHeight, behavior: 'smooth' });
        }, 50);
        break;
      case 'phase_completed':
        setCompletedPhases(prev => [...new Set([...prev, data.payload.phase])]);
        break;
      case 'collaboration_completed':
        setStatus('completed');
        setMode('replay');
        break;
      default:
        break;
    }
  }

  // Elapsed timer for live mode
  useEffect(() => {
    if (status !== 'active') return;
    const t = setInterval(() => {
      setElapsedSeconds(Math.floor((Date.now() - startTimeRef.current) / 1000));
    }, 1000);
    return () => clearInterval(t);
  }, [status]);

  // Replay step-through
  const stepReplay = useCallback((dir) => {
    const all = allEntriesRef.current;
    const next = Math.max(-1, Math.min(all.length - 1, replayIndex + dir));
    setReplayIndex(next);
    setEntries(next < 0 ? [] : all.slice(0, next + 1));
  }, [replayIndex]);

  // Keyboard navigation for replay
  useEffect(() => {
    if (mode !== 'replay') return;
    const handler = (e) => {
      if (e.key === 'ArrowRight') stepReplay(1);
      if (e.key === 'ArrowLeft') stepReplay(-1);
    };
    window.addEventListener('keydown', handler);
    return () => window.removeEventListener('keydown', handler);
  }, [mode, stepReplay]);

  // Auto-replay
  useEffect(() => {
    if (!isReplaying) return;
    const all = allEntriesRef.current;
    if (replayIndex >= all.length - 1) { setIsReplaying(false); return; }

    const delay = replaySpeed === 1 ? 2000 : replaySpeed === 2 ? 1000 : 400;
    const t = setTimeout(() => stepReplay(1), delay);
    return () => clearTimeout(t);
  }, [isReplaying, replayIndex, replaySpeed, stepReplay]);

  const phasesOrder = phases || ['triage', 'investigate', 'analyze', 'command'];

  return (
    <div className="collaboration-panel">
      <div className="collaboration-panel__header">
        <h3 className="collaboration-panel__title">Agent Collaboration</h3>
        <span className="collaboration-panel__mode-badge">
          {mode === 'live' ? 'LIVE' : 'REPLAY'}
        </span>
      </div>

      {/* Phase Timeline */}
      <div className="phase-timeline">
        {phasesOrder.map((phase, i) => {
          const isActive = activePhase === phase;
          const isDone = completedPhases.includes(phase);
          return (
            <div
              key={phase}
              className={`phase-step ${isActive ? 'active' : ''} ${isDone ? 'completed' : ''}`}
            >
              <div className="phase-step__dot">
                {isDone ? '✓' : i + 1}
              </div>
              <div className="phase-step__label">{PHASE_LABELS[phase] || phase}</div>
            </div>
          );
        })}
      </div>

      {/* Blackboard Feed */}
      <div className="blackboard-feed" ref={feedRef}>
        {entries.length === 0 && (
          <div style={{ color: 'rgba(224,240,255,0.3)', fontSize: 13, textAlign: 'center', marginTop: 24 }}>
            {mode === 'live' ? 'Waiting for agents...' : 'Press → to step through'}
          </div>
        )}
        {entries.map((entry, idx) => (
          <EntryCard
            key={entry.entry_id || idx}
            entry={entry}
            isHighlighted={mode === 'replay' && idx === replayIndex}
          />
        ))}
      </div>

      {/* Status Bar */}
      <div className="collaboration-status-bar">
        <div className={`status-dot ${status === 'active' ? 'active' : 'completed'}`} />
        <span>{status === 'active' ? `${elapsedSeconds}s` : 'Completed'}</span>
        <span>{entries.length} contributions</span>
        {mode === 'live' && status === 'active' && (
          <span style={{ marginLeft: 'auto', color: '#64b4ff' }}>● Live</span>
        )}
        {status === 'completed' && mode === 'live' && (
          <button
            style={{ marginLeft: 'auto', ...replayBtnStyle }}
            onClick={() => setMode('replay')}
          >
            Replay
          </button>
        )}
      </div>

      {/* Replay Controls (visible in replay mode) */}
      {mode === 'replay' && (
        <div className="replay-controls">
          <button className="replay-btn" onClick={() => { setReplayIndex(-1); setEntries([]); setIsReplaying(false); }}>
            ↩ Reset
          </button>
          <button className="replay-btn" onClick={() => stepReplay(-1)}>‹</button>
          <button
            className="replay-btn"
            onClick={() => setIsReplaying(!isReplaying)}
          >
            {isReplaying ? '⏸' : '▶'}
          </button>
          <button className="replay-btn" onClick={() => stepReplay(1)}>›</button>
          <button
            className="replay-btn"
            onClick={() => { setEntries(allEntriesRef.current); setReplayIndex(allEntriesRef.current.length - 1); setIsReplaying(false); }}
          >
            ⏭ End
          </button>
          <select
            className="replay-speed-select"
            value={replaySpeed}
            onChange={e => setReplaySpeed(Number(e.target.value))}
          >
            <option value={1}>1×</option>
            <option value={2}>2×</option>
            <option value={5}>5×</option>
          </select>
          <span style={{ fontSize: 11, color: 'rgba(224,240,255,0.3)', marginLeft: 'auto' }}>
            {replayIndex + 1}/{allEntriesRef.current.length}
          </span>
        </div>
      )}
    </div>
  );
}

const replayBtnStyle = {
  padding: '3px 10px',
  borderRadius: 4,
  border: '1px solid rgba(100,180,255,0.3)',
  background: 'rgba(100,180,255,0.08)',
  color: '#64b4ff',
  fontSize: 11,
  cursor: 'pointer',
};
```

- [ ] **Step 3: Commit**

```bash
git add apps/web/src/components/CollaborationPanel.js apps/web/src/components/CollaborationPanel.css
git commit -m "feat: add CollaborationPanel component with live mode, phase timeline, blackboard feed, and replay"
```

---

## Task 11: Wire ChatPage.js

**Files:**
- Modify: `apps/web/src/pages/ChatPage.js`

- [ ] **Step 1: Read ChatPage.js to understand current structure**

```bash
head -80 apps/web/src/pages/ChatPage.js
```

- [ ] **Step 2: Add session events SSE hook and collaboration state**

In `ChatPage.js`, add the following after the existing state declarations:

```javascript
// Collaboration state
const [activeCollaboration, setActiveCollaboration] = useState(null); // { id, phases, isCompleted }
const [showCollabPanel, setShowCollabPanel] = useState(false);
const sessionEventsRef = useRef(null);
const API_BASE = process.env.REACT_APP_API_BASE_URL || '';

// Open long-lived session events SSE when session loads.
// Use fetch (not EventSource) — native EventSource cannot send custom Authorization headers.
useEffect(() => {
  if (!sessionId || !token) return;

  const ctrl = new AbortController();
  sessionEventsRef.current = ctrl;

  (async () => {
    try {
      const res = await fetch(
        `${API_BASE}/api/v1/chat/sessions/${sessionId}/events`,
        { headers: { Authorization: `Bearer ${token}` }, signal: ctrl.signal }
      );
      if (!res.ok) return;
      const reader = res.body.getReader();
      const decoder = new TextDecoder();
      let buf = '';
      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        buf += decoder.decode(value, { stream: true });
        const lines = buf.split('\n');
        buf = lines.pop();
        for (const line of lines) {
          if (!line.startsWith('data: ')) continue;
          try {
            const data = JSON.parse(line.slice(6));
            if (data.event_type === 'collaboration_started') {
              const { collaboration_id, pattern, agents } = data.payload;
              const phases = agents.map(a => a.phase).filter(Boolean);
              setActiveCollaboration({
                id: collaboration_id,
                phases: phases.length ? phases : ['triage', 'investigate', 'analyze', 'command'],
                isCompleted: false,
              });
              setShowCollabPanel(true);
            } else if (data.event_type === 'collaboration_completed') {
              setActiveCollaboration(prev => prev ? { ...prev, isCompleted: true } : prev);
            }
          } catch (_) {}
        }
      }
    } catch (e) {
      if (e.name !== 'AbortError') console.warn('[ChatPage] session SSE error', e);
    }
  })();

  return () => { ctrl.abort(); };
}, [sessionId, token]);
```

- [ ] **Step 3: Add CollaborationPanel to the chat layout**

Import CollaborationPanel at the top of ChatPage.js:

```javascript
import CollaborationPanel from '../components/CollaborationPanel';
```

In the render/return, wrap the existing chat content in a flex container and conditionally render the panel:

```jsx
<div style={{ display: 'flex', height: '100%', overflow: 'hidden' }}>
  {/* Existing chat content */}
  <div style={{ flex: 1, overflow: 'hidden' }}>
    {/* ... existing JSX ... */}
  </div>

  {/* Collaboration Panel (slide-in from right) */}
  {showCollabPanel && activeCollaboration && (
    <CollaborationPanel
      collaborationId={activeCollaboration.id}
      phases={activeCollaboration.phases}
      apiBaseUrl={`${API_BASE}/api/v1`}
      token={token}
      isCompleted={activeCollaboration.isCompleted}
    />
  )}
</div>
```

Also add a toggle button in the chat header area to show/hide the panel:

```jsx
{activeCollaboration && (
  <button
    onClick={() => setShowCollabPanel(!showCollabPanel)}
    style={{ /* inline style matching ocean theme */ }}
  >
    {showCollabPanel ? 'Hide Panel' : 'View Collaboration'}
  </button>
)}
```

- [ ] **Step 4: Commit**

```bash
git add apps/web/src/pages/ChatPage.js
git commit -m "feat: wire CollaborationPanel into ChatPage with session events SSE stream"
```

---

## Task 12: Demo Seed Script

**Files:**
- Create: `apps/api/scripts/seed_incident_demo.py`

- [ ] **Step 1: Create the seed script**

Create `apps/api/scripts/seed_incident_demo.py`:

```python
"""Idempotent demo seed script for A2A incident investigation demo.

Run before demo day:
    cd apps/api && python scripts/seed_incident_demo.py

Scenario is parameterized in DEMO_CONFIG — swap values for Levi's-specific data.
"""

import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault("DATABASE_URL", "postgresql://postgres:postgres@localhost:5432/agentprovision")

# ---------------------------------------------------------------------------
# DEMO SCENARIO CONFIG — swap these values for Levi's-specific data
# ---------------------------------------------------------------------------
DEMO_CONFIG = {
    "tenant_email": "test@example.com",  # demo tenant login
    "agents": [
        {
            "name": "Triage Agent",
            "role": "triage_agent",
            "description": "Incident triage specialist — classifies severity and scopes blast radius",
            "personality": {"tone": "urgent", "verbosity": "concise", "description": "You are a triage specialist."},
        },
        {
            "name": "Data Investigator",
            "role": "investigator",
            "description": "Data pipeline investigator — correlates events, finds root causes in data flows",
            "personality": {"tone": "analytical", "verbosity": "detailed", "description": "You are a data investigator."},
        },
        {
            "name": "Root Cause Analyst",
            "role": "analyst",
            "description": "Root cause analyst — validates hypotheses with quantitative evidence",
            "personality": {"tone": "critical", "verbosity": "precise", "description": "You are a root cause analyst."},
        },
        {
            "name": "Incident Commander",
            "role": "commander",
            "description": "Incident commander — synthesizes findings into actionable remediation plans",
            "personality": {"tone": "authoritative", "verbosity": "structured", "description": "You are the incident commander."},
        },
    ],
    "knowledge_entities": [
        {"name": "Product Catalog ERP", "entity_type": "data_source", "description": "ERP system of record for product and pricing data"},
        {"name": "E-Commerce Platform", "entity_type": "service", "description": "Customer-facing product catalog and checkout"},
        {"name": "Pricing Engine", "entity_type": "service", "description": "Real-time pricing calculation service"},
        {"name": "Regional Sync Pipeline", "entity_type": "pipeline", "description": "Nightly sync pipeline for regional pricing"},
        {"name": "Master Data Hub", "entity_type": "infrastructure", "description": "Central MDM hub that transforms and routes product data"},
    ],
    "observations": [
        ("Master Data Hub", "1,247 SKUs have price mismatch between ERP and e-commerce as of 2026-04-12 reconciliation run"),
        ("Regional Sync Pipeline", "Last successful full sync was 6 days ago (2026-04-06)"),
        ("Regional Sync Pipeline", "Partial syncs running daily but skipping records with validation errors silently"),
        ("Master Data Hub", "Schema migration applied 2026-04-06: added NOT NULL column currency_precision to product_master table"),
        ("Master Data Hub", "340 SKUs missing currency_precision value — failing validation, excluded from sync"),
        ("Pricing Engine", "EMEA and APAC regions affected; NA uses separate sync path and is unaffected"),
        ("E-Commerce Platform", "Customer-facing prices for 1,247 SKUs are stale — last updated 6 days ago"),
    ],
    "relations": [
        ("Product Catalog ERP", "feeds", "Master Data Hub"),
        ("Master Data Hub", "syncs_to", "E-Commerce Platform"),
        ("Pricing Engine", "depends_on", "E-Commerce Platform"),
        ("Regional Sync Pipeline", "orchestrates", "Master Data Hub"),
    ],
    "coalition_template": {
        "name": "Master Data Incident Investigation Team",
        "pattern": "incident_investigation",
        "task_types": ["incident", "investigate", "outage", "data_quality"],
    },
}


def seed(config: dict):
    from app.db.session import SessionLocal
    from app.models.user import User
    from app.models.tenant import Tenant
    from app.models.agent import Agent
    from app.models.knowledge_entity import KnowledgeEntity
    from app.models.knowledge_relation import KnowledgeRelation
    from app.models.coalition import CoalitionTemplate

    db = SessionLocal()
    try:
        # Find demo tenant
        user = db.query(User).filter(User.email == config["tenant_email"]).first()
        if not user:
            print(f"ERROR: User {config['tenant_email']} not found. Run the API first to seed demo data.")
            return
        tenant_id = user.tenant_id
        print(f"Seeding demo for tenant: {tenant_id}")

        # Create agents (idempotent — skip if exists by role)
        agent_objects = {}
        for agent_cfg in config["agents"]:
            existing = db.query(Agent).filter(
                Agent.tenant_id == tenant_id,
                Agent.role == agent_cfg["role"],
            ).first()
            if existing:
                print(f"  Agent {agent_cfg['role']} already exists, skipping")
                agent_objects[agent_cfg["role"]] = existing
                continue
            agent = Agent(
                tenant_id=tenant_id,
                name=agent_cfg["name"],
                role=agent_cfg["role"],
                description=agent_cfg["description"],
                personality=agent_cfg["personality"],
                config={"llm_model": "gemini"},
                autonomy_level="supervised",
            )
            db.add(agent)
            db.flush()
            agent_objects[agent_cfg["role"]] = agent
            print(f"  Created agent: {agent_cfg['name']}")

        # Create knowledge entities (idempotent)
        entity_objects = {}
        for ent_cfg in config["knowledge_entities"]:
            existing = db.query(KnowledgeEntity).filter(
                KnowledgeEntity.tenant_id == tenant_id,
                KnowledgeEntity.name == ent_cfg["name"],
            ).first()
            if existing:
                entity_objects[ent_cfg["name"]] = existing
                continue
            entity = KnowledgeEntity(
                tenant_id=tenant_id,
                name=ent_cfg["name"],
                entity_type=ent_cfg["entity_type"],
                description=ent_cfg["description"],
            )
            db.add(entity)
            db.flush()
            entity_objects[ent_cfg["name"]] = entity
            print(f"  Created entity: {ent_cfg['name']}")

        # Add observations to entities
        from app.models.knowledge_entity import KnowledgeObservation
        for entity_name, observation_text in config["observations"]:
            entity = entity_objects.get(entity_name)
            if not entity:
                continue
            existing_obs = db.query(KnowledgeObservation).filter(
                KnowledgeObservation.entity_id == entity.id,
                KnowledgeObservation.content == observation_text,
            ).first()
            if existing_obs:
                continue
            obs = KnowledgeObservation(
                entity_id=entity.id,
                tenant_id=tenant_id,
                content=observation_text,
                source="demo_seed",
            )
            db.add(obs)
            print(f"  Added observation to {entity_name}")

        # Create relations
        for from_name, rel_type, to_name in config["relations"]:
            from_entity = entity_objects.get(from_name)
            to_entity = entity_objects.get(to_name)
            if not from_entity or not to_entity:
                continue
            existing_rel = db.query(KnowledgeRelation).filter(
                KnowledgeRelation.from_entity_id == from_entity.id,
                KnowledgeRelation.to_entity_id == to_entity.id,
                KnowledgeRelation.relation_type == rel_type,
            ).first()
            if existing_rel:
                continue
            rel = KnowledgeRelation(
                tenant_id=tenant_id,
                from_entity_id=from_entity.id,
                to_entity_id=to_entity.id,
                relation_type=rel_type,
            )
            db.add(rel)
            print(f"  Created relation: {from_name} → {rel_type} → {to_name}")

        # Create coalition template
        tmpl_cfg = config["coalition_template"]
        role_agent_map = {a_cfg["role"]: a_cfg["name"].lower().replace(" ", "-") for a_cfg in config["agents"]}
        existing_tmpl = db.query(CoalitionTemplate).filter(
            CoalitionTemplate.tenant_id == tenant_id,
            CoalitionTemplate.name == tmpl_cfg["name"],
        ).first()
        if not existing_tmpl:
            tmpl = CoalitionTemplate(
                tenant_id=tenant_id,
                name=tmpl_cfg["name"],
                pattern=tmpl_cfg["pattern"],
                role_agent_map=role_agent_map,
                task_types=tmpl_cfg["task_types"],
            )
            db.add(tmpl)
            print(f"  Created coalition template: {tmpl_cfg['name']}")

        db.commit()
        print("\nDemo seed complete.")
        print(f"Trigger: POST /api/v1/collaborations/trigger with task_description containing 'investigate', 'incident', or 'alert'")

    except Exception as e:
        db.rollback()
        print(f"ERROR: {e}")
        raise
    finally:
        db.close()


if __name__ == "__main__":
    seed(DEMO_CONFIG)
```

- [ ] **Step 2: Run seed script to verify it works**

```bash
cd apps/api && python scripts/seed_incident_demo.py
```

Expected output: Created/skipped agents, entities, observations, relations, coalition template.

- [ ] **Step 3: Commit**

```bash
git add apps/api/scripts/seed_incident_demo.py
git commit -m "feat: add parameterized incident demo seed script for A2A Levi's demo"
```

---

## Task 13: Integration Smoke Test

This verifies the full happy path end-to-end.

- [ ] **Step 1: Deploy to K8s**

```bash
git push origin main
# Wait for CI/CD to deploy (self-hosted runner picks up push to main)
# Monitor: kubectl get pods -n agentprovision -w
```

- [ ] **Step 2: Run demo seed**

```bash
kubectl exec -n agentprovision deploy/api -- python scripts/seed_incident_demo.py
```

- [ ] **Step 3: Test session events SSE**

```bash
# Get a token first
TOKEN=$(curl -s -X POST http://localhost:8000/api/v1/auth/login \
  -H "Content-Type: application/json" \
  -d '{"email":"test@example.com","password":"password"}' | jq -r '.access_token')

# Get a session ID (or create one)
SESSION_ID=$(curl -s -H "Authorization: Bearer $TOKEN" \
  http://localhost:8000/api/v1/chat/sessions | jq -r '.[0].id')

# Open session events stream in background
curl -N -H "Authorization: Bearer $TOKEN" \
  "http://localhost:8000/api/v1/chat/sessions/$SESSION_ID/events" &
SSE_PID=$!
```

- [ ] **Step 4: Trigger collaboration manually**

```bash
curl -s -X POST http://localhost:8000/api/v1/collaborations/trigger \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d "{\"chat_session_id\":\"$SESSION_ID\",\"task_description\":\"Investigate: pricing discrepancy on 1200 SKUs, ERP and e-commerce out of sync\"}"
```

Expected: `{"status": "dispatched", ...}`

- [ ] **Step 5: Verify collaboration_started event received**

Check the SSE output in the background process — should see `data: {"event_type": "collaboration_started", ...}` within a few seconds.

- [ ] **Step 6: Poll collaboration detail**

```bash
COLLAB_ID=<from collaboration_started event>
curl -s -H "Authorization: Bearer $TOKEN" \
  "http://localhost:8000/api/v1/collaborations/$COLLAB_ID/detail" | jq '.entry_count'
```

As the workflow runs, entry_count should increase (0 → 1 → 2 → 3 → 4).

- [ ] **Step 7: Kill SSE background process**

```bash
kill $SSE_PID
```

- [ ] **Step 8: Final commit (if any last-minute fixes)**

```bash
git add -u && git commit -m "fix: smoke test adjustments"
```

---

## Quick Reference: Test Commands

```bash
# Run all new tests
cd apps/api && pytest tests/test_blackboard_chat_session.py tests/test_collaboration_schema.py tests/test_collaboration_phase_mappings.py tests/test_collaboration_events.py tests/test_coalition_activities.py -v

# Run all tests (regression check)
cd apps/api && pytest tests/ -v --timeout=30

# Check for import errors
cd apps/api && python -c "from app.workflows.coalition_workflow import CoalitionWorkflow; print('OK')"
cd apps/api && python -c "from app.services.collaboration_events import publish_event; print('OK')"
cd apps/api && python -c "from app.api.v1.collaborations import router; print('OK')"
```

## Quick Reference: Deploy Commands

```bash
# Apply migration
PG_POD=$(kubectl get pod -n agentprovision -l app.kubernetes.io/name=postgresql -o jsonpath='{.items[0].metadata.name}')
kubectl cp apps/api/migrations/091_blackboard_chat_session_and_source_node.sql agentprovision/$PG_POD:/tmp/migration.sql
kubectl exec -n agentprovision $PG_POD -- psql -U postgres agentprovision -f /tmp/migration.sql

# Restart API after Redis dependency added
kubectl rollout restart deployment/api -n agentprovision

# Seed demo data
kubectl exec -n agentprovision deploy/api -- python scripts/seed_incident_demo.py

# Watch pods
kubectl get pods -n agentprovision -w
```
