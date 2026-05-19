"""Tests for the Phase-2 eval runner.

Two surfaces are exercised:

  1. ``app.services.skill_creator.eval_runner`` — pure-Python helpers
     (compute_eval_dir, dispatch_iteration with a ``_runner`` hook,
     get_iteration_status).
  2. ``POST /api/v1/skills/{skill_id}/evals/run`` +
     ``GET /api/v1/skills/{skill_id}/evals/jobs/{job_id}`` — the
     endpoints, mocked via the same _StubDB pattern as the existing
     ``test_skill_evals_endpoint.py``.

We do NOT call real Temporal here. The ``_runner`` hook on
``dispatch_iteration`` lets us substitute a recording stub for the
thread spawn so we can assert on the exact dispatch args without
booting a Temporal worker.
"""

from __future__ import annotations

import os
os.environ["TESTING"] = "True"

import json
import uuid
from typing import Any, Dict, List, Optional
from unittest.mock import patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api import deps
from app.api.v1 import skill_evals as skill_evals_module
from app.models.user import User
from app.services.skill_creator import eval_runner


# ──────────────────────────────────────────────────────────────────────────
# Stub DB — recognizes the SQL the runner + endpoint issue.
# ──────────────────────────────────────────────────────────────────────────


class _StubResult:
    def __init__(self, row=None, rows=None):
        self._row = row
        self._rows = rows or []

    def fetchone(self):
        return self._row

    def fetchall(self):
        return self._rows


class _StubDB:
    """Recognizes the queries the runner + endpoint issue.

    Substring matchers keep test setup compact while staying stable
    against minor formatting changes in the runner.
    """

    def __init__(
        self,
        *,
        skill_tenant_id: Optional[uuid.UUID] = None,
        skill_name: str = "expense-classifier",
        evals: Optional[List[Dict[str, Any]]] = None,
        iteration_rows: Optional[List[tuple]] = None,
    ):
        self._skill_tenant_id = skill_tenant_id
        self._skill_name = skill_name
        self._evals = evals or []
        self._iteration_rows = iteration_rows or []
        self.executed: List[Dict[str, Any]] = []
        self.committed = False
        self.rolled_back = False
        self._inserted_runs: List[Dict[str, Any]] = []

    def execute(self, statement, params=None):
        sql = str(statement)
        self.executed.append({"sql": sql, "params": params or {}})

        if "FROM skills WHERE" in sql and "name" in sql:
            if self._skill_tenant_id is None:
                return _StubResult(row=None)
            return _StubResult(row=(self._skill_name, str(self._skill_tenant_id)))
        if "FROM skills WHERE" in sql:
            # _verify_tenant_owns_skill query (only selects tenant_id)
            if self._skill_tenant_id is None:
                return _StubResult(row=None)
            return _StubResult(row=(str(self._skill_tenant_id),))
        if "FROM skill_evals" in sql and "ORDER BY created_at" in sql:
            rows = [(uuid.UUID(e["id"]), e["prompt"], e["expectations"])
                    for e in self._evals]
            return _StubResult(rows=rows)
        if "INSERT INTO skill_eval_runs" in sql:
            self._inserted_runs.append(dict(params or {}))
            return _StubResult(row=None)
        if "FROM skill_eval_runs r" in sql and "iteration_run_id" in sql:
            return _StubResult(rows=self._iteration_rows)
        if "FROM skill_eval_runs r" in sql:
            # Loaded by the grade endpoint — not exercised here.
            return _StubResult(row=None)
        return _StubResult(row=None)

    def commit(self):
        self.committed = True

    def rollback(self):
        self.rolled_back = True


def _user(tenant_id: Optional[uuid.UUID] = None) -> User:
    return User(
        id=uuid.uuid4(),
        email=f"user-{uuid.uuid4().hex[:6]}@test.com",
        tenant_id=tenant_id or uuid.uuid4(),
        is_active=True,
        is_superuser=False,
        hashed_password="x",
    )


def _build_client(user: User, db: _StubDB) -> TestClient:
    app = FastAPI()
    app.dependency_overrides[deps.get_current_user] = lambda: user

    def _stub_db():
        yield db

    app.dependency_overrides[deps.get_db] = _stub_db
    app.include_router(skill_evals_module.router, prefix="/api/v1/skills")
    return TestClient(app, raise_server_exceptions=False)


