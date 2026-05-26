"""T4.4e — internal `POST /api/v1/skills/library/install-learned` endpoint.

Persists a Luna Learn draft into the tenant skills library. Error
contract is locked here because mcp-server's ``install_skill`` retry
loop (T2.6) depends on the 409 → suffix-with-vN behavior:

  * 200 — installed cleanly, returns {skill_id, slug, path}
  * 409 — slug conflict (DB unique-violation OR on-disk dir exists)
  * 422 — draft frontmatter could not be parsed
  * 500 — FS write failed AFTER DB row reserved (DB row rolled back)

The DB session is stubbed (MagicMock) so the tests run without
Postgres; the FS-rollback test is the most important one because it's
the spec §1.7 "No-TOCTOU" invariant.
"""
from __future__ import annotations

import os
os.environ["TESTING"] = "True"

import shutil
import tempfile
import uuid
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


def test_install_router_imports_clean():
    from app.api.v1 import routes  # noqa: F401
    from app.api.v1 import skills_new

    paths = {r.path for r in skills_new.router.routes}
    assert "/install-learned" in paths


_VALID_MD = """---
name: cardio-report-generator
engine: markdown
script_path: prompt.md
description: Generate cardiac-event reports from raw ECG data.
---

## Description
Generate cardiac-event reports from raw ECG data.
"""


@pytest.fixture
def tenant_id():
    return str(uuid.uuid4())


@pytest.fixture
def isolated_skills_dir(monkeypatch):
    """Redirect SkillManager's tenant dir into a tmpdir per test so
    parallel runs don't collide and we can assert on FS state."""
    tmp = Path(tempfile.mkdtemp(prefix="install-learned-"))
    from app.services.skill_manager import skill_manager

    def fake_tenant_dir(tenant_id: str) -> Path:
        return tmp / "_tenant" / tenant_id

    monkeypatch.setattr(skill_manager, "_tenant_skills_dir", fake_tenant_dir)
    yield tmp
    shutil.rmtree(tmp, ignore_errors=True)


def _build_client(monkeypatch, db_stub):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from app.api import deps as api_deps
    from app.core.config import settings
    from app.api.v1.skills_new import router

    monkeypatch.setattr(settings, "API_INTERNAL_KEY", "test-key", raising=False)

    app = FastAPI()
    app.include_router(router, prefix="/api/v1/skills")
    app.dependency_overrides[api_deps.get_db] = lambda: db_stub
    return TestClient(app)


def _stub_db():
    """Return a MagicMock session that emulates SQLAlchemy add/flush/commit.

    On add, we capture the model object and stamp a UUID id (mirrors the
    default in models.skill.Skill). flush() and commit() are no-ops by
    default; tests can monkeypatch to raise for failure paths.
    """
    db = MagicMock()
    captured = []

    def add(obj):
        if getattr(obj, "id", None) is None:
            obj.id = uuid.uuid4()
        captured.append(obj)

    db.add.side_effect = add
    db.flush.return_value = None
    db.commit.return_value = None
    db.rollback.return_value = None
    db._captured = captured
    return db


def test_install_requires_internal_key(monkeypatch, tenant_id, isolated_skills_dir):
    db = _stub_db()
    client = _build_client(monkeypatch, db)
    r = client.post(
        "/api/v1/skills/install-learned",
        json={"skill_md": _VALID_MD, "slug": "test-slug", "tenant_id": tenant_id},
    )
    assert r.status_code == 401


def test_install_rejects_bad_frontmatter(monkeypatch, tenant_id, isolated_skills_dir):
    db = _stub_db()
    client = _build_client(monkeypatch, db)
    r = client.post(
        "/api/v1/skills/install-learned",
        json={"skill_md": "no frontmatter", "slug": "x", "tenant_id": tenant_id},
        headers={"X-Internal-Key": "test-key"},
    )
    assert r.status_code == 422
    assert "frontmatter" in r.json()["detail"].lower() or "parse" in r.json()["detail"].lower()


