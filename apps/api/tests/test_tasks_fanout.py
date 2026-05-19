"""Tests for `apps/api/app/api/v1/tasks_fanout.py` (Phase 1 CLI prototype).

Pin the security and correctness guarantees of the prototype endpoint:

  MT-1 (round-2): cross-tenant /status leak regression — the B1 attack
      vector. User in tenant A dispatches a task; user in tenant B
      attempts /status with the same task_id and gets 404, NOT 200.

  MT-2 (round-2): MAX_TASKS_PER_TENANT cap returns 429 once exceeded.

  MT-3 (round-2): TTL eviction works in isolation via direct
      manipulation of the record's `created_at` (time.monotonic mock
      not needed at this scope — the sweep walks the dict and uses
      the recorded timestamp).

  L2-3 (round-2): whitespace-only providers + a real fanout is valid
      (strip_provider_names runs first and reduces providers to [],
      then the model_validator sees [] ∧ ["claude"] which is fine).

  M2-1 (round-2): X-Tenant-Id header mismatch returns 400.

These tests use FastAPI TestClient with a `get_current_user` override
so they run without a live Postgres backend.
"""

from __future__ import annotations

import time
import uuid
from typing import Optional

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api import deps
from app.api.v1 import tasks_fanout as tf
from app.models.user import User


def _user(tenant_id: Optional[str] = None) -> User:
    return User(
        id=uuid.uuid4(),
        email=f"user-{uuid.uuid4().hex[:6]}@test.com",
        tenant_id=uuid.UUID(tenant_id) if tenant_id else uuid.uuid4(),
        is_active=True,
        is_superuser=False,
        hashed_password="x",
    )


def _make_client(user: User) -> TestClient:
    """Build a TestClient with `get_current_user` overridden to `user`.
    The `tasks_fanout` router is mounted at `/api/v1/tasks-fanout` to
    match production routing.

    #190: the route now also depends on `get_db` (for the cost
    estimator). Override with a MagicMock that terminates ANY query
    chain at `.all()` returning an empty list. Round-1 review M4:
    chain-shape-agnostic (loops over method names instead of hard-
    coding a specific .filter().filter().filter() depth)."""
    from unittest.mock import MagicMock

    def _stub_db():
        m = MagicMock()
        chain = MagicMock()
        chain.all.return_value = []
        for method in ("join", "filter", "order_by", "limit"):
            getattr(chain, method).return_value = chain
        m.query.return_value = chain
        yield m

    app = FastAPI()
    app.dependency_overrides[deps.get_current_user] = lambda: user
    app.dependency_overrides[deps.get_db] = _stub_db
    app.include_router(tf.router, prefix="/api/v1/tasks-fanout")
    return TestClient(app, raise_server_exceptions=False)


@pytest.fixture(autouse=True)
def _isolate_state():
    """Clear the module-level ledger between tests so cap counts and
    tenant-task accounting don't leak across cases."""
    tf._TASKS.clear()
    tf._TENANT_COUNTS.clear()
    yield
    tf._TASKS.clear()
    tf._TENANT_COUNTS.clear()


# ── MT-1: cross-tenant /status leak (B1 regression) ─────────────────


def test_cross_tenant_status_returns_404():
    """User in tenant A dispatches a task. User in tenant B attempts
    /status with that same task_id; must receive 404 (not 200, not
    403). 404 specifically — we do not leak existence."""

    user_a = _user()
    client_a = _make_client(user_a)
    resp = client_a.post(
        "/api/v1/tasks-fanout/run",
        json={"prompt": "tenant-A task"},
    )
    assert resp.status_code == 200, resp.text
    parent_task_id = resp.json()["task_id"]

    # Tenant B user — guess (or rather, know via leaked log) the task_id.
    user_b = _user()
    assert user_b.tenant_id != user_a.tenant_id
    client_b = _make_client(user_b)
    resp = client_b.get(f"/api/v1/tasks-fanout/{parent_task_id}/status")
    assert resp.status_code == 404, (
        f"Cross-tenant /status leak — expected 404 to avoid existence "
        f"oracle, got {resp.status_code}: {resp.text}"
    )