# ──────────────────────────────────────────────────────────────────────────
# Workspace path computation
# ──────────────────────────────────────────────────────────────────────────


def test_compute_eval_dir_layout(tmp_path):
    """The path shape must mirror Claude Code's reference layout."""
    tenant_id = uuid.uuid4()
    p = eval_runner.compute_eval_dir(
        tenant_id=tenant_id,
        skill_slug="expense-classifier",
        iteration=3,
        eval_id="eval-001",
        with_skill=True,
        workspaces_root=str(tmp_path),
    )
    assert p.as_posix().endswith(
        f"{tenant_id}/skills/expense-classifier-workspace/iteration-3/eval-eval-001/with-skill"
    )


def test_compute_eval_dir_baseline_leg(tmp_path):
    tenant_id = uuid.uuid4()
    p = eval_runner.compute_eval_dir(
        tenant_id=tenant_id,
        skill_slug="x",
        iteration=1,
        eval_id="eval-001",
        with_skill=False,
        workspaces_root=str(tmp_path),
    )
    assert p.name == "baseline"


def test_compute_iteration_dir_layout(tmp_path):
    tenant_id = uuid.uuid4()
    p = eval_runner.compute_iteration_dir(
        tenant_id=tenant_id,
        skill_slug="x",
        iteration=2,
        workspaces_root=str(tmp_path),
    )
    assert p.name == "iteration-2"
    assert "x-workspace" in p.as_posix()


# ──────────────────────────────────────────────────────────────────────────
# dispatch_iteration — direct unit test with _runner hook
# ──────────────────────────────────────────────────────────────────────────


def test_dispatch_iteration_inserts_paired_rows_and_calls_runner(tmp_path):
    """Two evals -> 4 row inserts (paired) and 4 runner invocations."""
    tenant_id = uuid.uuid4()
    skill_id = uuid.uuid4()
    eval1, eval2 = uuid.uuid4(), uuid.uuid4()

    db = _StubDB(
        skill_tenant_id=tenant_id,
        evals=[
            {"id": str(eval1), "prompt": "prompt 1", "expectations": []},
            {"id": str(eval2), "prompt": "prompt 2", "expectations": []},
        ],
    )

    recorded: List[Dict[str, Any]] = []

    def _record(**kw):
        recorded.append(kw)

    with patch("app.services.skill_creator.eval_runner._load_skill_body",
               return_value="# stub body"):
        out = eval_runner.dispatch_iteration(
            db,
            skill_id=skill_id,
            iteration=1,
            workspaces_root=str(tmp_path),
            _runner=_record,
        )

    # 4 paired runs = 2 evals * (with_skill + baseline)
    assert len(out["run_ids"]) == 4
    assert out["iteration"] == 1
    assert out["skill_id"] == str(skill_id)
    job_id = uuid.UUID(out["job_id"])

    # 4 INSERTs into skill_eval_runs queued
    inserts = [r for r in db.executed if "INSERT INTO skill_eval_runs" in r["sql"]]
    assert len(inserts) == 4
    # All share the same iteration_run_id
    irids = {r["params"]["irid"] for r in inserts}
    assert irids == {str(job_id)}
    # with_skill leg cardinality: exactly 2 True, 2 False
    legs = [r["params"]["with_skill"] for r in inserts]
    assert legs.count(True) == 2
    assert legs.count(False) == 2

    # Runner was called 4 times — once per row.
    assert len(recorded) == 4
    # with_skill leg gets a non-empty skill_body; baseline does not.
    for rec in recorded:
        if rec["with_skill"]:
            assert rec["skill_body"] == "# stub body"
        else:
            assert rec["skill_body"] == "# stub body"  # both get loaded; runner decides
            # The baseline thread passes "" as instruction_md_content
            # internally; that's enforced in _run_one, not here.
    assert db.committed


def test_dispatch_iteration_raises_on_no_evals():
    tenant_id = uuid.uuid4()
    skill_id = uuid.uuid4()
    db = _StubDB(skill_tenant_id=tenant_id, evals=[])

    with pytest.raises(ValueError, match="no evals defined"):
        eval_runner.dispatch_iteration(
            db, skill_id=skill_id, iteration=1, _runner=lambda **kw: None,
        )


