"""Tests for /fleet/snapshot — Luna OS Podium boot aggregator."""
import os
import uuid

os.environ.setdefault("TESTING", "True")

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.api.deps import get_db
from app.db.base import Base
from app.db.session import SessionLocal, engine


def _override_get_db():
    try:
        db = SessionLocal()
        yield db
    finally:
        db.close()


app.dependency_overrides[get_db] = _override_get_db
client = TestClient(app)


@pytest.fixture(name="db_session")
def db_session_fixture():
    Base.metadata.create_all(bind=engine)
    yield SessionLocal()
    Base.metadata.drop_all(bind=engine)


@pytest.fixture(name="auth_headers")
def auth_headers_fixture(db_session):
    email = f"podium-{uuid.uuid4().hex[:8]}@example.com"
    pw = "testpassword"
    client.post(
        "/api/v1/auth/register",
        json={
            "user_in": {"email": email, "password": pw, "full_name": "Podium User"},
            "tenant_in": {"name": "Podium Tenant"},
        },
    )
    resp = client.post(
        "/api/v1/auth/login",
        data={"username": email, "password": pw},
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    return {"Authorization": f"Bearer {resp.json()['access_token']}"}


def test_snapshot_empty_tenant_returns_skeleton(db_session, auth_headers):
    """A brand-new tenant should still produce a well-formed snapshot."""
    r = client.get("/api/v1/fleet/snapshot", headers=auth_headers)
    assert r.status_code == 200
    body = r.json()
    assert "captured_at" in body
    assert isinstance(body["agents"], list)
    assert isinstance(body["groups"], list)
    assert isinstance(body["active_collaborations"], list)
    assert isinstance(body["notifications"], list)
    assert isinstance(body["commitments"], list)


def test_snapshot_rejects_unauthenticated(db_session):
    r = client.get("/api/v1/fleet/snapshot")
    assert r.status_code in (401, 403)


def test_snapshot_includes_production_agents(db_session, auth_headers):
    """A production agent should land on the podium; a draft should not."""
    me = client.get("/api/v1/users/me", headers=auth_headers).json()
    tenant_id = uuid.UUID(me["tenant_id"]) if "tenant_id" in me else uuid.UUID(me["tenant"]["id"])

    from app.models.agent import Agent
    db_session.add_all([
        Agent(
            id=uuid.uuid4(),
            name="Cardiac Analyst",
            tenant_id=tenant_id,
            status="production",
            role="specialist",
        ),
        Agent(
            id=uuid.uuid4(),
            name="Draft Agent",
            tenant_id=tenant_id,
            status="draft",
        ),
    ])
    db_session.commit()

    body = client.get("/api/v1/fleet/snapshot", headers=auth_headers).json()
    names = [a["name"] for a in body["agents"]]
    assert "Cardiac Analyst" in names
    assert "Draft Agent" not in names
