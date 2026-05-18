"""Tests for ``POST /api/v1/skills/{skill_id}/evals/grade``.

Uses FastAPI TestClient with ``get_current_user`` and ``get_db`` overridden
so we don't need a live Postgres backend. The DB shape is the only thing the
endpoint reads through ORM-level queries — we stub the SQL execute path with
a fake that recognizes the three queries the endpoint issues.
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


# ──────────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────────


def _user(tenant_id: Optional[uuid.UUID] = None) -> User:
    return User(
        id=uuid.uuid4(),
        email=f"user-{uuid.uuid4().hex[:6]}@test.com",
        tenant_id=tenant_id or uuid.uuid4(),
        is_active=True,
        is_superuser=False,
        hashed_password="x",
    )


class _StubResult:
    """Minimal stand-in for SQLAlchemy's Result.fetchone()."""

    def __init__(self, row: Optional[tuple]):
        self._row = row

    def fetchone(self):
        return self._row


class _StubDB:
    """Recognizes the three SQL statements the endpoint issues.

    Statement matchers are substring-based: the endpoint's queries are stable
    enough that a substring like ``"FROM skills WHERE"`` is unambiguous.
    """

    def __init__(
        self,
        skill_tenant_id: Optional[uuid.UUID] = None,
        run_row: Optional[tuple] = None,
    ):
        self._skill_tenant_id = skill_tenant_id
        self._run_row = run_row
        self.executed: List[Dict[str, Any]] = []
        self.committed = False
        self.rolled_back = False

    # SQLAlchemy 1.4-style execute returns a Result
    def execute(self, statement, params=None):
        sql = str(statement)
        self.executed.append({"sql": sql, "params": params or {}})
        if "FROM skills WHERE" in sql:
            if self._skill_tenant_id is None:
                return _StubResult(None)
            return _StubResult((str(self._skill_tenant_id),))
        if "FROM skill_eval_runs r" in sql:
            return _StubResult(self._run_row)
        if "INSERT INTO skill_eval_grading" in sql:
            return _StubResult(None)
        return _StubResult(None)

    def commit(self):
        self.committed = True

    def rollback(self):
        self.rolled_back = True


def _build_client(user: User, db: _StubDB) -> TestClient:
    app = FastAPI()
    app.dependency_overrides[deps.get_current_user] = lambda: user

    def _stub_db():
        yield db

    app.dependency_overrides[deps.get_db] = _stub_db
    app.include_router(skill_evals_module.router, prefix="/api/v1/skills")
    return TestClient(app, raise_server_exceptions=False)


def _verdicts_json(*entries):
    return json.dumps({"expectations": list(entries)})


# ──────────────────────────────────────────────────────────────────────────
# Happy path
# ──────────────────────────────────────────────────────────────────────────


def test_grade_happy_path_returns_grading_payload():
    tenant_id = uuid.uuid4()
    skill_id = uuid.uuid4()
    run_id = uuid.uuid4()
    eval_id = uuid.uuid4()

    run_row = (
        str(run_id),                       # r.id
        str(eval_id),                      # r.eval_id
        "Hello! It is rainy today.",       # r.transcript
        None,                              # r.outputs
        [                                  # e.expectations (JSONB)
            {"id": "e1", "description": "Has greeting"},
            {"id": "e2", "description": "Mentions weather"},
        ],
        str(skill_id),                     # e.skill_id
    )

    user = _user(tenant_id=tenant_id)
    db = _StubDB(skill_tenant_id=tenant_id, run_row=run_row)
    client = _build_client(user, db)

    fake = _verdicts_json(
        {"id": "e1", "passed": True, "reasoning": "Says hello."},
        {"id": "e2", "passed": True, "reasoning": "Says rainy."},
    )

    with patch("app.services.local_inference.generate_sync", return_value=fake):
        resp = client.post(
            f"/api/v1/skills/{skill_id}/evals/grade",
            json={"run_id": str(run_id)},
        )

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["version"] == 1
    assert body["eval_id"] == str(eval_id)
    assert body["run_id"] == str(run_id)
    assert body["passed"] is True
    assert body["score"] == 1.0
    assert len(body["expectations"]) == 2
    assert body["graded_at"].endswith("Z")

    # Verify the grading row was persisted.
    insert_sqls = [r for r in db.executed if "INSERT INTO skill_eval_grading" in r["sql"]]
    assert len(insert_sqls) == 1
    assert db.committed is True


def test_grade_partial_pass_returns_correct_score():
    tenant_id = uuid.uuid4()
    skill_id = uuid.uuid4()
    run_id = uuid.uuid4()
    eval_id = uuid.uuid4()

    run_row = (
        str(run_id), str(eval_id), "Hello!", None,
        [
            {"id": "e1", "description": "Has greeting"},
            {"id": "e2", "description": "Mentions weather"},
        ],
        str(skill_id),
    )

    user = _user(tenant_id=tenant_id)
    db = _StubDB(skill_tenant_id=tenant_id, run_row=run_row)
    client = _build_client(user, db)

    fake = _verdicts_json(
        {"id": "e1", "passed": True, "reasoning": "Says hello."},
        {"id": "e2", "passed": False, "reasoning": "No weather reference."},
    )

    with patch("app.services.local_inference.generate_sync", return_value=fake):
        resp = client.post(
            f"/api/v1/skills/{skill_id}/evals/grade",
            json={"run_id": str(run_id)},
        )

    assert resp.status_code == 200
    body = resp.json()
    assert body["passed"] is False
    assert body["score"] == 0.5


# ──────────────────────────────────────────────────────────────────────────
# Auth / ownership
# ──────────────────────────────────────────────────────────────────────────