def test_cross_tenant_cancel_returns_404():
    """Same B1 attack against /cancel. Tenant B cannot delete tenant A's
    task even with the exact task_id."""

    user_a = _user()
    client_a = _make_client(user_a)
    resp = client_a.post("/api/v1/tasks-fanout/run", json={"prompt": "x"})
    parent_task_id = resp.json()["task_id"]

    user_b = _user()
    client_b = _make_client(user_b)
    resp = client_b.post(f"/api/v1/tasks-fanout/{parent_task_id}/cancel")
    assert resp.status_code == 404

    # And the task is still there for the rightful owner.
    resp = client_a.get(f"/api/v1/tasks-fanout/{parent_task_id}/status")
    assert resp.status_code == 200


# ── MT-2: MAX_TASKS_PER_TENANT cap ────────────────────────────────────


def test_cap_returns_429_after_max(monkeypatch):
    """Dispatching MAX + 1 single-task requests under the same tenant
    must 429 on the last. We monkeypatch MAX_TASKS_PER_TENANT down to 3
    so the test is fast."""

    monkeypatch.setattr(tf, "MAX_TASKS_PER_TENANT", 3)

    user = _user()
    client = _make_client(user)

    for i in range(3):
        resp = client.post("/api/v1/tasks-fanout/run", json={"prompt": f"task-{i}"})
        assert resp.status_code == 200, f"task #{i} should succeed: {resp.text}"

    # 4th must be rejected.
    resp = client.post("/api/v1/tasks-fanout/run", json={"prompt": "task-4"})
    assert resp.status_code == 429, resp.text
    assert "too many in-flight tasks" in resp.json()["detail"].lower()


def test_cap_counts_fanout_children(monkeypatch):
    """Parent + N children count separately. With MAX=4 and fanout=[a,b,c]
    one dispatch consumes 4 slots (parent + 3 children); a second dispatch
    of the same shape must 429."""

    monkeypatch.setattr(tf, "MAX_TASKS_PER_TENANT", 4)

    user = _user()
    client = _make_client(user)

    resp = client.post(
        "/api/v1/tasks-fanout/run",
        json={"prompt": "first fanout", "fanout": ["a", "b", "c"]},
    )
    assert resp.status_code == 200, resp.text
    assert len(resp.json()["children"]) == 3

    # Cap is exhausted; second dispatch (any shape) must 429.
    resp = client.post("/api/v1/tasks-fanout/run", json={"prompt": "second"})
    assert resp.status_code == 429


# ── MT-3: TTL eviction ───────────────────────────────────────────────


def test_ttl_eviction_drops_expired_records():
    """Direct test of `_sweep_expired_tasks`. Mutate a record's
    `created_at` to a time before TASK_TTL_SECONDS ago; sweep must
    evict it and decrement the tenant counter."""

    user = _user()
    client = _make_client(user)

    resp = client.post("/api/v1/tasks-fanout/run", json={"prompt": "soon-to-expire"})
    task_id = resp.json()["task_id"]
    tenant_id = str(user.tenant_id)
    assert tf._TENANT_COUNTS.get(tenant_id) == 1
    assert task_id in tf._TASKS

    # Fast-forward the record's birth past the TTL.
    tf._TASKS[task_id]["created_at"] = time.monotonic() - (tf.TASK_TTL_SECONDS + 1.0)

    evicted = tf._sweep_expired_tasks()
    assert evicted == 1
    assert task_id not in tf._TASKS
    # Counter dropped to 0 -> key removed by _evict_record.
    assert tenant_id not in tf._TENANT_COUNTS


# ── L2-3: whitespace-only providers + fanout is valid ────────────────