def test_install_rejects_bad_tenant_uuid(monkeypatch, isolated_skills_dir):
    db = _stub_db()
    client = _build_client(monkeypatch, db)
    r = client.post(
        "/api/v1/skills/install-learned",
        json={"skill_md": _VALID_MD, "slug": "x", "tenant_id": "not-a-uuid"},
        headers={"X-Internal-Key": "test-key"},
    )
    assert r.status_code == 422
    assert "uuid" in r.json()["detail"].lower()


def test_install_success_writes_db_row_and_fs(monkeypatch, tenant_id, isolated_skills_dir):
    db = _stub_db()
    # No-op the revision audit so tests don't depend on library_revision table.
    monkeypatch.setattr(
        "app.services.library_revisions.record_revision",
        lambda *a, **kw: None,
    )
    client = _build_client(monkeypatch, db)
    r = client.post(
        "/api/v1/skills/install-learned",
        json={
            "skill_md": _VALID_MD,
            "slug": "cardio-report",
            "tenant_id": tenant_id,
            "actor_user_id": str(uuid.uuid4()),
            "reason": "learned from https://example.com/foo",
        },
        headers={"X-Internal-Key": "test-key"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["slug"] == "cardio-report"
    assert "skill_id" in body
    assert body["path"].endswith("skill.md")

    # FS side effect: the skill.md is written, body matches what we sent.
    written = Path(body["path"])
    assert written.exists()
    assert written.read_text(encoding="utf-8") == _VALID_MD

    # DB side effect: one Skill row added + committed.
    assert len(db._captured) == 1
    assert db.commit.called


def test_install_409_on_existing_fs_dir(monkeypatch, tenant_id, isolated_skills_dir):
    """Slug conflict in the FS path → 409 so the caller's retry loop
    can re-attempt with a -vN suffix (T2.6 install_skill contract)."""
    # Pre-create the slug dir to simulate an existing skill.
    tenant_dir = isolated_skills_dir / "_tenant" / tenant_id
    (tenant_dir / "already-taken").mkdir(parents=True)

    db = _stub_db()
    client = _build_client(monkeypatch, db)
    r = client.post(
        "/api/v1/skills/install-learned",
        json={"skill_md": _VALID_MD, "slug": "already-taken", "tenant_id": tenant_id},
        headers={"X-Internal-Key": "test-key"},
    )
    assert r.status_code == 409, r.text
    assert "already-taken" in r.json()["detail"]
    # No DB row should have been written (we abort before db.add).
    assert len(db._captured) == 0


def test_install_409_on_db_unique_violation(monkeypatch, tenant_id, isolated_skills_dir):
    """DB unique-constraint violation also surfaces as 409."""
    db = _stub_db()
    db.flush.side_effect = Exception("duplicate key value violates unique constraint")
    client = _build_client(monkeypatch, db)
    r = client.post(
        "/api/v1/skills/install-learned",
        json={"skill_md": _VALID_MD, "slug": "fresh-slug", "tenant_id": tenant_id},
        headers={"X-Internal-Key": "test-key"},
    )
    assert r.status_code == 409
    assert db.rollback.called


def test_install_500_on_fs_write_failure_rolls_back_db(
    monkeypatch, tenant_id, isolated_skills_dir
):
    """Spec §1.7 No-TOCTOU: if FS write fails after DB row reserved,
    the DB row MUST be rolled back so the retry sees a clean slot."""
    monkeypatch.setattr(
        "app.services.library_revisions.record_revision",
        lambda *a, **kw: None,
    )
    db = _stub_db()

    # Patch the write_text call on the destination skill.md to blow up.
    real_write = Path.write_text
    fail_path = "fs-fail-slug"

    def maybe_fail(self, *args, **kwargs):
        if self.name == "skill.md" and fail_path in str(self):
            raise OSError("disk full")
        return real_write(self, *args, **kwargs)

    monkeypatch.setattr(Path, "write_text", maybe_fail)

    client = _build_client(monkeypatch, db)
    r = client.post(
        "/api/v1/skills/install-learned",
        json={"skill_md": _VALID_MD, "slug": fail_path, "tenant_id": tenant_id},
        headers={"X-Internal-Key": "test-key"},
    )
    assert r.status_code == 500, r.text
    assert "disk full" in r.json()["detail"] or "FS" in r.json()["detail"]
    # The DB row that was added must have been rolled back.
    assert db.rollback.called