def test_grade_foreign_tenant_skill_returns_404():
    """User in tenant A asks for a skill owned by tenant B → 404 (not 403)."""
    user = _user()
    foreign_tenant = uuid.uuid4()
    db = _StubDB(skill_tenant_id=foreign_tenant)
    client = _build_client(user, db)

    resp = client.post(
        f"/api/v1/skills/{uuid.uuid4()}/evals/grade",
        json={"run_id": str(uuid.uuid4())},
    )
    assert resp.status_code == 404


def test_grade_unknown_skill_returns_404():
    user = _user()
    db = _StubDB(skill_tenant_id=None)  # query returns no row
    client = _build_client(user, db)

    resp = client.post(
        f"/api/v1/skills/{uuid.uuid4()}/evals/grade",
        json={"run_id": str(uuid.uuid4())},
    )
    assert resp.status_code == 404


def test_grade_run_belongs_to_different_skill_returns_404():
    tenant_id = uuid.uuid4()
    skill_id = uuid.uuid4()
    other_skill_id = uuid.uuid4()
    run_id = uuid.uuid4()
    eval_id = uuid.uuid4()

    # The run row reports a skill_id that doesn't match the URL skill_id.
    run_row = (
        str(run_id), str(eval_id), "transcript", None,
        [{"id": "e1", "description": "x"}],
        str(other_skill_id),
    )

    user = _user(tenant_id=tenant_id)
    db = _StubDB(skill_tenant_id=tenant_id, run_row=run_row)
    client = _build_client(user, db)

    resp = client.post(
        f"/api/v1/skills/{skill_id}/evals/grade",
        json={"run_id": str(run_id)},
    )
    assert resp.status_code == 404


def test_grade_unknown_run_returns_404():
    tenant_id = uuid.uuid4()
    user = _user(tenant_id=tenant_id)
    db = _StubDB(skill_tenant_id=tenant_id, run_row=None)
    client = _build_client(user, db)

    resp = client.post(
        f"/api/v1/skills/{uuid.uuid4()}/evals/grade",
        json={"run_id": str(uuid.uuid4())},
    )
    assert resp.status_code == 404


# ──────────────────────────────────────────────────────────────────────────
# Request validation
# ──────────────────────────────────────────────────────────────────────────


def test_grade_missing_run_id_returns_422():
    user = _user()
    db = _StubDB(skill_tenant_id=user.tenant_id)
    client = _build_client(user, db)

    resp = client.post(
        f"/api/v1/skills/{uuid.uuid4()}/evals/grade",
        json={},
    )
    assert resp.status_code == 422


def test_grade_non_uuid_run_id_returns_422():
    user = _user()
    db = _StubDB(skill_tenant_id=user.tenant_id)
    client = _build_client(user, db)

    resp = client.post(
        f"/api/v1/skills/{uuid.uuid4()}/evals/grade",
        json={"run_id": "not-a-uuid"},
    )
    assert resp.status_code == 422


# ──────────────────────────────────────────────────────────────────────────
# Grader outage
# ──────────────────────────────────────────────────────────────────────────


def test_grade_grader_outage_returns_503():
    tenant_id = uuid.uuid4()
    skill_id = uuid.uuid4()
    run_id = uuid.uuid4()
    eval_id = uuid.uuid4()

    run_row = (
        str(run_id), str(eval_id), "Hello!", None,
        [{"id": "e1", "description": "Has greeting"}],
        str(skill_id),
    )

    user = _user(tenant_id=tenant_id)
    db = _StubDB(skill_tenant_id=tenant_id, run_row=run_row)
    client = _build_client(user, db)

    with patch("app.services.local_inference.generate_sync", return_value=None):
        resp = client.post(
            f"/api/v1/skills/{skill_id}/evals/grade",
            json={"run_id": str(run_id)},
        )

    assert resp.status_code == 503
    # No grading row persisted on outage.
    insert_sqls = [r for r in db.executed if "INSERT INTO skill_eval_grading" in r["sql"]]
    assert insert_sqls == []


# ──────────────────────────────────────────────────────────────────────────
# Persistence failure
# ──────────────────────────────────────────────────────────────────────────


def test_grade_persist_failure_returns_500_and_rolls_back():
    """If the commit raises, the endpoint must NOT return a 200 — the
    contract is side-effect-on-success only. Rollback + 500 is the right
    outcome so the caller can retry and any half-written transaction state
    is discarded.
    """
    tenant_id = uuid.uuid4()
    skill_id = uuid.uuid4()
    run_id = uuid.uuid4()
    eval_id = uuid.uuid4()

    run_row = (
        str(run_id), str(eval_id), "Hello!", None,
        [{"id": "e1", "description": "Has greeting"}],
        str(skill_id),
    )

    user = _user(tenant_id=tenant_id)
    db = _StubDB(skill_tenant_id=tenant_id, run_row=run_row)

    # Make commit blow up — simulates a DB error mid-flush.
    def _boom():
        raise RuntimeError("simulated DB error on commit")

    db.commit = _boom  # type: ignore[assignment]
    client = _build_client(user, db)

    fake = _verdicts_json(
        {"id": "e1", "passed": True, "reasoning": "Says hello."},
    )

    with patch("app.services.local_inference.generate_sync", return_value=fake):
        resp = client.post(
            f"/api/v1/skills/{skill_id}/evals/grade",
            json={"run_id": str(run_id)},
        )

    assert resp.status_code == 500
    assert db.rolled_back is True
    body = resp.json()
    assert "persist" in body.get("detail", "").lower()