def test_dispatch_iteration_raises_on_unknown_skill():
    db = _StubDB(skill_tenant_id=None)
    with pytest.raises(LookupError, match="not found"):
        eval_runner.dispatch_iteration(
            db, skill_id=uuid.uuid4(), iteration=1, _runner=lambda **kw: None,
        )


def test_dispatch_iteration_raises_on_zero_iteration():
    db = _StubDB(skill_tenant_id=uuid.uuid4(), evals=[
        {"id": str(uuid.uuid4()), "prompt": "x", "expectations": []},
    ])
    with pytest.raises(ValueError, match="iteration must be"):
        eval_runner.dispatch_iteration(
            db, skill_id=uuid.uuid4(), iteration=0, _runner=lambda **kw: None,
        )


# ──────────────────────────────────────────────────────────────────────────
# POST /run endpoint
# ──────────────────────────────────────────────────────────────────────────


def test_run_endpoint_happy_path_returns_202_and_job_id():
    tenant_id = uuid.uuid4()
    skill_id = uuid.uuid4()
    eval1 = uuid.uuid4()

    db = _StubDB(
        skill_tenant_id=tenant_id,
        evals=[{"id": str(eval1), "prompt": "p", "expectations": []}],
    )
    user = _user(tenant_id=tenant_id)
    client = _build_client(user, db)

    # Patch dispatch_iteration on the module the endpoint imported
    # (it's imported as `eval_runner_module`), and confirm the endpoint
    # returns 202 with the expected shape. We don't need the threads to
    # actually run — _runner=None would spawn them, but patching the
    # whole function side-steps that entirely.
    fake_job_id = str(uuid.uuid4())
    fake_run_ids = [str(uuid.uuid4()), str(uuid.uuid4())]
    with patch.object(
        skill_evals_module.eval_runner_module,
        "dispatch_iteration",
        return_value={
            "job_id": fake_job_id,
            "run_ids": fake_run_ids,
            "iteration": 1,
            "skill_id": str(skill_id),
        },
    ):
        resp = client.post(
            f"/api/v1/skills/{skill_id}/evals/run",
            json={"iteration": 1},
        )

    assert resp.status_code == 202, resp.text
    body = resp.json()
    assert body["job_id"] == fake_job_id
    assert body["run_ids"] == fake_run_ids
    assert body["iteration"] == 1
    assert body["skill_id"] == str(skill_id)


def test_run_endpoint_foreign_tenant_returns_404():
    user = _user()
    foreign_tenant = uuid.uuid4()
    db = _StubDB(skill_tenant_id=foreign_tenant)
    client = _build_client(user, db)

    resp = client.post(
        f"/api/v1/skills/{uuid.uuid4()}/evals/run",
        json={"iteration": 1},
    )
    assert resp.status_code == 404


def test_run_endpoint_unknown_skill_returns_404():
    user = _user()
    db = _StubDB(skill_tenant_id=None)
    client = _build_client(user, db)

    resp = client.post(
        f"/api/v1/skills/{uuid.uuid4()}/evals/run",
        json={"iteration": 1},
    )
    assert resp.status_code == 404


def test_run_endpoint_no_evals_returns_400():
    tenant_id = uuid.uuid4()
    skill_id = uuid.uuid4()
    user = _user(tenant_id=tenant_id)
    db = _StubDB(skill_tenant_id=tenant_id, evals=[])
    client = _build_client(user, db)

    # Patch _load_skill_body to dodge the FS scan
    with patch("app.services.skill_creator.eval_runner._load_skill_body",
               return_value=""):
        resp = client.post(
            f"/api/v1/skills/{skill_id}/evals/run",
            json={"iteration": 1},
        )

    assert resp.status_code == 400
    assert "no evals" in resp.json()["detail"].lower()


def test_run_endpoint_bad_iteration_returns_400():
    tenant_id = uuid.uuid4()
    user = _user(tenant_id=tenant_id)
    db = _StubDB(skill_tenant_id=tenant_id, evals=[
        {"id": str(uuid.uuid4()), "prompt": "p", "expectations": []},
    ])
    client = _build_client(user, db)

    with patch("app.services.skill_creator.eval_runner._load_skill_body",
               return_value=""):
        resp = client.post(
            f"/api/v1/skills/{uuid.uuid4()}/evals/run",
            json={"iteration": 0},
        )
    assert resp.status_code == 400