def test_whitespace_only_providers_with_fanout_is_valid():
    """`{"providers": [" ", ""], "fanout": ["claude"]}` should NOT
    return 422 — the strip validator collapses providers to [] before
    the mutual-exclusion check sees it, so [] ∧ ["claude"] is fine."""

    user = _user()
    client = _make_client(user)
    resp = client.post(
        "/api/v1/tasks-fanout/run",
        json={
            "prompt": "x",
            "providers": [" ", ""],
            "fanout": ["claude"],
        },
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["children"][0]["provider"] == "claude"


# ── M2-1: X-Tenant-Id header mismatch ────────────────────────────────


def test_x_tenant_id_mismatch_returns_400():
    """If the client sends X-Tenant-Id and it does not match the JWT
    tenant, return 400. This catches stale `~/.config/agentprovision/config.toml` after
    a tenant switch."""

    user = _user()
    client = _make_client(user)
    resp = client.post(
        "/api/v1/tasks-fanout/run",
        json={"prompt": "x"},
        headers={"X-Tenant-Id": "00000000-0000-0000-0000-000000000000"},
    )
    assert resp.status_code == 400, resp.text
    assert "x-tenant-id" in resp.json()["detail"].lower()


def test_x_tenant_id_matching_is_accepted():
    """Matching X-Tenant-Id should not be rejected (the contract is
    'must equal', not 'must be absent')."""

    user = _user()
    client = _make_client(user)
    resp = client.post(
        "/api/v1/tasks-fanout/run",
        json={"prompt": "x"},
        headers={"X-Tenant-Id": str(user.tenant_id)},
    )
    assert resp.status_code == 200, resp.text


def test_x_tenant_id_whitespace_only_is_treated_as_not_set():
    """Round-3 L3-2: a header of pure whitespace ("   ") should be
    treated as not-set (same as missing), not a mismatch. A
    hand-edited config might leave a blank value; that shouldn't
    produce a confusing 400."""

    user = _user()
    client = _make_client(user)
    resp = client.post(
        "/api/v1/tasks-fanout/run",
        json={"prompt": "x"},
        headers={"X-Tenant-Id": "   "},
    )
    assert resp.status_code == 200, resp.text


def test_tenant_counts_invariant_after_dispatch_cancel_sweep(monkeypatch):
    """Round-3 N3-1: assert `_TENANT_COUNTS` matches the slow recount
    of `_TASKS` through a full dispatch + cancel + TTL-sweep cycle.
    Locks down the lock-step invariant against future bypass."""

    user = _user()
    tenant_id = str(user.tenant_id)
    client = _make_client(user)

    # Dispatch: parent + 2 children = 3 records.
    resp = client.post(
        "/api/v1/tasks-fanout/run",
        json={"prompt": "x", "fanout": ["claude", "codex"]},
    )
    assert resp.status_code == 200
    parent_id = resp.json()["task_id"]
    child_id = resp.json()["children"][0]["task_id"]

    # Counter == 3, recount == 3.
    assert tf._TENANT_COUNTS[tenant_id] == 3
    assert tf._recount_tenant_tasks_from_records(tenant_id) == 3
    assert tf._TENANT_COUNTS[tenant_id] == tf._recount_tenant_tasks_from_records(
        tenant_id
    )

    # Cancel one child: counter drops by 1, recount drops by 1.
    resp = client.post(f"/api/v1/tasks-fanout/{child_id}/cancel")
    assert resp.status_code == 204
    assert tf._TENANT_COUNTS[tenant_id] == 2
    assert tf._recount_tenant_tasks_from_records(tenant_id) == 2

    # TTL sweep: expire all remaining records. Counter must zero out
    # and remove the tenant key entirely.
    now = time.monotonic()
    for rec in tf._TASKS.values():
        rec["created_at"] = now - (tf.TASK_TTL_SECONDS + 1.0)
    evicted = tf._sweep_expired_tasks()
    assert evicted == 2
    assert tf._recount_tenant_tasks_from_records(tenant_id) == 0
    assert tenant_id not in tf._TENANT_COUNTS  # zero-count key was pruned


# ── Additional defense-in-depth: model_validator (M4) regression ─────


def test_providers_and_fanout_together_returns_422():
    """Round-1 M4: the model_validator must reject the combo at the
    schema level with 422 + structured field error."""

    user = _user()
    client = _make_client(user)
    resp = client.post(
        "/api/v1/tasks-fanout/run",
        json={"prompt": "x", "providers": ["claude"], "fanout": ["codex"]},
    )
    assert resp.status_code == 422
    body = resp.json()
    assert "mutually exclusive" in str(body).lower()


# ── #177 Phase 1 ship: USE_REAL_FANOUT_WORKFLOW flag ──────────────────


def test_real_fanout_flag_off_uses_stub(monkeypatch):
    """Default flag (False) keeps the in-memory stub path; task_id
    is prefixed `t_` (16-hex stub id), not `fanout-...`."""

    monkeypatch.setattr(tf.settings, "USE_REAL_FANOUT_WORKFLOW", False)

    user = _user()
    client = _make_client(user)
    resp = client.post(
        "/api/v1/tasks-fanout/run",
        json={"prompt": "stub path", "fanout": ["claude", "codex"]},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["task_id"].startswith("t_")


def test_real_fanout_flag_on_dispatches_workflow(monkeypatch):
    """Flag=True + fanout: route bypasses the stub and dispatches a
    real Temporal workflow. We monkeypatch `_dispatch_fanout_workflow`
    so the test does not need a live Temporal server."""

    monkeypatch.setattr(tf.settings, "USE_REAL_FANOUT_WORKFLOW", True)

    fake_dispatched = {}

    async def fake_dispatch(*, prompt, tenant_id, providers, merge, agent_id, session_id):
        fake_dispatched.update(
            prompt=prompt,
            tenant_id=tenant_id,
            providers=providers,
            merge=merge,
        )
        wf_id = f"fanout-{tenant_id}-fake-uuid"
        return {"task_id": wf_id, "run_id": "fake-run-id"}

    monkeypatch.setattr(tf, "_dispatch_fanout_workflow", fake_dispatch)

    user = _user()
    client = _make_client(user)
    resp = client.post(
        "/api/v1/tasks-fanout/run",
        json={
            "prompt": "real workflow path",
            "fanout": ["claude", "codex", "gemini"],
            "merge": "council",
        },
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    # Real-dispatch task_id has the `fanout-` prefix shape.
    assert body["task_id"].startswith("fanout-"), body
    # Round-1 review N2: real-dispatch path surfaces an empty children
    # list; real child workflow_ids land on /status from the follow-up.
    assert body["children"] == []
    # The dispatch helper was called with the body's params verbatim.
    assert fake_dispatched["merge"] == "council"
    assert fake_dispatched["providers"] == ["claude", "codex", "gemini"]


def test_real_fanout_cross_tenant_status_returns_404(monkeypatch):
    """Round-1 review M2: real-path tenant isolation regression test.
    A user in tenant B passes a task_id with tenant A's prefix; must
    return 404, not 500, not 200."""

    monkeypatch.setattr(tf.settings, "USE_REAL_FANOUT_WORKFLOW", True)

    user_a = _user()
    user_b = _user()
    assert user_a.tenant_id != user_b.tenant_id

    # Fake a workflow_id under tenant A's prefix.
    fake_wf_id = f"fanout-{user_a.tenant_id}-deadbeef"
    client_b = _make_client(user_b)
    resp = client_b.get(f"/api/v1/tasks-fanout/{fake_wf_id}/status")
    assert resp.status_code == 404, resp.text


def test_real_fanout_cross_tenant_cancel_returns_404(monkeypatch):
    """Round-1 review M2: same regression for /cancel."""

    monkeypatch.setattr(tf.settings, "USE_REAL_FANOUT_WORKFLOW", True)

    user_a = _user()
    user_b = _user()
    fake_wf_id = f"fanout-{user_a.tenant_id}-deadbeef"
    client_b = _make_client(user_b)
    resp = client_b.post(f"/api/v1/tasks-fanout/{fake_wf_id}/cancel")
    assert resp.status_code == 404, resp.text


def test_real_fanout_status_completed_returns_merged_text(monkeypatch):
    """Round-1 review M2: when Temporal reports COMPLETED, the route
    surfaces the workflow's `merged_text` from the dataclass result.
    We mock both describe + fetch helpers so no live Temporal is needed."""

    monkeypatch.setattr(tf.settings, "USE_REAL_FANOUT_WORKFLOW", True)

    user = _user()
    tenant_str = str(user.tenant_id)
    wf_id = f"fanout-{tenant_str}-fake-uuid"

    async def fake_describe(task_id, *, expected_tenant_id):
        assert task_id == wf_id
        assert expected_tenant_id == tenant_str
        return {
            "task_id": task_id,
            "status": "completed",
            "result": None,
            "error": None,
            "raw": {},
        }

    async def fake_fetch_result(workflow_id, *, run_id=None):
        assert workflow_id == wf_id
        return {
            "merge_mode": "council",
            "merged_text": "consensus: pass",
            "children": [],
            "success": True,
        }

    monkeypatch.setattr(tf, "_describe_fanout_workflow", fake_describe)

    # Patch the workflows service module the route imports lazily.
    from app.services import workflows as wf_service

    monkeypatch.setattr(wf_service, "fetch_workflow_result", fake_fetch_result)

    client = _make_client(user)
    resp = client.get(f"/api/v1/tasks-fanout/{wf_id}/status")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "completed"
    assert body["result"] == "consensus: pass"
    assert body["error"] is None


def test_real_fanout_describe_exception_returns_404(monkeypatch):
    """Round-1 review M2: when `_describe_fanout_workflow` raises
    (e.g. Temporal unreachable), the route returns 404 with a
    descriptive error string — not 500."""

    monkeypatch.setattr(tf.settings, "USE_REAL_FANOUT_WORKFLOW", True)

    user = _user()
    wf_id = f"fanout-{user.tenant_id}-fake-uuid"

    async def boom(task_id, *, expected_tenant_id):
        raise RuntimeError("temporal unreachable")

    monkeypatch.setattr(tf, "_describe_fanout_workflow", boom)

    client = _make_client(user)
    resp = client.get(f"/api/v1/tasks-fanout/{wf_id}/status")
    assert resp.status_code == 404
    detail = resp.json()["detail"]
    assert "RuntimeError" in detail and "temporal unreachable" in detail


def test_real_fanout_tenant_counts_invariant(monkeypatch):
    """Round-3 H3-1 regression: on the real-dispatch path, the
    parent + mirrored child records must keep `_TENANT_COUNTS` in
    lock-step with the slow recount. Without the mirrored child
    records, counter inflates by len(fanout) per dispatch."""

    monkeypatch.setattr(tf.settings, "USE_REAL_FANOUT_WORKFLOW", True)

    async def fake_dispatch(*, prompt, tenant_id, providers, merge, agent_id, session_id):
        return {"task_id": f"fanout-{tenant_id}-fake", "run_id": "r"}

    monkeypatch.setattr(tf, "_dispatch_fanout_workflow", fake_dispatch)

    user = _user()
    tenant_id = str(user.tenant_id)
    client = _make_client(user)

    resp = client.post(
        "/api/v1/tasks-fanout/run",
        json={"prompt": "x", "fanout": ["claude", "codex"]},
    )
    assert resp.status_code == 200, resp.text

    # Parent + 2 children = 3 records; counter must == 3.
    assert tf._TENANT_COUNTS[tenant_id] == 3
    assert tf._recount_tenant_tasks_from_records(tenant_id) == 3
    assert tf._TENANT_COUNTS[tenant_id] == tf._recount_tenant_tasks_from_records(
        tenant_id
    )


def test_real_fanout_dispatch_enforces_cap(monkeypatch):
    """Round-1 review B2 regression: real-dispatch path must also
    honor MAX_TASKS_PER_TENANT — the cap-bypass was the original
    blocker. Set MAX low, dispatch one fanout, expect the 2nd to 429."""

    monkeypatch.setattr(tf.settings, "USE_REAL_FANOUT_WORKFLOW", True)
    monkeypatch.setattr(tf, "MAX_TASKS_PER_TENANT", 1)

    async def fake_dispatch(*, prompt, tenant_id, providers, merge, agent_id, session_id):
        return {"task_id": f"fanout-{tenant_id}-x", "run_id": "r"}

    monkeypatch.setattr(tf, "_dispatch_fanout_workflow", fake_dispatch)

    user = _user()
    client = _make_client(user)

    # Cap is 1, fanout=[a,b] would mint 1 parent + 2 children = 3 slots
    # which exceeds the cap on the very first request. Use single
    # provider so we have a chance to push two requests through.
    resp = client.post("/api/v1/tasks-fanout/run", json={"prompt": "x", "fanout": ["a"]})
    # n_new = 1 + 1 = 2 which > cap of 1, so this 429s.
    assert resp.status_code == 429, resp.text


def test_real_fanout_flag_on_providers_chain_dispatches_workflow(monkeypatch):
    """Phase 2 (#177 follow-up, 2026-05-18): `--providers` fallback
    chain is now dispatched as a real FanoutChatCliWorkflow with
    `merge=first-wins`. Before this change the same input fell back
    to the in-memory stub even with the flag on."""

    monkeypatch.setattr(tf.settings, "USE_REAL_FANOUT_WORKFLOW", True)

    captured = {}

    async def fake_dispatch(*, prompt, tenant_id, providers, merge, agent_id, session_id):
        captured.update(prompt=prompt, providers=providers, merge=merge)
        return {"task_id": f"fanout-{tenant_id}-fake", "run_id": "r"}

    monkeypatch.setattr(tf, "_dispatch_fanout_workflow", fake_dispatch)

    user = _user()
    client = _make_client(user)
    resp = client.post(
        "/api/v1/tasks-fanout/run",
        json={"prompt": "chain dispatch", "providers": ["claude", "codex"]},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    # Real-dispatch task_id has the `fanout-` prefix shape.
    assert body["task_id"].startswith("fanout-"), body
    # Workflow was invoked with the body's providers verbatim, and
    # the merge mode was rewritten to first-wins (the closest analog
    # to a fallback chain we can express on FanoutChatCliWorkflow).
    assert captured["providers"] == ["claude", "codex"]
    assert captured["merge"] == "first-wins"


def test_real_fanout_flag_on_single_provider_dispatches_workflow(monkeypatch):
    """Phase 2 (#177 follow-up, 2026-05-18): the 90% case — `alpha run
    "..."` with no provider flags at all — dispatches a single-child
    real workflow with the safe default platform. Returns immediately
    with a `fanout-` task_id (the `--background` semantics)."""

    monkeypatch.setattr(tf.settings, "USE_REAL_FANOUT_WORKFLOW", True)

    captured = {}

    async def fake_dispatch(*, prompt, tenant_id, providers, merge, agent_id, session_id):
        captured.update(prompt=prompt, providers=providers, merge=merge)
        return {"task_id": f"fanout-{tenant_id}-fake", "run_id": "r"}

    monkeypatch.setattr(tf, "_dispatch_fanout_workflow", fake_dispatch)

    user = _user()
    client = _make_client(user)
    resp = client.post(
        "/api/v1/tasks-fanout/run",
        json={"prompt": "delegate this research"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    # Real-dispatch task_id shape — no synthetic `t_<hex>`.
    assert body["task_id"].startswith("fanout-"), body
    # Default single-provider list (one platform, first-wins merge).
    assert captured["providers"] == [tf.DEFAULT_RUN_PROVIDER]
    assert captured["merge"] == "first-wins"


def test_run_background_returns_immediately(monkeypatch):
    """`--background` contract: POST /run returns 200 with a task_id
    before the workflow completes. We assert the response shape arrives
    synchronously — the fake dispatch does not block, mirroring the
    Temporal `start_workflow` (not `execute_workflow`) semantics."""

    monkeypatch.setattr(tf.settings, "USE_REAL_FANOUT_WORKFLOW", True)

    async def fake_dispatch(*, prompt, tenant_id, providers, merge, agent_id, session_id):
        # Real start_workflow returns a handle, NOT a workflow result.
        # We do not sleep / await the run — that's the whole point of
        # --background and the alpha watch poll loop.
        return {"task_id": f"fanout-{tenant_id}-bg", "run_id": "r"}

    monkeypatch.setattr(tf, "_dispatch_fanout_workflow", fake_dispatch)

    user = _user()
    client = _make_client(user)
    resp = client.post(
        "/api/v1/tasks-fanout/run",
        json={"prompt": "long-running thing", "fanout": ["claude", "codex"]},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    # Caller gets a queued status + task_id without waiting for the
    # workflow to finish.
    assert body["status"] == "queued"
    assert body["task_id"].startswith("fanout-")


def test_real_fanout_flag_off_single_provider_stays_on_stub(monkeypatch):
    """Rollback story: with the flag explicitly off, the single-provider
    path stays on the in-memory stub (one env-var flip rollback)."""

    monkeypatch.setattr(tf.settings, "USE_REAL_FANOUT_WORKFLOW", False)

    user = _user()
    client = _make_client(user)
    resp = client.post(
        "/api/v1/tasks-fanout/run",
        json={"prompt": "no fanout"},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["task_id"].startswith("t_")


# ── #188 SSE endpoint coverage (round-1 review M4) ────────────────────


def test_sse_stream_own_tenant_returns_event_stream():
    """`GET /events/stream` for an own-tenant task returns 200 +
    text/event-stream and emits at least the initial status event."""

    user = _user()
    client = _make_client(user)
    resp = client.post("/api/v1/tasks-fanout/run", json={"prompt": "x"})
    assert resp.status_code == 200
    task_id = resp.json()["task_id"]

    # FastAPI TestClient's `stream=True` returns a response that
    # exposes `.iter_bytes()`. We read a small prefix to confirm
    # SSE shape without waiting for the full lifecycle.
    with client.stream("GET", f"/api/v1/tasks-fanout/{task_id}/events/stream") as r:
        assert r.status_code == 200
        assert r.headers["content-type"].startswith("text/event-stream")
        chunks = b""
        for chunk in r.iter_bytes(chunk_size=256):
            chunks += chunk
            if b"event: status" in chunks or len(chunks) > 4096:
                break
        assert b"event: status" in chunks, (
            f"expected at least one status event, got: {chunks[:512]!r}"
        )


def test_sse_stream_cross_tenant_returns_404():
    """SSE endpoint must enforce the same cross-tenant 404 as
    /status. Tenant B with tenant A's task_id → 404 BEFORE any
    stream bytes."""

    user_a = _user()
    client_a = _make_client(user_a)
    resp = client_a.post("/api/v1/tasks-fanout/run", json={"prompt": "tenant A"})
    task_id = resp.json()["task_id"]

    user_b = _user()
    client_b = _make_client(user_b)
    resp = client_b.get(f"/api/v1/tasks-fanout/{task_id}/events/stream")
    assert resp.status_code == 404


def test_sse_stream_missing_task_returns_404():
    """Unknown task_id → 404, never an empty stream."""

    user = _user()
    client = _make_client(user)
    resp = client.get("/api/v1/tasks-fanout/t_nonexistent/events/stream")
    assert resp.status_code == 404


def test_cancelling_child_removes_it_from_parent_status():
    """Round-2 M2-2: cancelling a child task surgically removes it
    from the parent's children list so /status no longer reports
    the stale child."""

    user = _user()
    client = _make_client(user)
    resp = client.post(
        "/api/v1/tasks-fanout/run",
        json={"prompt": "x", "fanout": ["claude", "codex"]},
    )
    parent_id = resp.json()["task_id"]
    child_to_cancel = resp.json()["children"][0]["task_id"]

    # Cancel one child.
    resp = client.post(f"/api/v1/tasks-fanout/{child_to_cancel}/cancel")
    assert resp.status_code == 204

    # Parent /status no longer surfaces the cancelled child.
    resp = client.get(f"/api/v1/tasks-fanout/{parent_id}/status")
    assert resp.status_code == 200
    children = resp.json()["children"]
    assert all(c["task_id"] != child_to_cancel for c in children)
    assert len(children) == 1  # the other one still there