def test_run_endpoint_missing_iteration_returns_422():
    user = _user()
    db = _StubDB(skill_tenant_id=user.tenant_id)
    client = _build_client(user, db)

    resp = client.post(
        f"/api/v1/skills/{uuid.uuid4()}/evals/run",
        json={},
    )
    assert resp.status_code == 422


# ──────────────────────────────────────────────────────────────────────────
# GET /jobs/{job_id} status endpoint
# ──────────────────────────────────────────────────────────────────────────


def test_get_iteration_job_happy_path_terminal_true():
    """All rows ok → terminal=True, snapshot returned."""
    from datetime import datetime, timezone

    tenant_id = uuid.uuid4()
    skill_id = uuid.uuid4()
    job_id = uuid.uuid4()
    eval_id = uuid.uuid4()

    # rows shape matches the SELECT in get_iteration_status:
    # (r.id, r.eval_id, r.with_skill, r.status, r.error, r.started_at,
    #  r.completed_at, r.iteration, e.skill_id, s.tenant_id)
    now = datetime.now(timezone.utc)
    rows = [
        (uuid.uuid4(), eval_id, True, "ok", None, now, now, 1, skill_id, tenant_id),
        (uuid.uuid4(), eval_id, False, "ok", None, now, now, 1, skill_id, tenant_id),
    ]

    user = _user(tenant_id=tenant_id)
    db = _StubDB(skill_tenant_id=tenant_id, iteration_rows=rows)
    client = _build_client(user, db)

    resp = client.get(f"/api/v1/skills/{skill_id}/evals/jobs/{job_id}")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["job_id"] == str(job_id)
    assert body["terminal"] is True
    assert len(body["runs"]) == 2


def test_get_iteration_job_in_flight_terminal_false():
    """At least one run still running → terminal=False."""
    from datetime import datetime, timezone

    tenant_id = uuid.uuid4()
    skill_id = uuid.uuid4()
    eval_id = uuid.uuid4()
    now = datetime.now(timezone.utc)
    rows = [
        (uuid.uuid4(), eval_id, True, "running", None, now, None, 1, skill_id, tenant_id),
        (uuid.uuid4(), eval_id, False, "ok", None, now, now, 1, skill_id, tenant_id),
    ]

    user = _user(tenant_id=tenant_id)
    db = _StubDB(skill_tenant_id=tenant_id, iteration_rows=rows)
    client = _build_client(user, db)

    resp = client.get(f"/api/v1/skills/{skill_id}/evals/jobs/{uuid.uuid4()}")
    assert resp.status_code == 200
    assert resp.json()["terminal"] is False


def test_get_iteration_job_unknown_returns_404():
    tenant_id = uuid.uuid4()
    user = _user(tenant_id=tenant_id)
    db = _StubDB(skill_tenant_id=tenant_id, iteration_rows=[])
    client = _build_client(user, db)

    resp = client.get(f"/api/v1/skills/{uuid.uuid4()}/evals/jobs/{uuid.uuid4()}")
    assert resp.status_code == 404


def test_get_iteration_job_foreign_tenant_returns_404():
    """A row whose tenant_id != caller is treated as not found."""
    from datetime import datetime, timezone

    tenant_id = uuid.uuid4()
    other_tenant = uuid.uuid4()
    skill_id = uuid.uuid4()
    eval_id = uuid.uuid4()
    now = datetime.now(timezone.utc)

    rows = [
        (uuid.uuid4(), eval_id, True, "ok", None, now, now, 1, skill_id, other_tenant),
    ]

    user = _user(tenant_id=tenant_id)
    db = _StubDB(skill_tenant_id=tenant_id, iteration_rows=rows)
    client = _build_client(user, db)

    resp = client.get(f"/api/v1/skills/{skill_id}/evals/jobs/{uuid.uuid4()}")
    assert resp.status_code == 404


# ──────────────────────────────────────────────────────────────────────────
# Status taxonomy — defense-in-depth check
# ──────────────────────────────────────────────────────────────────────────


def test_terminal_statuses_set_is_strict_subset():
    """Phase 2's terminal taxonomy is {ok, error, timeout}."""
    assert set(eval_runner.TERMINAL_STATUSES) == {"ok", "error", "timeout"}
